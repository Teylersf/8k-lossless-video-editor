"""Loopback-only persisted editing API. GPL-2.0-only; port 8770."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import copy
import importlib
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile as StarletteUploadFile

from media_library import MediaLibrary, MediaBusyError, MAX_UPLOAD, atomic_json, file_hash, read_json, valid_id

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'engine'))
APP_NAME = '8K Lossless Video Editor'
VERSION = '0.1.0-local'
ORIGINS = {'http://127.0.0.1:8770', 'http://localhost:8770', 'http://[::1]:8770',
           'http://127.0.0.1:5173', 'http://localhost:5173'}
ACTIVE = {'queued', 'running', 'cancelling'}
SERVER = None


def editor_pipeline():
    return importlib.import_module('engine.editor_pipeline')


def project_model():
    return importlib.import_module('engine.project_model')


def referenced_media(project):
    return {clip['media_id'] for track in project['tracks'] if not track.get('muted') for clip in track['clips'] if clip.get('media_id')}


class BodyTooLarge(HTTPException):
    def __init__(self):
        super().__init__(413, 'Request exceeds the local size limit')


class RequestBounds:
    """Apply streaming body limits before multipart parsing/spooling."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in ('POST', 'PUT', 'PATCH'):
            return await self.app(scope, receive, send)
        limit = MAX_UPLOAD + 16 * 1024**2 if scope['path'] in ('/api/media', '/api/media/sequence') else 2 * 1024**2
        headers = dict(scope.get('headers', []))
        try:
            length = int(headers.get(b'content-length', b'0'))
        except ValueError:
            length = limit + 1
        if length < 0 or length > limit:
            return await JSONResponse({'detail': 'Request exceeds the local size limit'}, status_code=413)(scope, receive, send)
        count = 0
        started = False
        async def bounded_receive():
            nonlocal count
            value = await receive()
            if value['type'] == 'http.request':
                count += len(value.get('body', b''))
                if count > limit:
                    raise BodyTooLarge()
            return value
        async def tracked_send(value):
            nonlocal started
            if value['type'] == 'http.response.start':
                started = True
            await send(value)
        try:
            await self.app(scope, bounded_receive, tracked_send)
        except BodyTooLarge:
            if started:
                raise
            await JSONResponse({'detail': 'Request exceeds the local size limit'}, status_code=413)(scope, receive, send)


class EditorService:
    def __init__(self, store, pipeline=None, model=None, executor=None):
        self.store = Path(store).resolve()
        self.store.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in ('media', 'projects', 'jobs', 'frames'):
            if (self.store / name).is_symlink():
                raise ValueError('Editor storage subdirectories must not be symbolic links')
        self.library = MediaLibrary(self.store / 'media')
        for name in ('projects', 'jobs', 'frames'):
            (self.store / name).mkdir(exist_ok=True, mode=0o700)
        self.pipeline_override, self.model_override = pipeline, model
        self.executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix='editor-render')
        self.lock, self.renderer = threading.RLock(), threading.Lock()
        self.jobs, self.cancel = {}, {}
        self.preview_cancel = set()
        self.closing = False
        for path in (self.store / 'jobs').glob('*/job.json'):
            try:
                job = read_json(path, 16 * 1024**2)
                if not valid_id(job['id']) or path.parent.name != job['id'] or path.parent.is_symlink():
                    continue
                if job['status'] in ACTIVE:
                    job.update(status='failed', message='Service restarted during this render. Start a new export.', error='Interrupted by service restart')
                    atomic_json(path, job)
                if job['status'] not in ACTIVE | {'completed', 'failed', 'cancelled'}:
                    continue
                self.jobs[job['id']] = job
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def pipeline(self):
        return self.pipeline_override or editor_pipeline()

    def model(self):
        return self.model_override or project_model()

    def clean(self, value):
        """Remove private filesystem handles from public result/error metadata."""
        if isinstance(value, dict):
            return {key: self.clean(item) for key, item in value.items() if not key.startswith('_') and key not in {'output', 'path', 'source', 'source_path', 'work', 'input_path'}}
        if isinstance(value, (list, tuple)):
            return [self.clean(item) for item in value]
        if isinstance(value, str):
            return value.replace(str(self.store), '<local data>').replace(self.store.as_posix(), '<local data>')
        return value

    def public_job(self, job):
        return self.clean({key: value for key, value in job.items() if not key.startswith('_')})

    def update(self, identifier, **values):
        with self.lock:
            job = self.jobs[identifier]
            job.update(values)
            atomic_json(self.store / 'jobs' / identifier / 'job.json', job)
            return self.public_job(job)

    def validate(self, project, *, media=False):
        if not isinstance(project, dict):
            raise ValueError('Project must be an object')
        normalized = self.model().validate_project(copy.deepcopy(project))
        media_map = self.library.snapshot(referenced_media(normalized)) if media else None
        if media:
            normalized = self.model().validate_project(normalized, media_map)
        return normalized, media_map

    def get_project(self, identifier):
        if not valid_id(identifier):
            raise KeyError('Project not found')
        folder = self.store / 'projects' / identifier
        if folder.is_symlink() or folder.resolve().parent != self.store / 'projects':
            raise KeyError('Project not found')
        value = read_json(folder / 'project.json')
        if value['id'] != identifier:
            raise KeyError('Project identity mismatch')
        return value

    def save_project(self, value):
        project, _ = self.validate(value)
        identifier = project.get('id')
        if identifier:
            try:
                identifier = uuid.UUID(identifier).hex
            except (ValueError, TypeError, AttributeError) as error:
                raise ValueError('Project ID must be a UUID returned by the editor') from error
        else:
            identifier = uuid.uuid4().hex
        project['id'] = identifier
        with self.lock:
            folder = self.store / 'projects' / identifier
            if folder.is_symlink():
                raise ValueError('Invalid project folder')
            folder.mkdir(exist_ok=True, mode=0o700)
            result = {'id': identifier, 'name': project['name'], 'project': project, 'updated_at': time.time()}
            atomic_json(folder / 'project.json', result)
        return result

    def start_job(self, project, mode, preview):
        project, media_map = self.validate(project, media=True)
        if self.model().project_duration(project) <= 0:
            raise ValueError('Add a clip before rendering the timeline')
        selected = next((item for item in self.pipeline().capabilities().get('modes', []) if item.get('id') == mode), None)
        if selected is None:
            raise ValueError('Unknown export profile; choose an installed capability')
        if not selected.get('available', False):
            raise ValueError(selected.get('reason') or 'This export profile is unavailable')
        with self.lock:
            if self.closing:
                raise MediaBusyError('Editor is stopping')
            if sum(job['status'] in ACTIVE for job in self.jobs.values()) >= 4:
                raise MediaBusyError('Four renders are already active or queued')
            identifier = uuid.uuid4().hex
            folder = self.store / 'jobs' / identifier
            folder.mkdir(mode=0o700)
            suffix = '.mp4' if preview else selected.get('extension') or ('.mov' if mode == 'prores' else '.mkv')
            if mode == 'copy' and not preview:
                first_id = next((clip['media_id'] for track in project['tracks'] if not track['muted'] for clip in track['clips'] if clip.get('media_id')), None)
                first = media_map.get(first_id)
                if first:
                    suffix = Path(first['path']).suffix.lower()
            if not re.fullmatch(r'\.[a-z0-9]{1,10}', suffix):
                raise ValueError('Export profile has no supported output container')
            stem = re.sub(r'[^a-zA-Z0-9_-]+', '-', project['name']).strip('-')[:60] or 'timeline'
            name = stem + ('-preview' if preview else '-edited') + suffix
            job = {'id': identifier, 'status': 'queued', 'progress': 0, 'message': 'Queued for the local renderer',
                   'mode': mode, 'preview': preview, 'error': None, 'output_url': None, 'output_name': None,
                   'created_at': time.time(), '_project': project, '_media_map': media_map, '_name': name}
            self.jobs[identifier], self.cancel[identifier] = job, threading.Event()
            atomic_json(folder / 'job.json', job)
            self.executor.submit(self.execute_job, identifier)
            return self.public_job(job)

    def execute_job(self, identifier):
        event = self.cancel[identifier]
        acquired = False
        try:
            while not event.is_set():
                if self.renderer.acquire(timeout=.1):
                    acquired = True
                    break
            if event.is_set():
                self.update(identifier, status='cancelled', message='Cancelled before rendering')
                return
            with self.lock:
                if event.is_set():
                    self.update(identifier, status='cancelled', message='Cancelled before rendering')
                    return
                job = copy.deepcopy(self.jobs[identifier])
                self.update(identifier, status='running', message='Rendering and verifying the timeline')
            output = self.store / 'jobs' / identifier / job['_name']
            def progress(value, text):
                value = float(value)
                if math.isfinite(value):
                    self.update(identifier, progress=max(0, min(.99, value)), message=str(text)[:1000])
            result = self.pipeline().render_project(job['_project'], job['_media_map'], output, mode=job['mode'],
                                                    progress_callback=progress, cancel_event=event, preview=job['preview'])
            if output.is_symlink() or not output.is_file() or not output.stat().st_size:
                raise ValueError('Renderer did not publish a verified output')
            # Publication won a late cancellation race: retain and expose the verified file.
            self.update(identifier, status='completed', progress=1, message='Export verified and ready', error=None,
                        output_url=f'/api/jobs/{identifier}/download', output_name=job['_name'], result=self.clean(result),
                        _output_sha256=file_hash(output), _output_bytes=output.stat().st_size)
        except Exception as error:
            self.update(identifier, status='cancelled' if event.is_set() else 'failed',
                        message='Render cancelled' if event.is_set() else 'Render failed', error=str(self.clean(str(error)))[:4000])
        finally:
            if acquired:
                self.renderer.release()

    def cancel_job(self, identifier):
        with self.lock:
            if identifier not in self.jobs:
                raise KeyError('Render not found')
            job = self.jobs[identifier]
            if job['status'] in ACTIVE:
                self.cancel[identifier].set()
                queued = job['status'] == 'queued'
                return self.update(identifier, status='cancelled' if queued else 'cancelling',
                                   message='Cancelled before rendering' if queued else 'Stopping this owned render')
            return self.public_job(job)

    def request_stop(self):
        with self.lock:
            self.closing = True
            for event in self.preview_cancel:
                event.set()
            for identifier, event in self.cancel.items():
                event.set()
                job = self.jobs[identifier]
                if job['status'] == 'queued':
                    self.update(identifier, status='cancelled', message='Cancelled while editor stopped')
                elif job['status'] == 'running':
                    self.update(identifier, status='cancelling', message='Editor stopping this render')
        self.library.close()

    def close(self):
        self.request_stop()
        self.executor.shutdown(wait=True, cancel_futures=True)


class ProjectRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project: dict[str, Any]


class PreviewRequest(ProjectRequest):
    time: float = Field(default=0, ge=0, strict=True, allow_inf_nan=False)
    width: int = Field(default=1280, ge=64, le=1920, strict=True)


class JobRequest(ProjectRequest):
    mode: str = Field(default='lossless', strict=True, min_length=1, max_length=80)
    preview: bool = Field(default=False, strict=True)


def create_app(store=None, *, pipeline=None, model=None, executor=None):
    @asynccontextmanager
    async def lifespan(application):
        service = EditorService(store or os.environ.get('EDITOR_DATA_DIR') or ROOT / 'data', pipeline, model, executor)
        application.state.editor = service
        try:
            yield
        finally:
            await asyncio.to_thread(service.close)

    application = FastAPI(title=APP_NAME, version=VERSION, lifespan=lifespan)
    application.add_middleware(RequestBounds)
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', '[::1]', 'testserver'])
    application.add_middleware(CORSMiddleware, allow_origins=sorted(ORIGINS), allow_methods=['GET', 'POST'], allow_headers=['Content-Type', 'X-Editor-Client'])

    def service():
        return application.state.editor

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return JSONResponse({'detail': [{key: item[key] for key in ('loc', 'msg', 'type')} for item in error.errors()]}, status_code=422)

    @application.exception_handler(ValueError)
    async def invalid_value(request, error):
        return JSONResponse({'detail': service().clean(str(error))[:4000]}, status_code=422)

    @application.exception_handler(KeyError)
    async def missing_value(request, error):
        return JSONResponse({'detail': service().clean(str(error))[:1000]}, status_code=404)

    @application.exception_handler(MediaBusyError)
    async def busy_value(request, error):
        return JSONResponse({'detail': str(error)}, status_code=409)

    @application.middleware('http')
    async def local_requests(request: Request, call_next):
        origin = request.headers.get('origin')
        if origin and origin not in ORIGINS:
            return JSONResponse({'detail': 'Only the local editor origin is allowed'}, status_code=403)
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.headers.get('X-Editor-Client') != '8k-lossless-editor':
            return JSONResponse({'detail': 'Use the local editor client header'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @application.get('/api/status')
    def status():
        from engine.media_pipeline import FFMPEG, FFPROBE
        ffmpeg = bool(shutil.which(FFMPEG) and shutil.which(FFPROBE))
        try:
            caps = service().pipeline().capabilities()
            backend = caps.get('acceleration') or importlib.import_module('engine.runtime_backend').backend_info()
            from engine.format_catalog import capabilities as format_capabilities
            return {'app': APP_NAME, 'version': VERSION, 'ready': ffmpeg, **backend, 'ffmpeg': ffmpeg,
                    'capabilities': {**service().clean(caps), 'formats': format_capabilities()}, 'renderer_busy': service().renderer.locked()}
        except (ImportError, ValueError, RuntimeError, OSError) as error:
            return {'app': APP_NAME, 'version': VERSION, 'ready': False, 'device': None, 'gpu': 'Renderer unavailable',
                    'accelerated': False, 'reason': service().clean(str(error)), 'ffmpeg': ffmpeg, 'capabilities': {}}

    @application.get('/api/capabilities')
    def capabilities():
        from engine.format_catalog import capabilities as format_capabilities
        return {**service().clean(service().pipeline().capabilities()), 'formats': format_capabilities()}

    @application.get('/api/formats')
    def formats():
        from engine.format_catalog import capabilities as format_capabilities
        return format_capabilities()

    @application.get('/api/media')
    def list_media():
        return service().library.list()

    @application.post('/api/media')
    def import_media(file: UploadFile):
        try:
            return service().library.import_file(file.file, file.filename)
        finally:
            file.file.close()

    @application.post('/api/media/sequence')
    async def import_sequence(request: Request):
        async with request.form(max_files=10000, max_fields=4) as form:
            files = form.getlist('files')
            if not files or any(not isinstance(item, StarletteUploadFile) for item in files):
                raise ValueError('Provide image files under the multipart files key')
            if set(form.keys()) - {'files', 'fps'}:
                raise ValueError('Sequence import accepts only files and fps')
            fps = form.get('fps', '24/1')
            return await run_in_threadpool(service().library.import_sequence, files, fps)

    @application.post('/api/media/{identifier}/interpret')
    def interpret_media(identifier: str, settings: dict[str, Any]):
        return service().library.interpret(identifier, settings)

    @application.get('/api/media/{identifier}')
    def get_media(identifier: str):
        return service().library.get(identifier)

    @application.get('/api/media/{identifier}/waveform')
    def waveform(identifier: str):
        return service().library.get(identifier)['waveform']

    @application.get('/api/media/{identifier}/{asset}')
    def media_asset(identifier: str, asset: str):
        path, mime = service().library.asset(identifier, asset)
        return FileResponse(path, media_type=mime)

    @application.get('/api/projects')
    def list_projects():
        result = []
        for path in (service().store / 'projects').glob('*/project.json'):
            try:
                result.append(service().get_project(path.parent.name))
            except (KeyError, ValueError, OSError):
                continue
        return sorted(result, key=lambda item: item['updated_at'], reverse=True)

    @application.post('/api/projects')
    def save_project(request: ProjectRequest):
        return service().save_project(request.project)

    @application.get('/api/projects/{identifier}')
    def get_project(identifier: str):
        try:
            return service().get_project(identifier)
        except FileNotFoundError as error:
            raise HTTPException(404, 'Project not found') from error

    @application.post('/api/preview')
    def preview(request: PreviewRequest):
        state = service()
        project, media_map = state.validate(request.project, media=True)
        duration = state.model().project_duration(project)
        if duration <= 0 or request.time > duration:
            raise ValueError('Preview time is outside the project timeline')
        if not state.renderer.acquire(blocking=False):
            raise HTTPException(409, 'The renderer is processing an export; existing frames remain available')
        identifier = uuid.uuid4().hex
        output = state.store / 'frames' / (identifier + '.jpg')
        event = threading.Event()
        try:
            with state.lock:
                if state.closing:
                    raise HTTPException(409, 'Editor is stopping')
                state.preview_cancel.add(event)
            result = state.pipeline().render_frame(project, media_map, request.time, request.width, output, cancel_event=event)
            if output.is_symlink() or not output.is_file() or not output.stat().st_size:
                raise ValueError('Renderer did not publish a preview image')
            return {**state.clean(result), 'url': f'/api/frames/{identifier}.jpg'}
        except InterruptedError as error:
            raise HTTPException(409, 'The owned preview was cancelled') from error
        finally:
            with state.lock:
                state.preview_cancel.discard(event)
            state.renderer.release()

    @application.get('/api/frames/{name}')
    def frame_asset(name: str):
        if not re.fullmatch(r'[0-9a-f]{32}\.jpg', name):
            raise HTTPException(404, 'Preview not found')
        path = service().store / 'frames' / name
        if path.is_symlink() or not path.is_file() or path.resolve().parent != (service().store / 'frames').resolve():
            raise HTTPException(404, 'Preview not found')
        return FileResponse(path, media_type='image/jpeg')

    @application.post('/api/jobs')
    def start_job(request: JobRequest):
        return service().start_job(request.project, request.mode, request.preview)

    @application.get('/api/jobs/{identifier}')
    def job_status(identifier: str):
        with service().lock:
            if identifier not in service().jobs:
                raise HTTPException(404, 'Render not found')
            return service().public_job(service().jobs[identifier])

    @application.post('/api/jobs/{identifier}/cancel')
    def cancel_job(identifier: str):
        return service().cancel_job(identifier)

    @application.get('/api/jobs/{identifier}/download')
    def download(identifier: str):
        state = service()
        with state.lock:
            job = copy.deepcopy(state.jobs.get(identifier))
        if not job:
            raise HTTPException(404, 'Render not found')
        if job['status'] != 'completed' or not job.get('output_name'):
            raise HTTPException(409, 'A verified completed export is required')
        folder = state.store / 'jobs' / identifier
        name = job['output_name']
        if Path(name).name != name or folder.is_symlink():
            raise HTTPException(409, 'Invalid owned output identity')
        path = folder / name
        if path.is_symlink() or not path.is_file() or path.resolve().parent != folder.resolve() or path.stat().st_size != job.get('_output_bytes') or file_hash(path) != job.get('_output_sha256'):
            raise HTTPException(409, 'The verified output changed or is unavailable')
        return FileResponse(path, filename=name, media_type=mimetypes.guess_type(name)[0] or 'application/octet-stream')

    @application.post('/api/shutdown')
    def shutdown():
        service().request_stop()
        if SERVER is not None:
            SERVER.should_exit = True
        return {'stopping': True, 'app': APP_NAME}

    if (ROOT / 'app/dist').is_dir():
        application.mount('/', StaticFiles(directory=ROOT / 'app/dist', html=True), name='editor')
    else:
        @application.get('/')
        def build_first():
            return {'app': APP_NAME, 'message': 'Build app/dist, then restart the editor.'}
    return application


app = create_app()

if __name__ == '__main__':
    import uvicorn
    SERVER = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8770, log_level='info'))
    SERVER.run()
