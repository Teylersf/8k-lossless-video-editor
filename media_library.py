"""Owned local media imports and bounded browser assets. GPL-3.0-or-later; see LICENSE and LICENSE-EXCEPTION.

The preparation subprocess has its own session, so cancelling shutdown can stop
its native decoders without signalling another application or the source file.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from fractions import Fraction

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'engine'))
from engine import media_pipeline as media_pipeline

MAX_UPLOAD = 64 * 1024**3
MAX_PIXELS = 35_651_584
MAX_DURATION = 86400
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff', '.gif', '.dpx', '.exr', '.jp2', '.j2k', '.j2c', '.jpf', '.jpx', '.jls', '.jxl', '.tga'}
PUBLIC_FIELDS = {'id', 'name', 'kind', 'width', 'height', 'fps', 'fps_exact', 'duration', 'codec', 'container', 'pixel_format', 'bit_depth', 'audio_streams', 'audio_channels', 'audio_layout', 'audio_sample_rate', 'transfer', 'color_primaries', 'color_space', 'color_range', 'bytes', 'sha256', 'proxy_url', 'poster_url', 'waveform', 'warnings', 'rotation', 'video_stream_index', 'sample_aspect_ratio', 'frame_count', 'float_source'}


def safe_formats():
    from engine.format_catalog import safe_demuxer_names
    return ','.join(sorted(safe_demuxer_names()))


def supported_extensions():
    from engine.format_catalog import supported_extensions as installed_extensions
    return set(installed_extensions())


def interpretation(value):
    if not isinstance(value, dict) or set(value) - {'transfer', 'color_primaries'}:
        raise ValueError('Source interpretation accepts only transfer and color_primaries')
    allowed = {'transfer': {'linear', 'srgb', 'bt709', 'pq', 'hlg'},
               'color_primaries': {'bt709', 'bt2020', 'smpte432', 'smpte431', 'bt470bg', 'smpte170m', 'smpte240m', 'acescg'}}
    for key, item in value.items():
        if not isinstance(item, str) or item not in allowed[key]:
            raise ValueError(f'Unsupported source {key}')
    return dict(value)


def probe(source):
    from engine.format_catalog import BLOCKED_DEMUXERS
    data = json.loads(_run([media_pipeline.FFPROBE, '-v', 'error', '-protocol_whitelist', 'file,pipe', '-format_whitelist', safe_formats(),
                           '-show_streams', '-show_format', '-of', 'json', str(source)]))
    detected = set(data.get('format', {}).get('format_name', '').split(','))
    if detected & set(BLOCKED_DEMUXERS):
        raise ValueError('Playlist, script, network and package demuxers are not accepted')
    return data


def still_info(source, poster=None, override=None):
    """Inspect native DPX/EXR/16-bit images before making an 8-bit display asset."""
    import av
    import numpy as np
    from PIL import Image
    raw = probe(source)
    stream_info = next((s for s in raw['streams'] if s.get('codec_type') == 'video'), None)
    if not stream_info:
        raise ValueError('Image decoder did not find a picture')
    _geometry(int(stream_info['width']), int(stream_info['height']))
    with av.open(str(source), options={'protocol_whitelist': 'file,pipe', 'format_whitelist': safe_formats()}) as container:
        stream = container.streams.video[0]
        stream.codec_context.thread_count = 1
        frame = next(container.decode(stream), None)
        if frame is None:
            raise ValueError('Image decoder produced no frame')
        pixel = frame.format.name
        floating = 'f32' in pixel or 'f16' in pixel or source.suffix == '.exr'
        transfer = 'linear' if floating else {'smpte2084': 'pq', 'arib-std-b67': 'hlg', 'iec61966-2-1': 'srgb', 'linear': 'linear'}.get(stream_info.get('color_transfer'), 'srgb')
        info = {'kind': 'image', 'width': frame.width, 'height': frame.height, 'codec': stream_info.get('codec_name', 'image'),
                'pixel_format': pixel, 'bit_depth': max(c.bits for c in frame.format.components), 'transfer': transfer,
                'color_primaries': stream_info.get('color_primaries', 'unknown'), 'color_space': stream_info.get('color_space', 'unknown'),
                'color_range': stream_info.get('color_range', 'unknown'), 'float_source': floating, 'warnings': []}
        info.update(interpretation(override or {}))
        if floating:
            info['warnings'].append('Scene-linear float source bytes are preserved. Viewing assets are SDR tone-mapped; export uses the native float source, not this display proxy.')
            if info['color_primaries'] in ('unknown', 'unspecified'):
                info['warnings'].append('Float source primaries are unknown. Set source/clip interpretation before rendering; the viewing approximation does not assign an export color space.')
        if info['color_primaries'] in ('unknown', 'unspecified') and not floating:
            info['warnings'].append('Still-image primaries are unspecified; select source interpretation when a calibrated input color space is known.')
        if poster is not None:
            ratio = min(1, 1280 / max(frame.width, frame.height))
            width, height = max(1, round(frame.width * ratio)), max(1, round(frame.height * ratio))
            if floating:
                values = frame.reformat(width=width, height=height, format='gbrpf32le').to_ndarray()
                if not np.isfinite(values).all():
                    raise ValueError('Float image contains nonfinite samples')
                positive = np.maximum(values, 0)
                mapped = positive / (1 + positive)
                mapped = np.where(mapped <= .0031308, mapped * 12.92, 1.055 * np.power(mapped, 1 / 2.4) - .055)
                image = np.rint(np.clip(mapped, 0, 1) * 255).astype(np.uint8)
            else:
                image = frame.reformat(width=width, height=height, format='rgb24').to_ndarray()
            Image.fromarray(image).save(poster, quality=92)
    return info


def valid_id(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[0-9a-f]{32}', value))


def atomic_json(path, data):
    path = Path(path)
    temporary = path.with_name('.' + uuid.uuid4().hex + '.writing')
    try:
        with temporary.open('x', encoding='utf-8') as target:
            json.dump(data, target, ensure_ascii=False, allow_nan=False, indent=2)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path, limit=2 * 1024**2):
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > limit:
        raise ValueError('Invalid local record')
    return json.loads(path.read_text(encoding='utf-8'), parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _run(args, timeout=120, limit=4 * 1024**2):
    """Bound command output; stderr lives in a temporary file, never a pipe."""
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors)
        timer = threading.Timer(timeout, proc.kill)
        timer.start()
        try:
            chunks, count = [], 0
            while block := proc.stdout.read(65536):
                count += len(block)
                if count > limit:
                    raise ValueError('Decoder metadata exceeded its safety bound')
                chunks.append(block)
            proc.wait(timeout=10)
            if proc.returncode:
                errors.seek(0, 2)
                errors.seek(max(0, errors.tell() - 3000))
                raise ValueError('Media decoder failed: ' + errors.read().decode(errors='replace'))
            return b''.join(chunks)
        finally:
            timer.cancel()
            proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=15)


def _geometry(width, height):
    if not 1 <= width <= 8192 or not 1 <= height <= 8192 or width * height > MAX_PIXELS:
        raise ValueError('Source exceeds the 8192-per-axis / 35.65 MP processing envelope')


def _nut_video_duration(source, video, nominal_rate):
    """NUT's format duration can be the last PTS, excluding the final picture.

    Stream packet timings without decoding or retaining the packet table. Use
    each packet's duration, so a VFR gap is not flattened to frame_count / FPS.
    """
    base = Fraction(video['time_base'])
    fallback = 1 / Fraction(str(nominal_rate))
    earliest, end, count = None, None, 0
    args = [media_pipeline.FFPROBE, '-v', 'error', '-protocol_whitelist', 'file,pipe', '-format_whitelist', safe_formats(),
            '-select_streams', str(video['index']), '-show_packets', '-show_entries', 'packet=pts,duration', '-of', 'compact=p=0:nk=0', str(source)]
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors)
        timer = threading.Timer(7200, proc.kill)
        timer.start()
        try:
            while line := proc.stdout.readline(4096):
                count += 1
                if len(line) >= 4096 or count > 20_000_000:
                    raise ValueError('NUT packet timing scan exceeded its safety bound')
                fields = dict(part.split('=', 1) for part in line.decode('ascii').strip().split('|') if '=' in part)
                if fields.get('pts') in (None, 'N/A'):
                    raise ValueError('NUT video packet has no usable presentation timestamp')
                point = int(fields['pts']) * base
                duration = int(fields['duration']) * base if fields.get('duration') not in (None, 'N/A', '0') else fallback
                if duration <= 0:
                    raise ValueError('NUT video packet duration is invalid')
                earliest = point if earliest is None else min(earliest, point)
                end = point + duration if end is None else max(end, point + duration)
            proc.wait(timeout=15)
            if proc.returncode or end is None:
                raise ValueError('Could not determine the complete NUT video extent')
            origin = Fraction(str(video['start_time'])) if video.get('start_time') not in (None, 'N/A') else earliest
            result = float(end - origin)
            if not math.isfinite(result) or not 0 < result <= MAX_DURATION:
                raise ValueError('NUT video duration exceeds the supported timeline bound')
            return result
        finally:
            timer.cancel()
            proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=15)


def _waveform(source, duration, has_audio):
    if not has_audio:
        return []
    import numpy as np
    bins, rate = 512, 8000
    peaks = np.zeros(bins, dtype=np.float32)
    count = 0
    expected = max(1, math.ceil(duration * rate))
    args = [media_pipeline.FFMPEG, '-v', 'error', '-xerror', '-protocol_whitelist', 'file,pipe', '-format_whitelist', safe_formats(),
            '-i', str(source), '-map', '0:a:0', '-vn', '-ac', '1', '-ar', str(rate), '-f', 'f32le', 'pipe:1']
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors)
        timer = threading.Timer(7200, proc.kill)
        timer.start()
        try:
            while block := proc.stdout.read(65536):
                if len(block) % 4:
                    raise ValueError('Incomplete audio sample from decoder')
                values = np.frombuffer(block, dtype='<f4')
                if not np.isfinite(values).all():
                    raise ValueError('Audio contains nonfinite samples')
                if count + len(values) > (MAX_DURATION + 2) * rate:
                    raise ValueError('Audio exceeds the one-day safety bound')
                indices = np.minimum(bins - 1, (np.arange(len(values), dtype=np.int64) + count) * bins // expected)
                np.maximum.at(peaks, indices, np.abs(values))
                count += len(values)
            proc.wait(timeout=15)
            if proc.returncode or not count:
                raise ValueError('Audio waveform decode failed')
            return np.minimum(peaks, 1).round(6).tolist()
        finally:
            timer.cancel()
            proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=15)


def prepare_media(folder, source_name):
    """Called only inside the owned preparation process."""
    from PIL import Image, ImageDraw
    source = folder / source_name
    if source.is_symlink() or not source.is_file() or not re.fullmatch(r'source\.[a-z0-9]{1,10}', source_name):
        raise ValueError('Invalid owned source')
    common = {'width': 0, 'height': 0, 'fps': 0, 'fps_exact': '0/1', 'duration': 0, 'codec': '', 'container': source.suffix[1:],
              'pixel_format': '', 'bit_depth': 0, 'audio_streams': 0, 'transfer': 'bt709', 'color_primaries': 'unknown',
              'color_space': 'unknown', 'color_range': 'unknown', 'waveform': [], 'warnings': [],
              'audio_channels': 0, 'audio_layout': 'unknown', 'audio_sample_rate': 0}
    # This module runs in an isolated import worker. Expand only its inherited
    # adapter configuration; no upstream source or live-server globals change.
    formats = safe_formats()
    media_pipeline._DEMUXERS = formats
    media_pipeline._PROTOCOLS = ['-protocol_whitelist', 'file,pipe', '-format_whitelist', formats]
    def worker_command(args, timeout=120):
        if args[0] == media_pipeline.FFMPEG and '-i' in args:
            args = [args[0], '-threads', '1', '-filter_threads', '1', '-filter_complex_threads', '1', *args[1:]]
        return _run(args, timeout)
    media_pipeline._run = worker_command
    if source.suffix in IMAGE_EXTENSIONS:
        common.update(still_info(source, folder / 'poster.jpg'))
    else:
        raw = probe(source)
        videos = [s for s in raw.get('streams', []) if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')]
        audios = [s for s in raw.get('streams', []) if s.get('codec_type') == 'audio']
        if audios:
            common.update(audio_channels=int(audios[0].get('channels') or 0),
                          audio_layout=audios[0].get('channel_layout') or 'unknown',
                          audio_sample_rate=int(audios[0].get('sample_rate') or 0))
        if videos:
            details = media_pipeline.inspect_media(source)
            exact_duration = None
            try:
                ticks = int(videos[0]['duration_ts'])
                time_base = Fraction(videos[0]['time_base'])
                if ticks > 0 and time_base > 0:
                    candidate = float(ticks * time_base)
                    if math.isfinite(candidate) and candidate <= MAX_DURATION:
                        exact_duration = candidate
            except (KeyError, ValueError, TypeError, ZeroDivisionError, OverflowError):
                pass
            if exact_duration is not None:
                details['duration'] = exact_duration
            elif 'nut' in raw.get('format', {}).get('format_name', '').split(',') and videos[0].get('duration') in (None, 'N/A'):
                details['duration'] = _nut_video_duration(source, videos[0], details['fps_exact'])
            _geometry(int(details['width']), int(details['height']))
            common.update({key: value for key, value in details.items() if key in PUBLIC_FIELDS})
            common.update(kind='video', width=details.get('display_width', details['width']), height=details.get('display_height', details['height']))
            if not math.isfinite(common['duration']) or not 0 < common['duration'] <= MAX_DURATION:
                raise ValueError('Video duration must be positive and at most one day')
            media_pipeline.make_proxy(source, folder / 'proxy.mp4', folder / 'poster.jpg')
        elif audios:
            duration = float(audios[0].get('duration') or raw.get('format', {}).get('duration') or 0)
            if not math.isfinite(duration) or not 0 < duration <= MAX_DURATION:
                raise ValueError('Audio duration must be positive and at most one day')
            common.update(kind='audio', duration=duration, codec=audios[0].get('codec_name', 'audio'), audio_streams=len(audios),
                          pixel_format=audios[0].get('sample_fmt', ''), bit_depth=int(audios[0].get('bits_per_raw_sample') or audios[0].get('bits_per_sample') or 0))
            _run([media_pipeline.FFMPEG, '-v', 'error', '-xerror', '-n', '-protocol_whitelist', 'file,pipe', '-format_whitelist', formats,
                  '-i', str(source), '-map', '0:a:0', '-vn', '-c:a', 'aac', '-b:a', '160k', str(folder / 'proxy.m4a')], 7200)
        else:
            raise ValueError('No supported video or audio stream was found')
        common['waveform'] = _waveform(source, common['duration'], bool(audios))
        if common['kind'] == 'audio':
            image = Image.new('RGB', (640, 180), '#181b22')
            draw = ImageDraw.Draw(image)
            for i, value in enumerate(common['waveform']):
                x = round(i * 639 / 511)
                draw.line((x, 90 - value * 74, x, 90 + value * 74), fill='#bdacff')
            image.save(folder / 'poster.jpg', quality=90)
    atomic_json(folder / 'prepared.json', common)


class MediaBusyError(ValueError):
    pass


def prepare_sequence(folder, fps):
    rate = Fraction(fps)
    if not 1 <= rate <= 120:
        raise ValueError('Sequence frame rate must be 1..120')
    manifest = read_json(folder / 'sequence-input.json')
    frames = manifest['frames']
    if not isinstance(frames, list) or not 1 <= len(frames) <= 10000:
        raise ValueError('Sequence supports 1..10000 explicit frames')
    first = None
    for index, name in enumerate(frames):
        if not re.fullmatch(r'frame[0-9]{8}\.[a-z0-9]{1,10}', name) or not name.startswith(f'frame{index:08d}.'):
            raise ValueError('Invalid owned sequence frame order')
        path = folder / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Sequence frame is unavailable')
        info = still_info(path, folder / 'poster.jpg' if index == 0 else None)
        if first is None:
            first = info
        elif any(info[key] != first[key] for key in ('width', 'height', 'pixel_format', 'bit_depth', 'transfer', 'color_primaries')):
            raise ValueError('Sequence frames must have matching geometry, pixel format and color interpretation')
    duration = len(frames) / float(rate)
    if duration > MAX_DURATION:
        raise ValueError('Sequence exceeds the one-day safety bound')
    first.update(kind='sequence', duration=duration, frame_count=len(frames), fps=float(rate), fps_exact=f'{rate.numerator}/{rate.denominator}',
                 audio_streams=0, audio_channels=0, audio_layout='unknown', audio_sample_rate=0,
                 waveform=[], container=Path(frames[0]).suffix[1:])
    filters = []
    if first['transfer'] == 'linear':
        primaries = first['color_primaries'] if first['color_primaries'] not in ('unknown', 'unspecified') else 'bt709'
        filters += ['format=gbrpf32le', 'tonemap=tonemap=reinhard:desat=0',
                    f'zscale=pin={primaries}:tin=linear:min=gbr:p=bt709:t=bt709:m=bt709:r=tv']
    elif first['transfer'] in ('pq', 'hlg'):
        media_pipeline._require_hdr_proxy_filters()
        filters += ['zscale=t=linear:npl=100', 'format=gbrpf32le', 'tonemap=tonemap=mobius:desat=0', 'zscale=p=bt709:t=bt709:m=bt709:r=tv']
    filters += ["scale=w='max(2,2*trunc(min(1280,iw)/2))':h='max(2,2*trunc(ow*ih/iw/2))'", 'format=yuv420p', 'setsar=1']
    pattern = folder / ('frame%08d' + Path(frames[0]).suffix)
    _run([media_pipeline.FFMPEG, '-threads', '1', '-filter_threads', '1', '-filter_complex_threads', '1', '-v', 'error', '-xerror', '-n', '-protocol_whitelist', 'file,pipe', '-format_whitelist', safe_formats(),
          '-framerate', str(rate), '-f', 'image2', '-start_number', '0', '-i', str(pattern), '-frames:v', str(len(frames)), '-vf', ','.join(filters),
          '-an', *media_pipeline._cli_h264_encoder(media_pipeline.FFMPEG), '-threads', '1', '-movflags', '+faststart', str(folder / 'proxy.mp4')], 7200)
    _run([media_pipeline.FFMPEG, '-v', 'error', '-xerror', '-protocol_whitelist', 'file,pipe', '-format_whitelist', safe_formats(),
          '-i', str(folder / 'proxy.mp4'), '-f', 'null', '-'], 7200)
    atomic_json(folder / 'prepared.json', first)


class MediaLibrary:
    def __init__(self, root):
        if Path(root).is_symlink():
            raise ValueError('Media library directory must not be a symbolic link')
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.records = {}
        self.lock = threading.RLock()
        self.import_lock = threading.Lock()
        self.stopped = threading.Event()
        self.processes = set()
        for path in self.root.glob('*/media.json'):
            try:
                value = read_json(path, 4 * 1024**2)
                identifier = value['id']
                if not valid_id(identifier) or path.parent.name != identifier or path.parent.is_symlink():
                    continue
                self._source(value)
                if not re.fullmatch('[0-9a-f]{64}', value['sha256']):
                    continue
                self.records[identifier] = value
            except (ValueError, KeyError, TypeError, OSError):
                continue

    def _source(self, value):
        identifier, name = value['id'], value['_source']
        if not valid_id(identifier) or not re.fullmatch(r'(?:source|frame[0-9]{8})\.[a-z0-9]{1,10}', name):
            raise ValueError('Invalid media identity')
        folder, source = self.root / identifier, self.root / identifier / name
        if folder.is_symlink() or source.is_symlink() or not source.is_file() or source.resolve().parent != folder.resolve() or folder.resolve().parent != self.root:
            raise ValueError('Media source is unavailable or outside the owned library')
        if source.stat().st_size != (value['_frame_sizes'][0] if value.get('kind') == 'sequence' else value['bytes']):
            raise ValueError('Media source size changed; import it again')
        return source

    def public(self, value):
        return {key: value[key] for key in PUBLIC_FIELDS if key in value}

    def get(self, identifier):
        with self.lock:
            if not valid_id(identifier) or identifier not in self.records:
                raise KeyError('Media not found; import or relink it')
            value = self.records[identifier]
            self._source(value)
            return self.public(value)

    def list(self):
        with self.lock:
            return [self.public(value) for value in self.records.values()]

    def snapshot(self, identifiers=None):
        with self.lock:
            selected = set(self.records) if identifiers is None else set(identifiers)
            if selected - self.records.keys():
                raise KeyError('Project references missing media; import and relink it')
            result = {}
            for key in selected:
                value = self.records[key]
                item = {'path': str(self._source(value)), 'public': self.public(value), **self.public(value)}
                if value.get('kind') == 'sequence':
                    item['frames'] = []
                    for name, size in zip(value['_frames'], value['_frame_sizes'], strict=True):
                        path = self.root / key / name
                        if not re.fullmatch(r'frame[0-9]{8}\.[a-z0-9]{1,10}', name) or path.is_symlink() or not path.is_file() or path.stat().st_size != size:
                            raise ValueError('Sequence source frame changed or is unavailable')
                        item['frames'].append(str(path))
                result[key] = item
            return result

    def interpret(self, identifier, settings):
        settings = interpretation(settings)
        self.get(identifier)
        with self.lock:
            value = dict(self.records[identifier])
            value.update(settings)
            value['warnings'] = [item for item in value.get('warnings', []) if 'Float source primaries are unknown' not in item]
            warning = 'Source interpretation is explicit metadata; source bytes are unchanged. Browser assets remain viewing approximations; timeline previews use this interpretation.'
            if warning not in value['warnings']:
                value['warnings'].append(warning)
            atomic_json(self.root / identifier / 'media.json', value)
            self.records[identifier] = value
            return self.public(value)

    def asset(self, identifier, asset):
        media = self.get(identifier)
        if asset == 'poster' or (asset == 'proxy' and media['kind'] == 'image'):
            name, mime = 'poster.jpg', 'image/jpeg'
        elif asset == 'proxy':
            name = 'proxy.mp4' if media['kind'] in ('video', 'sequence') else 'proxy.m4a'
            mime = 'video/mp4' if media['kind'] in ('video', 'sequence') else 'audio/mp4'
        else:
            raise KeyError('Unknown media asset')
        path = self.root / identifier / name
        if path.is_symlink() or not path.is_file() or path.resolve().parent != (self.root / identifier).resolve():
            raise KeyError('Media asset is unavailable')
        return path, mime

    def import_file(self, stream, filename):
        if not self.import_lock.acquire(blocking=False):
            raise MediaBusyError('Another import is preparing browser assets; try again when it finishes')
        folder = None
        try:
            if self.stopped.is_set():
                raise ValueError('Editor is stopping')
            name = Path((filename or '').replace('\\', '/')).name
            name = ''.join(c for c in name if c.isprintable())[:255]
            extension = Path(name).suffix.lower()
            if extension in {'.braw', '.r3d', '.ari', '.arx'}:
                raise ValueError('BRAW, R3D and ARRIRAW require their vendor SDK/development workflow; import an interchange render such as ProRes, DNxHR, DPX or EXR instead')
            if extension not in supported_extensions():
                raise ValueError('This file extension is outside the installed format catalogue; check Format Support')
            identifier = uuid.uuid4().hex
            folder = self.root / identifier
            folder.mkdir(mode=0o700)
            source = folder / ('source' + extension)
            length, digest = 0, hashlib.sha256()
            with source.open('xb') as target:
                while block := stream.read(1024 * 1024):
                    length += len(block)
                    if self.stopped.is_set() or length > MAX_UPLOAD:
                        raise ValueError('Import cancelled or exceeds the 64 GiB bound')
                    if shutil.disk_usage(self.root).free < len(block) + 512 * 1024**2:
                        raise ValueError('Insufficient local storage for the import')
                    digest.update(block)
                    target.write(block)
                target.flush()
                os.fsync(target.fileno())
            if not length:
                raise ValueError('The selected file is empty')
            value = self._prepare_worker(folder, '--prepare', source.name)
            if file_hash(source) != digest.hexdigest():
                raise ValueError('The uploaded source changed during preparation')
            value.update(id=identifier, name=name, bytes=length, sha256=digest.hexdigest(), _source=source.name,
                         proxy_url=f'/api/media/{identifier}/proxy', poster_url=f'/api/media/{identifier}/poster')
            atomic_json(folder / 'media.json', value)
            with self.lock:
                self.records[identifier] = value
            return self.public(value)
        except BaseException:
            if folder is not None and folder.parent == self.root and not folder.is_symlink():
                shutil.rmtree(folder)
            raise
        finally:
            self.import_lock.release()

    def _prepare_worker(self, folder, command, argument):
        options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        with (folder / 'prepare.log').open('wb') as log:
            proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), command, str(folder), argument], stdout=log, stderr=log, **options)
            with self.lock:
                self.processes.add(proc)
            try:
                started = time.monotonic()
                while proc.poll() is None:
                    if self.stopped.is_set() or time.monotonic() - started > 7200:
                        raise InterruptedError('Import preparation cancelled or timed out')
                    time.sleep(.05)
                if proc.returncode:
                    with (folder / 'prepare.log').open('rb') as error:
                        error.seek(0, 2)
                        error.seek(max(0, error.tell() - 3000))
                        raise ValueError(error.read().decode(errors='replace'))
            finally:
                media_pipeline._stop_tree(proc)
                with self.lock:
                    self.processes.discard(proc)
        return read_json(folder / 'prepared.json', 128 * 1024)

    def import_sequence(self, uploads, fps):
        try:
            rate = Fraction(str(fps))
        except (ValueError, ZeroDivisionError, TypeError) as error:
            raise ValueError('Sequence fps must be a rational frame rate') from error
        if not 1 <= rate <= 120 or not 1 <= len(uploads) <= 10000:
            raise ValueError('Sequence requires 1..10000 frames and 1..120 fps')
        if not self.import_lock.acquire(blocking=False):
            raise MediaBusyError('Another import is preparing browser assets')
        folder = None
        try:
            if self.stopped.is_set():
                raise ValueError('Editor is stopping')
            identifier = uuid.uuid4().hex
            folder = self.root / identifier
            folder.mkdir(mode=0o700)
            names, hashes, sizes = [], [], []
            extension, total = None, 0
            first_name = Path((uploads[0].filename or 'sequence').replace('\\', '/')).name
            for index, upload in enumerate(uploads):
                suffix = Path(upload.filename or '').suffix.lower()
                if suffix not in IMAGE_EXTENSIONS or suffix not in supported_extensions():
                    raise ValueError('Sequences require supported raster image files')
                if extension is None:
                    extension = suffix
                if suffix != extension:
                    raise ValueError('Sequence files must share one image format/extension')
                name = f'frame{index:08d}{extension}'
                digest, length = hashlib.sha256(), 0
                with (folder / name).open('xb') as target:
                    while block := upload.file.read(1024 * 1024):
                        length += len(block)
                        total += len(block)
                        if self.stopped.is_set() or total > MAX_UPLOAD:
                            raise ValueError('Sequence import cancelled or exceeds 64 GiB')
                        if shutil.disk_usage(self.root).free < len(block) + 512 * 1024**2:
                            raise ValueError('Insufficient local storage for sequence')
                        digest.update(block)
                        target.write(block)
                    target.flush()
                    os.fsync(target.fileno())
                if not length:
                    raise ValueError('An image sequence frame is empty')
                names.append(name)
                hashes.append(digest.hexdigest())
                sizes.append(length)
            atomic_json(folder / 'sequence-input.json', {'frames': names})
            value = self._prepare_worker(folder, '--sequence', str(rate))
            for name, expected in zip(names, hashes, strict=True):
                if file_hash(folder / name) != expected:
                    raise ValueError('Sequence source changed during preparation')
            digest = hashlib.sha256(json.dumps(list(zip(hashes, sizes)), separators=(',', ':')).encode()).hexdigest()
            value.update(id=identifier, name=first_name + f' · {len(names)} frames', bytes=total, sha256=digest,
                         _source=names[0], _frames=names, _frame_hashes=hashes, _frame_sizes=sizes,
                         proxy_url=f'/api/media/{identifier}/proxy', poster_url=f'/api/media/{identifier}/poster')
            value['warnings'].append('Sequence order is the explicit upload order. Its SHA-256 identifies the ordered frame-hash/size manifest.')
            atomic_json(folder / 'media.json', value)
            with self.lock:
                self.records[identifier] = value
            return self.public(value)
        except BaseException:
            if folder is not None and folder.parent == self.root and not folder.is_symlink():
                shutil.rmtree(folder)
            raise
        finally:
            self.import_lock.release()

    def close(self):
        self.stopped.set()
        with self.lock:
            owned = list(self.processes)
        for proc in owned:
            media_pipeline._stop_tree(proc)


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] not in ('--prepare', '--sequence'):
        raise SystemExit('Internal owned media-preparation entry point')
    try:
        if sys.argv[1] == '--prepare':
            prepare_media(Path(sys.argv[2]), sys.argv[3])
        else:
            prepare_sequence(Path(sys.argv[2]), sys.argv[3])
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
