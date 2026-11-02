"""Independently verify an exported DPX16 cinema master. GPL-3.0-or-later.

See LICENSE-EXCEPTION for the application's additional NVIDIA permission.
Only one decoded frame is retained at a time. No source/output media is changed.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import time
import zipfile

import av


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--receipt', required=True, type=Path)
    args = parser.parse_args()
    if args.receipt.exists():
        raise FileExistsError('Use a new receipt filename')
    started = time.perf_counter()
    digest = hashlib.sha256()
    hashes = []
    with zipfile.ZipFile(args.archive) as archive:
        manifest = json.loads(archive.read('timeline.json'))
        names = sorted(n for n in archive.namelist() if n.startswith('frames/') and n.endswith('.dpx'))
        if len(names) != manifest['frame_count']:
            raise ValueError('Image count does not match the timeline manifest')
        for index, name in enumerate(names):
            with archive.open(name) as stream:
                data = stream.read()
            if data[:4] not in (b'SDPX', b'XPDS') or data[801:804] != bytes((6, 6, 16)):
                raise ValueError('Expected DPX16 with Rec.709 transfer and primaries')
            with av.open(io.BytesIO(data)) as source:
                frame = next(source.decode(video=0))
                if (frame.width, frame.height) != (manifest['project']['width'], manifest['project']['height']):
                    raise ValueError('Unexpected decoded image raster')
                if frame.color_trc != 1 or frame.color_primaries != 1:
                    raise ValueError('Independent decoder did not recover Rec.709 metadata')
                samples = frame.to_ndarray(format='rgb48le').tobytes()
                digest.update(samples)
                hashes.append(hashlib.sha256(samples).hexdigest())
            del data, samples, frame
            print(f'Verified image {index+1}/{len(names)}', flush=True)
        if digest.hexdigest() != manifest['sample_sha256']:
            raise ValueError('Independent decoded RGB16 does not match the rendered-sample manifest')
        if archive.testzip() is not None:
            raise ValueError('ZIP CRC check failed')
        audio_bytes = archive.getinfo('mix.wav').file_size
    report = dict(status='passed', archive=args.archive.name, archive_bytes=args.archive.stat().st_size,
                  width=manifest['project']['width'], height=manifest['project']['height'],
                  frames=len(names), fps_exact=manifest['fps'], mode='dpx-sequence',
                  pixel_format='rgb48be', bit_depth=16, transfer='bt709', primaries='bt709',
                  independent_decoded_rgb16_sha256=digest.hexdigest(), frame_sha256=hashes,
                  manifest_sample_hash_matches=True, zip_crc_verified=True, mixed_audio_bytes=audio_bytes,
                  archive_read_only=True, verification_seconds=time.perf_counter()-started,
                  scope='Independent decoded sample and metadata verification of a processed DPX16 sequence; does not assert source-camera bit depth or native motion capture.')
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k != 'frame_sha256'}, indent=2))


if __name__ == '__main__':
    main()
