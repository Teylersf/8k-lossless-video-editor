"""Real local API/storage tests with temporary imports; no persistent service.

The pipeline stub isolates deterministic API queue/cancellation races. Image,
audio and video ingest cases use the installed decoders and owned subprocess.
"""
from __future__ import annotations

from concurrent.futures import Future
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

import server
from engine import project_model
from media_library import file_hash, atomic_json, MediaLibrary, media_pipeline

HEADER = {'X-Editor-Client': '8k-lossless-editor'}


class PendingExecutor:
    def __init__(self):
        self.pending = []

    def submit(self, function, *args):
        future = Future()
        self.pending.append((function, args, future))
        return future

    def run(self, index=0):
        function, args, future = self.pending.pop(index)
        function(*args)
        future.set_result(None)

    def shutdown(self, **kwargs):
        for _, _, future in self.pending:
            future.cancel()
        self.pending.clear()


class Pipeline:
    def __init__(self):
        self.snapshots = []
        self.entered, self.release = threading.Event(), threading.Event()
        self.block = False
        self.frame_block = False
        self.frame_entered = threading.Event()

    def capabilities(self):
        return {'modes': [{'id': 'lossless', 'available': True}], 'acceleration': {'device': 'cpu', 'gpu': 'CPU', 'accelerated': False, 'reason': 'Test CPU'}, 'encoders': ['ffv1']}

    def render_frame(self, project, media_map, time_s, width, output, cancel_event=None):
        self.frame_entered.set()
        while self.frame_block:
            if cancel_event.wait(.01):
                raise InterruptedError('Preview cancelled')
        Image.new('RGB', (width, round(width * project['height'] / project['width'])), '#7852a1').save(output)
        return {'width': width, 'height': round(width * project['height'] / project['width']), 'time': time_s, 'render_ms': 1}

    def render_project(self, project, media_map, output, mode, progress_callback, cancel_event, preview):
        self.snapshots.append(copy.deepcopy(project))
        self.entered.set()
        while self.block and not self.release.wait(.01):
            if cancel_event.is_set():
                raise InterruptedError('Stopped this test renderer')
        if cancel_event.is_set():
            raise InterruptedError('Cancelled')
        progress_callback(.5, 'Test render')
        Path(output).write_bytes(b'verified-test-output')
        return {'output': str(output), 'codec': 'ffv1', 'preview': preview, 'width': project['width'], 'height': project['height'], 'warnings': []}


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='8k editor API ')
        self.store = Path(self.temp.name) / 'data'
        self.pipeline, self.executor = Pipeline(), PendingExecutor()
        self.app = server.create_app(self.store, pipeline=self.pipeline, model=project_model, executor=self.executor)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.client.__enter__()

    def tearDown(self):
        self.pipeline.release.set()
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    @property
    def state(self):
        return self.app.state.editor

    def image_bytes(self, size=(64, 48)):
        stream = io.BytesIO()
        Image.new('RGB', size, '#bbaaff').save(stream, format='PNG')
        return stream.getvalue()

    def upload(self, content=None, name='image.png'):
        content = self.image_bytes() if content is None else content
        response = self.client.post('/api/media', headers=HEADER, files={'file': (name, content)})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def project(self, media=None):
        media = media or self.upload()
        return {'schema_version': 1, 'name': 'Synthetic project', 'width': 64, 'height': 48, 'fps': '24/1',
                'tracks': [{'id': 'track1', 'name': 'Video', 'kind': 'video', 'muted': False, 'locked': False,
                            'clips': [{'id': 'clip1', 'media_id': media['id'], 'start': 0, 'in': 0, 'duration': .2}]}]}

    def queue(self, project=None, **options):
        response = self.client.post('/api/jobs', headers=HEADER, json={'project': project or self.project(), **options})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_local_security_header_origin_host_and_bounds(self):
        for endpoint in ('/api/media', '/api/projects', '/api/preview', '/api/jobs', '/api/jobs/missing/cancel', '/api/shutdown'):
            self.assertEqual(self.client.post(endpoint, json={}).status_code, 403)
        self.assertEqual(self.client.get('/api/status', headers={'Host': 'external.example'}).status_code, 400)
        self.assertEqual(self.client.post('/api/projects', headers={**HEADER, 'Origin': 'https://external.example'}, json={}).status_code, 403)
        response = self.client.options('/api/jobs', headers={'Origin': 'http://localhost:8770', 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'X-Editor-Client'})
        self.assertEqual(response.status_code, 200)
        response = self.client.post('/api/projects', headers={**HEADER, 'Content-Type': 'application/json', 'Content-Length': '99999999'}, content='{}')
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.client.post('/api/projects', headers=HEADER, content=b'x' * (2 * 1024**2 + 1)).status_code, 413)
        self.assertEqual(self.client.post('/api/projects', headers=HEADER, content=iter([b'x' * 1048576] * 3)).status_code, 413)
        self.assertEqual(list((self.store / 'media').iterdir()), [])

    def test_status_and_capabilities_no_gpu_requirement(self):
        status = self.client.get('/api/status').json()
        self.assertEqual(status['app'], '8K Lossless Video Editor')
        self.assertEqual(status['device'], 'cpu')
        self.assertFalse(status['accelerated'])
        self.assertEqual(self.client.get('/api/capabilities').json()['modes'][0]['id'], 'lossless')
        self.assertIn('formats', self.client.get('/api/formats').json())

    def test_real_image_ingest_hash_private_paths_and_assets(self):
        original = self.image_bytes()
        media = self.upload(original, '../../personal.png')
        self.assertEqual(media['name'], 'personal.png')
        self.assertEqual(media['kind'], 'image')
        self.assertEqual(media['sha256'], hashlib.sha256(original).hexdigest())
        source = self.state.library.snapshot()[media['id']]['path']
        self.assertEqual(Path(source).read_bytes(), original)
        self.assertNotIn('_source', media)
        self.assertNotIn(str(self.store), json.dumps(media))
        self.assertEqual(self.client.get(media['poster_url']).status_code, 200)
        self.assertEqual(self.client.get(media['proxy_url']).status_code, 200)
        self.assertEqual(self.client.get(f"/api/media/{media['id']}/waveform").json(), [])
        self.assertNotEqual(self.upload(original)['id'], media['id'])
        self.assertEqual(len(self.client.get('/api/media').json()), 2)
        for asset in ('source.png', 'media.json', '../source.png', 'prepare.log'):
            self.assertNotEqual(self.client.get(f"/api/media/{media['id']}/{asset}").status_code, 200)

    def test_bad_import_cleanup_preserves_existing_files(self):
        sentinel = self.store / 'personal.txt'
        sentinel.write_bytes(b'preserve')
        for name, content in [('bad.png', b'invalid image'), ('bad.r3d', b'RAW'), ('empty.mov', b'')]:
            result = self.client.post('/api/media', headers=HEADER, files={'file': (name, content)})
            self.assertEqual(result.status_code, 422, result.text)
            self.assertNotIn(str(self.store), result.text)
        self.assertEqual(list((self.store / 'media').iterdir()), [])
        self.assertEqual(sentinel.read_bytes(), b'preserve')

    def test_real_audio_and_video_proxy_waveform_and_source_preservation(self):
        for suffix, args, kind in [('.wav', ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000:duration=0.25', '-c:a', 'pcm_s16le'], 'audio'),
                                   ('.mov', ['-f', 'lavfi', '-i', 'testsrc2=size=64x48:rate=24:duration=0.25', '-f', 'lavfi', '-i', 'sine=frequency=400:duration=0.25', *media_pipeline._cli_h264_encoder(media_pipeline.FFMPEG), '-pix_fmt', 'yuv420p', '-c:a', 'pcm_s16le', '-shortest'], 'video')]:
            path = Path(self.temp.name) / ('fixture' + suffix)
            subprocess.run(['ffmpeg', '-v', 'error', '-n', *args, str(path)], check=True, capture_output=True)
            before = file_hash(path)
            media = self.upload(path.read_bytes(), path.name)
            self.assertEqual(media['kind'], kind)
            self.assertEqual(media['sha256'], before)
            self.assertEqual(len(media['waveform']), 512)
            self.assertGreater(max(media['waveform']), .01)
            self.assertEqual(media['audio_channels'], 1)
            self.assertGreater(media['audio_sample_rate'], 0)
            self.assertIsInstance(media['audio_layout'], str)
            self.assertEqual(self.client.get(media['proxy_url']).status_code, 200)
            self.assertEqual(file_hash(path), before)

    def test_native_16bit_image_and_metadata_interpretation_preserve_source(self):
        import numpy as np
        stream = io.BytesIO()
        Image.fromarray(np.arange(64 * 48, dtype=np.uint16).reshape(48, 64) * 17).save(stream, format='PNG')
        original = stream.getvalue()
        media = self.upload(original, 'precision.png')
        self.assertEqual(media['bit_depth'], 16)
        response = self.client.post(f"/api/media/{media['id']}/interpret", headers=HEADER,
                                    json={'transfer': 'linear', 'color_primaries': 'bt2020'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['color_primaries'], 'bt2020')
        self.assertEqual(response.json()['transfer'], 'linear')
        self.assertEqual(self.state.library.records[media['id']]['sha256'], hashlib.sha256(original).hexdigest())
        self.assertEqual(Path(self.state.library.snapshot()[media['id']]['path']).read_bytes(), original)
        self.assertEqual(self.client.post(f"/api/media/{media['id']}/interpret", headers=HEADER, json={'path': '/outside'}).status_code, 422)

    def test_float_exr_and_sequence_preserve_values_above_one(self):
        import av
        import numpy as np
        path = Path(self.temp.name) / 'scene.exr'
        with av.open(str(path), 'w', format='image2') as target:
            stream = target.add_stream('exr', rate=1)
            stream.width, stream.height, stream.pix_fmt = 64, 48, 'gbrpf32le'
            frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), 2.5, dtype=np.float32), format='gbrpf32le')
            for packet in stream.encode(frame):
                target.mux(packet)
            for packet in stream.encode():
                target.mux(packet)
        original = path.read_bytes()
        media = self.upload(original, 'scene.exr')
        self.assertTrue(media['float_source'])
        self.assertEqual((media['transfer'], media['bit_depth']), ('linear', 32))
        source = self.state.library.snapshot()[media['id']]['path']
        with av.open(source) as decoder:
            self.assertEqual(float(next(decoder.decode(video=0)).to_ndarray().max()), 2.5)
        self.assertEqual(Path(source).read_bytes(), original)
        response = self.client.post('/api/media/sequence', headers=HEADER, data={'fps': '24/1'},
                                    files=[('files', ('scene1.exr', original)), ('files', ('scene2.exr', original))])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((response.json()['bit_depth'], response.json()['transfer']), (32, 'linear'))
        self.assertEqual(self.client.get(response.json()['proxy_url']).status_code, 200)
        for source in self.state.library.snapshot()[response.json()['id']]['frames']:
            self.assertEqual(Path(source).read_bytes(), original)

    def test_professional_dnxhr_mxf_actual_ingest(self):
        path = Path(self.temp.name) / 'interchange.mxf'
        command = ['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i', 'testsrc2=size=256x144:rate=24:duration=0.125',
                   '-c:v', 'dnxhd', '-profile:v', 'dnxhr_lb', '-pix_fmt', 'yuv422p', '-threads', '1', str(path)]
        subprocess.run(command, check=True, capture_output=True)
        before = file_hash(path)
        media = self.upload(path.read_bytes(), path.name)
        self.assertEqual((media['kind'], media['codec'], media['container']), ('video', 'dnxhd', 'mxf'))
        self.assertEqual(media['sha256'], before)
        self.assertEqual(file_hash(path), before)

    def test_nut_duration_includes_last_frame_and_vfr_packet_extent(self):
        import av
        import numpy as np
        from fractions import Fraction
        for label, positions, expected in [('cfr', (0, 1), 2 / 24), ('gap', (0, 2), 3 / 24)]:
            path = Path(self.temp.name) / (label + '.nut')
            with av.open(str(path), 'w', format='nut') as target:
                stream = target.add_stream('ffv1', rate=24)
                stream.width, stream.height, stream.pix_fmt = 64, 48, 'gbrp16le'
                stream.time_base = stream.codec_context.time_base = Fraction(1, 24)
                for index in positions:
                    frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), index * 10000, dtype=np.uint16), format='rgb48le')
                    frame.pts, frame.time_base, frame.duration = index, Fraction(1, 24), 1
                    for packet in stream.encode(frame):
                        target.mux(packet)
                for packet in stream.encode():
                    target.mux(packet)
            original = path.read_bytes()
            media = self.upload(original, path.name)
            self.assertAlmostEqual(media['duration'], expected, places=9)
            self.assertEqual(media['sha256'], hashlib.sha256(original).hexdigest())
            timeline = self.project(media)
            timeline['tracks'][0]['clips'][0]['duration'] = expected
            self.assertEqual(self.client.post('/api/jobs', headers=HEADER, json={'project': timeline}).status_code, 200)

    def test_mov_duration_uses_exact_stream_ticks_not_rounded_decimal(self):
        import av
        import numpy as np
        from fractions import Fraction
        path = Path(self.temp.name) / '349-frames.mov'
        rate = Fraction(30000, 1001)
        with av.open(str(path), 'w', format='mov') as target:
            stream = target.add_stream('mjpeg', rate=rate)
            stream.width, stream.height, stream.pix_fmt = 64, 64, 'yuvj420p'
            stream.time_base = stream.codec_context.time_base = 1 / rate
            stream.codec_context.thread_count = 1
            pixels = np.full((64, 64, 3), 120, dtype=np.uint8)
            for index in range(349):
                frame = av.VideoFrame.from_ndarray(pixels, format='rgb24')
                frame.pts, frame.time_base, frame.duration = index, 1 / rate, 1
                for packet in stream.encode(frame):
                    target.mux(packet)
            for packet in stream.encode():
                target.mux(packet)
        original = path.read_bytes()
        media = self.upload(original, path.name)
        self.assertEqual(media['fps_exact'], '30000/1001')
        self.assertEqual(media['duration'], float(Fraction(349, 1) / rate))
        self.assertNotEqual(media['duration'], 11.644967)
        self.assertEqual(media['sha256'], hashlib.sha256(original).hexdigest())

    def test_real_image_sequence_explicit_order_proxy_and_restart(self):
        first, second = io.BytesIO(), io.BytesIO()
        Image.new('RGB', (64, 48), 'red').save(first, format='PNG')
        Image.new('RGB', (64, 48), 'blue').save(second, format='PNG')
        files = [('files', ('z-last-name.png', first.getvalue(), 'image/png')),
                 ('files', ('a-first-name.png', second.getvalue(), 'image/png'))]
        response = self.client.post('/api/media/sequence', headers=HEADER, data={'fps': '2/1'}, files=files)
        self.assertEqual(response.status_code, 200, response.text)
        media = response.json()
        self.assertEqual((media['kind'], media['frame_count'], media['fps_exact'], media['duration']), ('sequence', 2, '2/1', 1))
        record = self.state.library.snapshot()[media['id']]
        self.assertEqual([Path(name).read_bytes() for name in record['frames']], [first.getvalue(), second.getvalue()])
        self.assertNotIn('frames', media)
        self.assertNotIn(str(self.store), json.dumps(media))
        self.assertEqual(self.client.get(media['proxy_url']).status_code, 200)
        replacement = MediaLibrary(self.store / 'media')
        try:
            self.assertEqual(len(replacement.snapshot()[media['id']]['frames']), 2)
        finally:
            replacement.close()
        for fps in ('NaN', '0/1', '1/0', '121'):
            response = self.client.post('/api/media/sequence', headers=HEADER, data={'fps': fps}, files=files)
            self.assertEqual(response.status_code, 422, response.text)
        mismatched = [('files', ('one.png', first.getvalue(), 'image/png')),
                      ('files', ('two.png', self.image_bytes((32, 48)), 'image/png'))]
        response = self.client.post('/api/media/sequence', headers=HEADER, data={'fps': '24/1'}, files=mismatched)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(len(self.state.library.list()), 1)

    def test_project_save_update_reload_and_offline_relink(self):
        project = self.project()
        response = self.client.post('/api/projects', headers=HEADER, json={'project': project})
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertEqual(saved['id'], saved['project']['id'])
        saved['project']['name'] = 'Changed name'
        updated = self.client.post('/api/projects', headers=HEADER, json={'project': saved['project']}).json()
        self.assertEqual(saved['id'], updated['id'])
        self.assertEqual(self.client.get('/api/projects').json()[0]['name'], 'Changed name')
        reloaded = server.EditorService(self.store, self.pipeline, project_model, PendingExecutor())
        try:
            self.assertEqual(reloaded.get_project(saved['id'])['project'], updated['project'])
            self.assertEqual(len(reloaded.library.list()), 1)
        finally:
            reloaded.close()
        saved['project']['tracks'][0]['clips'][0]['media_id'] = 'f' * 32
        response = self.client.post('/api/projects', headers=HEADER, json={'project': saved['project']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.post('/api/jobs', headers=HEADER, json={'project': saved['project']}).status_code, 404)
        saved['project']['tracks'][0]['muted'] = True
        self.assertEqual(self.client.post('/api/jobs', headers=HEADER, json={'project': saved['project']}).status_code, 200)

    def test_project_numeric_unknown_and_path_rejections(self):
        project = self.project()
        for field, value in [('width', True), ('fps', '0/1'), ('path', '/private/source'), ('id', '../outside')]:
            body = copy.deepcopy(project)
            body[field] = value
            response = self.client.post('/api/projects', headers=HEADER, json={'project': body})
            self.assertEqual(response.status_code, 422, response.text)
        project['tracks'][0]['clips'][0]['exposure'] = float('inf')
        response = self.client.post('/api/projects', headers={**HEADER, 'Content-Type': 'application/json'}, content=json.dumps({'project': project}))
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.post('/api/jobs', headers=HEADER, json={'project': self.project(), 'mode': '../unsafe'}).status_code, 422)

    def test_preview_actual_jpeg_busy_and_validation(self):
        project = self.project()
        response = self.client.post('/api/preview', headers=HEADER, json={'project': project, 'time': .1, 'width': 64})
        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()
        self.assertEqual(Image.open(io.BytesIO(self.client.get(preview['url']).content)).size, (64, 48))
        self.state.renderer.acquire()
        try:
            self.assertEqual(self.client.post('/api/preview', headers=HEADER, json={'project': project}).status_code, 409)
        finally:
            self.state.renderer.release()
        for extra in ({'time': 99}, {'width': True}, {'time': True}):
            self.assertEqual(self.client.post('/api/preview', headers=HEADER, json={'project': project, **extra}).status_code, 422)

    def test_job_snapshot_queue_limit_cancel_and_verified_download(self):
        project = self.project()
        job = self.queue(project)
        saved_name = project['name']
        project['name'] = 'Edited after queue'
        for _ in range(3):
            self.queue(project)
        self.assertEqual(self.client.post('/api/jobs', headers=HEADER, json={'project': project}).status_code, 409)
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}/download").status_code, 409)
        self.executor.run()
        completed = self.client.get(f"/api/jobs/{job['id']}").json()
        self.assertEqual(completed['status'], 'completed')
        self.assertEqual(self.pipeline.snapshots[0]['name'], saved_name)
        self.assertNotIn(str(self.store), json.dumps(completed))
        self.assertNotIn('output', completed['result'])
        self.assertEqual(self.client.get(completed['output_url']).content, b'verified-test-output')
        output = self.store / 'jobs' / job['id'] / completed['output_name']
        output.write_bytes(b'tampered-test-output')
        self.assertEqual(self.client.get(completed['output_url']).status_code, 409)
        pending = next(value for value in self.state.jobs.values() if value['status'] == 'queued')
        response = self.client.post(f"/api/jobs/{pending['id']}/cancel", headers=HEADER)
        self.assertEqual(response.status_code, 200)
        self.executor.run()
        self.assertEqual(self.client.get(f"/api/jobs/{pending['id']}").json()['status'], 'cancelled')

    def test_running_cancel_and_late_completed_cancel(self):
        job = self.queue()
        self.pipeline.block = True
        worker = threading.Thread(target=self.executor.run)
        worker.start()
        self.assertTrue(self.pipeline.entered.wait(2))
        self.client.post(f"/api/jobs/{job['id']}/cancel", headers=HEADER)
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}").json()['status'], 'cancelled')
        self.pipeline.block = False
        completed = self.queue()
        self.executor.run()
        response = self.client.post(f"/api/jobs/{completed['id']}/cancel", headers=HEADER)
        self.assertEqual(response.json()['status'], 'completed')

    def test_failed_export_retains_final_worker_cause_and_bounded_private_diagnostics(self):
        job = self.queue()
        final_cause = 'ValueError: Requested pixel format is not supported by this encoder'
        worker_log = 'Export failed: ' + ('progress and encoder context\n' * 400) + str(self.store) + '\n' + final_cause
        with patch.object(self.pipeline, 'render_project', side_effect=ValueError(worker_log)):
            self.executor.run()
        failed = self.client.get(f"/api/jobs/{job['id']}").json()
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['message'], 'Render failed')
        self.assertEqual(len(failed['error']), server.MAX_ERROR_DETAILS)
        self.assertTrue(failed['error'].startswith('[Earlier diagnostic lines omitted]\n'))
        self.assertTrue(failed['error'].endswith(final_cause))
        self.assertIn('<local data>', failed['error'])
        self.assertNotIn(str(self.store), failed['error'])
        self.assertIsNone(failed['output_url'])
        persisted = json.loads((self.store / 'jobs' / job['id'] / 'job.json').read_text(encoding='utf-8'))
        self.assertEqual(persisted['error'], failed['error'])
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}/download").status_code, 409)
        with patch.object(self.state, 'validate', side_effect=ValueError(worker_log)):
            response = self.client.post('/api/jobs', headers=HEADER, json={'project': self.project()})
        self.assertEqual(response.status_code, 422)
        self.assertTrue(response.json()['detail'].endswith(final_cause))
        self.assertLessEqual(len(response.json()['detail']), server.MAX_ERROR_DETAILS)
        self.assertEqual(server.diagnostic_tail('Short actionable error'), 'Short actionable error')

    def test_queued_cancellation_immediately_frees_a_queue_slot(self):
        project = self.project()
        jobs = [self.queue(project) for _ in range(4)]
        self.assertEqual(self.client.post('/api/jobs', headers=HEADER, json={'project': project}).status_code, 409)
        response = self.client.post(f"/api/jobs/{jobs[-1]['id']}/cancel", headers=HEADER)
        self.assertEqual(response.json()['status'], 'cancelled')
        self.assertEqual(self.client.post('/api/jobs', headers=HEADER, json={'project': project}).status_code, 200)

    def test_shutdown_cancels_owned_preview_and_releases_renderer(self):
        project = self.project()
        self.pipeline.frame_block = True
        result = []
        worker = threading.Thread(target=lambda: result.append(self.client.post('/api/preview', headers=HEADER, json={'project': project})))
        worker.start()
        self.assertTrue(self.pipeline.frame_entered.wait(2))
        self.assertEqual(self.client.post('/api/shutdown', headers=HEADER).status_code, 200)
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0].status_code, 409)
        self.assertFalse(self.state.renderer.locked())
        self.assertEqual(self.state.preview_cancel, set())

    def test_restart_marks_interrupted_and_shutdown_only_this_service(self):
        job = self.queue()
        replacement = server.EditorService(self.store, self.pipeline, project_model, PendingExecutor())
        try:
            self.assertEqual(replacement.jobs[job['id']]['status'], 'failed')
            self.assertIn('restart', replacement.jobs[job['id']]['message'])
        finally:
            replacement.close()
        source = next(iter(self.state.library.snapshot().values()))['path']
        before = file_hash(source)
        with patch.object(server, 'SERVER', None):
            self.assertTrue(self.client.post('/api/shutdown', headers=HEADER).json()['stopping'])
        self.assertTrue(self.state.closing)
        self.assertEqual(file_hash(source), before)

    def test_actual_cpu_timeline_render_preview_export_and_decode(self):
        import av
        actual_store = Path(self.temp.name) / 'actual-engine-data'
        original = self.image_bytes()
        with patch.dict(os.environ, {'STUDIO_DEVICE': 'cpu'}):
            with TestClient(server.create_app(actual_store), raise_server_exceptions=False) as client:
                media = client.post('/api/media', headers=HEADER, files={'file': ('native.png', original)}).json()
                project = self.project(media)
                project['tracks'][0]['clips'][0]['duration'] = .125
                frame = client.post('/api/preview', headers=HEADER, json={'project': project, 'time': 0., 'width': 64})
                self.assertEqual(frame.status_code, 200, frame.text)
                self.assertEqual(client.get(frame.json()['url']).status_code, 200)
                response = client.post('/api/jobs', headers=HEADER, json={'project': project, 'mode': 'lossless'})
                self.assertEqual(response.status_code, 200, response.text)
                job = response.json()
                deadline = time.monotonic() + 60
                while job['status'] in server.ACTIVE and time.monotonic() < deadline:
                    time.sleep(.1)
                    job = client.get(f"/api/jobs/{job['id']}").json()
                self.assertEqual(job['status'], 'completed', job)
                result = client.get(job['output_url'])
                self.assertEqual(result.status_code, 200)
                with av.open(io.BytesIO(result.content)) as decoded:
                    frames = list(decoded.decode(video=0))
                self.assertEqual(len(frames), 3)
                self.assertEqual((frames[0].width, frames[0].height), (64, 48))
                self.assertIn('SHA256', job['result']['verification'])
                self.assertEqual((actual_store / 'media' / media['id'] / 'source.png').read_bytes(), original)


if __name__ == '__main__':
    unittest.main(verbosity=2)
