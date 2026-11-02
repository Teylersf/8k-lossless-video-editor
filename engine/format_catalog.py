"""Installed codec/format catalogue and explicit professional export profiles.

GPL-2.0-only. An installed encoder is not a successful hardware capability probe.
The export worker must open/encode/verify the requested geometry before claiming
hardware or lossless success. Container names never imply codec losslessness.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from functools import lru_cache

import av

BLOCKED_DEMUXERS = frozenset({'concat', 'hls', 'dash', 'sdp', 'rtsp', 'sap', 'rtp',
                            'avisynth', 'vapoursynth', 'lavfi', 'imf', 'ffmetadata', 'tty'})

_PROFILES = [
    dict(id='lossless', label='Lossless RGB16 master', category='Lossless', extension='.mkv', encoder='ffv1', codec='ffv1', pixel_format='gbrp16le', options={'level':'3','coder':'1','context':'1'}, audio_codec='pcm_f32le', container='matroska', lossless=True, rgb_lossless=True),
    dict(id='nvenc-lossless', label='NVIDIA HEVC codec-lossless', category='Lossless', extension='.mkv', encoder='hevc_nvenc', codec='hevc', pixel_format='p010le', options={'preset':'p3','tune':'lossless','rc':'constqp','qp':'0'}, audio_codec='pcm_f32le', container='matroska', lossless=True, rgb_lossless=False, hardware='nvidia'),
    dict(id='copy', label='Original packet copy', category='Lossless', extension=None, encoder=None, codec=None, pixel_format=None, options={}, audio_codec='copy', container=None, lossless=True, rgb_lossless=False),
    dict(id='hevc', label='HEVC / H.265 delivery', category='Delivery', extension='.mp4', encoder='libx265', codec='hevc', pixel_format='yuv420p10le', options={'preset':'medium','crf':'16','x265-params':'log-level=error:pools=4:frame-threads=2'}, audio_codec='aac', container='mp4', lossless=False, rgb_lossless=False, hardware='auto'),
    dict(id='h264', label='H.264 / AVC delivery', category='Delivery', extension='.mp4', encoder='libx264', codec='h264', pixel_format='yuv420p', options={'preset':'medium','crf':'16'}, audio_codec='aac', container='mp4', lossless=False, rgb_lossless=False),
    dict(id='av1', label='AV1 delivery', category='Delivery', extension='.mkv', encoder='libsvtav1', codec='av1', pixel_format='yuv420p10le', options={'preset':'8','crf':'16','svtav1-params':'lp=4'}, audio_codec='flac', container='matroska', lossless=False, rgb_lossless=False),
    dict(id='prores', label='ProRes 422 HQ', category='Film / intermediate', extension='.mov', encoder='prores_ks', codec='prores', pixel_format='yuv422p10le', options={'profile':'3','qscale':'1'}, audio_codec='pcm_s24le', container='mov', lossless=False, rgb_lossless=False),
    dict(id='prores-proxy', label='ProRes Proxy', category='Film / intermediate', extension='.mov', encoder='prores_ks', codec='prores', pixel_format='yuv422p10le', options={'profile':'0'}, audio_codec='pcm_s24le', container='mov', lossless=False, rgb_lossless=False),
    dict(id='prores-lt', label='ProRes 422 LT', category='Film / intermediate', extension='.mov', encoder='prores_ks', codec='prores', pixel_format='yuv422p10le', options={'profile':'1'}, audio_codec='pcm_s24le', container='mov', lossless=False, rgb_lossless=False),
    dict(id='prores-422', label='ProRes 422', category='Film / intermediate', extension='.mov', encoder='prores_ks', codec='prores', pixel_format='yuv422p10le', options={'profile':'2'}, audio_codec='pcm_s24le', container='mov', lossless=False, rgb_lossless=False),
    dict(id='prores-4444', label='ProRes 4444', category='Film / intermediate', extension='.mov', encoder='prores_ks', codec='prores', pixel_format='yuv444p10le', options={'profile':'4','qscale':'1'}, audio_codec='pcm_s24le', container='mov', lossless=False, rgb_lossless=False),
    dict(id='prores-xq', label='ProRes 4444 XQ', category='Film / intermediate', extension='.mov', encoder='prores_ks', codec='prores', pixel_format='yuv444p10le', options={'profile':'5','qscale':'1'}, audio_codec='pcm_s24le', container='mov', lossless=False, rgb_lossless=False),
    dict(id='dnxhr', label='DNxHR HQX 10-bit', category='Broadcast / intermediate', extension='.mov', encoder='dnxhd', codec='dnxhd', pixel_format='yuv422p10le', options={'profile':'dnxhr_hqx'}, audio_codec='pcm_s24le', container='mov', lossless=False, rgb_lossless=False),
    dict(id='dnxhr-444', label='DNxHR 444 10-bit', category='Broadcast / intermediate', extension='.mov', encoder='dnxhd', codec='dnxhd', pixel_format='gbrp10le', options={'profile':'dnxhr_444'}, audio_codec='pcm_s24le', container='mov', lossless=False, rgb_lossless=False),
    dict(id='dnxhd', label='DNxHD 1080p broadcast', category='Broadcast / intermediate', extension='.mov', encoder='dnxhd', codec='dnxhd', pixel_format='yuv422p10le', options={'profile':'dnxhd'}, audio_codec='pcm_s24le', container='mov', lossless=False, rgb_lossless=False),
    dict(id='mxf', label='MXF OP1a / DNxHR HQX', category='Broadcast', extension='.mxf', encoder='dnxhd', codec='dnxhd', pixel_format='yuv422p10le', options={'profile':'dnxhr_hqx'}, audio_codec='pcm_s24le', container='mxf', lossless=False, rgb_lossless=False),
    dict(id='mxf-mpeg2', label='MXF / MPEG-2 4:2:2 50 Mb/s', category='Broadcast', extension='.mxf', encoder='mpeg2video', codec='mpeg2video', pixel_format='yuv422p', options={'b':'50000000','minrate':'50000000','maxrate':'50000000','bufsize':'17825792','g':'12'}, audio_codec='pcm_s24le', container='mxf', lossless=False, rgb_lossless=False),
    dict(id='cineform', label='GoPro CineForm RGB 12-bit', category='Film / intermediate', extension='.mov', encoder='cfhd', codec='cfhd', pixel_format='gbrp12le', options={}, audio_codec='pcm_s24le', container='mov', lossless=False, rgb_lossless=False),
    dict(id='jpeg2000', label='JPEG 2000 reversible RGB16', category='Film / intermediate', extension='.mov', encoder='jpeg2000', codec='jpeg2000', pixel_format='rgb48le', options={'pred':'1','format':'j2k','layer_rates':'0'}, audio_codec='pcm_f32le', container='mov', lossless=True, rgb_lossless=True),
    dict(id='uncompressed', label='Uncompressed RGB16', category='Lossless', extension='.nut', encoder='rawvideo', codec='rawvideo', pixel_format='rgb48le', options={}, audio_codec='pcm_f32le', container='nut', lossless=True, rgb_lossless=True),
    dict(id='png-sequence', label='PNG 16-bit image sequence', category='Image sequence', extension='.zip', encoder='png', codec='png', pixel_format='rgb48be', options={}, audio_codec='pcm_f32le', container='zip', lossless=True, rgb_lossless=True, sequence='.png'),
    dict(id='tiff-sequence', label='TIFF 16-bit image sequence', category='Image sequence', extension='.zip', encoder='tiff', codec='tiff', pixel_format='rgb48le', options={'compression_algo':'deflate'}, audio_codec='pcm_f32le', container='zip', lossless=True, rgb_lossless=True, sequence='.tiff'),
    dict(id='dpx-sequence', label='DPX 16-bit image sequence', category='Image sequence', extension='.zip', encoder='dpx', codec='dpx', pixel_format='rgb48be', options={}, audio_codec='pcm_f32le', container='zip', lossless=True, rgb_lossless=True, sequence='.dpx'),
    dict(id='exr-sequence', label='OpenEXR float image sequence', category='Image sequence', extension='.zip', encoder='exr', codec='exr', pixel_format='gbrpf32le', options={'compression':'zip1'}, audio_codec='pcm_f32le', container='zip', lossless=True, rgb_lossless=False, sequence='.exr', notes='Linear float export requires a supported explicit color interpretation; no camera RAW decoder is implied.'),
]

_FORMATS = [
    ('everyday', 'MP4 / M4V / MOV — H.264 / HEVC', ('h264','hevc'), ('.mp4','.m4v','.mov','.3gp'), 'Video'),
    ('everyday', 'Matroska / WebM — AVC, HEVC, VP8/9, AV1, FFV1', ('h264','hevc','vp8','vp9','av1','ffv1'), ('.mkv','.webm'), 'Video'),
    ('everyday', 'AVI / MPEG-4 / MJPEG / uncompressed', ('mpeg4','mjpeg','rawvideo'), ('.avi',), 'Video'),
    ('film', 'NUT / lossless FFV1 / uncompressed RGB', ('ffv1','rawvideo'), ('.nut',), 'Video'),
    ('everyday', 'WMV / ASF / FLV / MPEG program streams', ('wmv3','vc1','flv','mpeg2video'), ('.wmv','.asf','.flv','.mpg','.mpeg','.vob'), 'Video'),
    ('broadcast', 'MPEG-TS / MTS / M2TS / XDCAM / AVC-Intra', ('mpeg2video','h264','hevc'), ('.ts','.mts','.m2ts','.m2t'), 'Video'),
    ('broadcast', 'MXF OP1a / OP-Atom', ('dnxhd','prores','mpeg2video','h264','jpeg2000'), ('.mxf',), 'Video'),
    ('broadcast', 'GXF', ('mpeg2video','dvvideo'), ('.gxf',), 'Video'),
    ('film', 'Apple ProRes Proxy / LT / 422 / HQ / 4444 / XQ', ('prores',), ('.mov','.mxf'), 'Video'),
    ('film', 'Avid DNxHD / DNxHR', ('dnxhd',), ('.mov','.mxf'), 'Video'),
    ('film', 'GoPro CineForm', ('cfhd',), ('.mov','.avi'), 'Video'),
    ('film', 'JPEG 2000 / MJ2', ('jpeg2000',), ('.mov','.mxf','.mj2','.j2k','.jp2'), 'Video / image'),
    ('image', 'DPX 10/12/16-bit', ('dpx',), ('.dpx',), 'Still / sequence'),
    ('image', 'OpenEXR float', ('exr',), ('.exr',), 'Still / sequence'),
    ('image', 'TIFF / PNG 8/16-bit', ('tiff','png'), ('.tif','.tiff','.png'), 'Still / sequence'),
    ('image', 'JPEG / JPEG-LS / JPEG XL / BMP / TGA / WebP', ('mjpeg','jpegls','jpegxl','bmp','targa','webp'), ('.jpg','.jpeg','.jls','.jxl','.bmp','.tga','.webp'), 'Still / sequence'),
    ('audio', 'WAV / BWF / AIFF / RF64 / Wave64', ('pcm_s16le','pcm_s24le','pcm_f32le','pcm_s16be'), ('.wav','.bwf','.aiff','.aif','.w64'), 'Audio'),
    ('audio', 'FLAC / ALAC / MP3 / AAC / Opus / Vorbis', ('flac','alac','mp3','aac','opus','vorbis'), ('.flac','.m4a','.mp3','.aac','.opus','.ogg'), 'Audio'),
]


@lru_cache(maxsize=512)
def _codec_available(name, direction):
    try:
        av.codec.Codec(name, direction)
        return True
    except (ValueError, RuntimeError, av.error.FFmpegError):
        return False


def profiles():
    result = []
    for original in _PROFILES:
        p = deepcopy(original)
        p['available'] = p['encoder'] is None or _codec_available(p['encoder'], 'w')
        p['hardware_verified'] = False
        p['availability_scope'] = 'encoder installed; actual project capability is checked during export'
        p['reason'] = '' if p['available'] else f"This PyAV build does not include {p['encoder']}"
        result.append(p)
    return result


def get_profile(mode, project=None):
    """Return a fresh encoder plan; geometry restrictions are never implicit."""
    p = next((p for p in profiles() if p['id'] == mode), None)
    if p is None:
        raise ValueError(f'Unknown export profile: {mode}')
    if not p['available']:
        raise ValueError(p['reason'])
    if project:
        width, height, rate = int(project['width']), int(project['height']), Fraction(str(project['fps']))
        if mode == 'uncompressed' and project.get('color_space', 'bt709') != 'bt709':
            raise ValueError('Uncompressed NUT cannot retain HDR colorimetry in this build; choose a Matroska RGB16 master.')
        if mode == 'dnxhd':
            rates = {Fraction(24):175000000, Fraction(24000,1001):175000000,
                     Fraction(25):185000000, Fraction(30):220000000, Fraction(30000,1001):220000000}
            if (width,height) != (1920,1080) or rate not in rates:
                raise ValueError('This DNxHD profile requires 1920×1080 at 23.976/24/25/29.97/30 fps; use DNxHR for other rasters or 8K.')
            p['options']['b'] = str(rates[rate])
        if mode == 'mxf-mpeg2' and (width,height) not in {(1920,1080),(1280,720)}:
            raise ValueError('MPEG-2 broadcast MXF requires a supported HD raster; choose DNxHR/MXF or another profile for 8K.')
        if mode not in {'lossless','copy','uncompressed','png-sequence','tiff-sequence','dpx-sequence','exr-sequence','jpeg2000'} and (width%2 or height%2):
            raise ValueError('This chroma-subsampled profile requires even dimensions; RGB lossless masters support odd dimensions.')
    return p


def supported_extensions():
    return tuple(sorted({ext for _,_,_,extensions,_ in _FORMATS for ext in extensions}))


def safe_demuxer_names():
    """Installed format names minus scripting/playlist/linked-package readers.

    Callers must additionally restrict protocols to file,pipe and reject detected
    blocked demuxers. A list alone is not a security or successful-import proof.
    """
    return tuple(sorted(name for name in av.formats_available if name not in BLOCKED_DEMUXERS))


def capabilities():
    formats = []
    for category,label,codecs,extensions,kind in _FORMATS:
        installed = {name:_codec_available(name,'r') for name in codecs}
        formats.append({'id':label.lower().replace(' ','-'), 'category':category,'label':label,
                        'kind':kind,'extensions':list(extensions),'decoders':installed,
                        'import_available':any(installed.values()),
                        'scope':'Installed decoder; actual file, raster, metadata and compression variant are checked on import.'})
    for ident,label,reason in (
        ('braw','Blackmagic RAW (.braw)','Requires the Blackmagic RAW SDK adapter; not decoded by the bundled PyAV path.'),
        ('r3d','RED R3D','Requires a vendor SDK adapter; not decoded by this build.'),
        ('dcp-imf','DCP / IMF packages','Individual supported MXF/JPEG 2000 assets can be imported; CPL package assembly, encryption and mastering are not implemented.'),
        ('camera-raw','ARRIRAW / Canon Cinema RAW / ProRes RAW','Not implied by ProRes or rawvideo support; requires the relevant decoder/SDK adapter.'),
    ):
        formats.append({'id':ident,'category':'SDK / package','label':label,'import_available':False,'reason':reason,'decoders':{},'extensions':[]})
    return {'schema_version':1,'formats':formats,'export_profiles':profiles(),
            'extensions':list(supported_extensions()),'max_dimension':8192,'max_pixels':35651584,
            'notes':['Container support does not imply every codec variant is supported.',
                     'Only packet copy preserves original compressed packets. Rendered RGB16 lossless masters preserve the processed samples.',
                     'Lossy delivery/intermediate codecs are never labeled mathematically lossless.',
                     'Hardware availability is verified at the requested format/geometry during export.'],
            'references':{'ffmpeg':'https://ffmpeg.org/general.html','braw':'https://www.blackmagicdesign.com/developer/products/braw/sdk-and-software'}}
