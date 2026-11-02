#!/usr/bin/env python3
"""Opt-in native 8K editor verification, with original synthetic fixtures.

GPL-3.0-or-later; see LICENSE and LICENSE-EXCEPTION. Run with the editor's Python environment, for example:
  python scripts/validate_native_8k.py --device cuda --frames 2 --nvenc --effects
  python scripts/validate_native_8k.py --device mps --frames 24

This is an offline processing/precision test, not a sensor or real-time claim.
Only a fresh output directory is accepted. Frames are generated and decoded
sequentially; no list of full-resolution frames is retained. Source FFV1 files
and all output verification use native 7680 x 4320, never proxy geometry.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT, RATE, AUDIO_RATE, CHANNELS = 7680, 4320, 24, 48000, 2
sys.path.insert(0, str(ROOT / "engine"))


def file_hash(path):
    checksum = hashlib.sha256()
    with Path(path).open("rb") as source:
        while block := source.read(1024 * 1024):
            checksum.update(block)
    return checksum.hexdigest()


def fill_pattern(array, index):
    """Exact original integer fixture; index is global across both sources."""
    import numpy as np
    x = np.arange(WIDTH, dtype=np.int32)[None, :]
    for first in range(0, HEIGHT, 128):
        end = min(first + 128, HEIGHT)
        y = np.arange(first, end, dtype=np.int32)[:, None]
        tile = array[first:end]
        tile[:, :, 0] = ((x * 65535 // (WIDTH - 1)) + index * 257) & 65535
        tile[:, :, 1] = ((y * 65535 // (HEIGHT - 1)) + index * 509) & 65535
        tile[:, :, 2] = (x * 31 + y * 17 + index * 997) & 65535
        checker = y[:, 0] < HEIGHT // 3
        tile[checker, :, 0] = ((x + y[checker] + index) & 1) * 65535
        tile[checker, :, 1] = 10000 + ((x + index) & 1)
        tile[checker, :, 2] = 30000 + ((y[checker] + index) & 1)
        breakpoint = (y[:, 0] >= HEIGHT // 3) & (y[:, 0] < HEIGHT // 2)
        tile[breakpoint, :, 0] = 5290 + ((x + index) % 31)


def generate_video(path, first, count, cumulative):
    import av
    import numpy as np
    expected = []
    started = time.perf_counter()
    array = np.empty((HEIGHT, WIDTH, 3), dtype=np.uint16)
    with av.open(str(path), "w", format="nut") as container:
        stream = container.add_stream("ffv1", rate=RATE)
        stream.width, stream.height, stream.pix_fmt = WIDTH, HEIGHT, "gbrp16le"
        stream.time_base = stream.codec_context.time_base = Fraction(1, RATE)
        stream.codec_context.max_b_frames = 0
        stream.codec_context.thread_count = 2
        stream.codec_context.color_range, stream.codec_context.color_trc = 2, 1
        stream.codec_context.color_primaries, stream.codec_context.colorspace = 1, 0
        stream.options = {"level": "3", "coder": "1", "context": "1", "slicecrc": "1"}
        for index in range(count):
            fill_pattern(array, first + index)
            active = memoryview(array).cast("B")
            expected.append(hashlib.sha256(active).hexdigest())
            cumulative.update(active)
            frame = av.VideoFrame.from_ndarray(array, format="rgb48le")
            frame.pts, frame.time_base, frame.duration = index, Fraction(1, RATE), 1
            for packet in stream.encode(frame):
                container.mux(packet)
            del active, frame
        for packet in stream.encode():
            container.mux(packet)
    del array
    return {"name": path.name, "first_global_frame": first, "frames": count,
            "rgb16_frame_sha256": expected, "file_sha256": file_hash(path),
            "bytes": path.stat().st_size, "generation_seconds": time.perf_counter() - started}


def generate_audio(path, frames):
    """Stereo IEEE float32 WAV; right channel preserves sub-24-bit detail."""
    import numpy as np
    samples = frames * (AUDIO_RATE // RATE)
    size = samples * CHANNELS * 4
    checksum = hashlib.sha256()
    with path.open("xb") as wave:
        wave.write(b"RIFF" + struct.pack("<I", size + 36) + b"WAVEfmt ")
        wave.write(struct.pack("<IHHIIHH", 16, 3, CHANNELS, AUDIO_RATE,
                               AUDIO_RATE * CHANNELS * 4, CHANNELS * 4, 32))
        wave.write(b"data" + struct.pack("<I", size))
        for first in range(0, samples, 4096):
            point = np.arange(first, min(first + 4096, samples), dtype=np.int32)
            data = np.empty((len(point), CHANNELS), dtype="<f4")
            data[:, 0] = np.ldexp(((point % 1024) - 512).astype(np.float32), -14)
            data[:, 1] = np.ldexp(((point % 2048) - 1024).astype(np.float32), -32)
            active = memoryview(data).cast("B")
            checksum.update(active)
            wave.write(active)
    return {"name": path.name, "samples_per_channel": samples, "sample_rate": AUDIO_RATE,
            "channels": CHANNELS, "format": "f32le", "pcm_sha256": checksum.hexdigest(),
            "pcm_bytes": size, "file_sha256": file_hash(path), "bytes": path.stat().st_size}


def decode_video(path, expected=None, *, canonical_yuv=False):
    """Independent row/active-plane hashes, geometry and per-frame CFR checks."""
    import av
    aggregate, hashes, timestamps = hashlib.sha256(), [], []
    with av.open(str(path), options={"protocol_whitelist": "file,pipe"}) as container:
        stream = container.streams.video[0]
        stream.codec_context.thread_count = 1
        if (stream.width, stream.height) != (WIDTH, HEIGHT):
            raise AssertionError("Output was not native 7680 x 4320")
        geometry = {"width": stream.width, "height": stream.height,
                    "codec": stream.codec_context.name, "pixel_format": stream.codec_context.format.name}
        tolerance = stream.time_base / 2 + Fraction(1, 1_000_000)
        for index, frame in enumerate(container.decode(stream)):
            if frame.pts is None:
                raise AssertionError("Decoded frame has no presentation timestamp")
            timestamp = frame.pts * frame.time_base
            if abs(timestamp - Fraction(index, RATE)) > tolerance:
                raise AssertionError(f"Frame {index} violates CFR cadence: {timestamp}")
            if (frame.width, frame.height) != (WIDTH, HEIGHT):
                raise AssertionError("Decoded frame geometry changed")
            checksum = hashlib.sha256()
            if canonical_yuv:
                canonical = frame.reformat(format="yuv420p10le")
                for plane in canonical.planes:
                    data = memoryview(plane)
                    for row in range(plane.height):
                        active = data[row * plane.line_size:row * plane.line_size + plane.width * 2]
                        checksum.update(active)
                        aggregate.update(active)
                del canonical, data, active
            else:
                array = frame.to_ndarray(format="rgb48le")
                active = memoryview(array).cast("B")
                checksum.update(active)
                aggregate.update(active)
                del active, array
            digest = checksum.hexdigest()
            if expected is not None and (index >= len(expected) or digest != expected[index]):
                raise AssertionError(f"Decoded RGB16 samples differ at frame {index}")
            hashes.append(digest)
            timestamps.append(str(timestamp))
            del frame
        if expected is not None and len(hashes) != len(expected):
            raise AssertionError("Decoded frame count differs from expected")
    return {**geometry, "frames": len(hashes), "fps_exact": f"{RATE}/1",
            "timestamps": timestamps, "frame_sha256": hashes,
            "sample_sha256": aggregate.hexdigest(), "canonical_samples": "yuv420p10le" if canonical_yuv else "rgb48le",
            "cfr_verified": True, "exact_expected_rgb16": expected is not None}


def decode_audio(path, expected, ffmpeg, ffprobe):
    metadata = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-protocol_whitelist", "file,pipe",
        "-show_streams", "-of", "json", str(path)]))
    stream = next(s for s in metadata["streams"] if s.get("codec_type") == "audio")
    if (int(stream.get("sample_rate", 0)), stream.get("channels")) != (AUDIO_RATE, CHANNELS):
        raise AssertionError("Mixed audio rate/channel geometry changed")
    checksum, count = hashlib.sha256(), 0
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([ffmpeg, "-hide_banner", "-v", "error", "-xerror",
            "-protocol_whitelist", "file,pipe", "-i", str(path), "-map", "0:a:0", "-c:a", "pcm_f32le",
            "-ar", str(AUDIO_RATE), "-ac", str(CHANNELS), "-f", "f32le", "pipe:1"], stdout=subprocess.PIPE, stderr=errors)
        try:
            while block := process.stdout.read(1024 * 1024):
                checksum.update(block)
                count += len(block)
            if process.wait(timeout=120):
                errors.seek(0)
                raise AssertionError("Independent audio decode failed: " + errors.read().decode(errors="replace")[-2000:])
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()
    if count != expected["pcm_bytes"] or checksum.hexdigest() != expected["pcm_sha256"]:
        raise AssertionError("Decoded float32 PCM samples differ from the exact synthetic audio")
    return {"codec": stream["codec_name"], "sample_rate": AUDIO_RATE, "channels": CHANNELS,
            "samples_per_channel": count // (CHANNELS * 4), "pcm_bytes": count,
            "pcm_sha256": checksum.hexdigest(), "exact_expected_pcm": True}


class ResourceSampler:
    """Optional sampled process-tree RSS / device-wide CUDA usage, not true peaks."""
    def __init__(self, device):
        self.stop = threading.Event()
        self.thread = None
        self.result = {"sampling_interval_seconds": .5,
                       "note": "Sampled maxima can miss transient peaks; GPU usage is device-wide, including other processes."}
        self.ps = None
        try:
            import psutil
            self.ps = psutil.Process()
        except ImportError:
            self.result["process_rss"] = "Unavailable: optional psutil not installed"
        self.smi = shutil.which("nvidia-smi") if device.type == "cuda" else None
        if device.type == "mps":
            self.result["mps_allocator"] = "Not sampled: renderer runs in a separate worker; parent allocator counters would be misleading."
        self.gpu = str(device.index or 0)
        if self.smi:
            import torch
            self.gpu = str(getattr(torch.cuda.get_device_properties(device), "uuid", self.gpu))
            if "-" in self.gpu and not self.gpu.startswith("GPU-"):
                self.gpu = "GPU-" + self.gpu

    def __enter__(self):
        self.thread = threading.Thread(target=self._sample, name="8k-validation-resources", daemon=True)
        self.thread.start()
        return self

    def _sample(self):
        while not self.stop.is_set():
            if self.ps:
                try:
                    processes = [self.ps, *self.ps.children(recursive=True)]
                    amount = sum(p.memory_info().rss for p in processes if p.is_running())
                    self.result["sampled_process_tree_rss_max_bytes"] = max(amount, self.result.get("sampled_process_tree_rss_max_bytes", 0))
                except Exception:
                    pass  # A short-lived decoder can exit between inventory and RSS.
            if self.smi:
                try:
                    raw = subprocess.check_output([self.smi, "-i", self.gpu, "--query-gpu=memory.used", "--format=csv,noheader,nounits"], timeout=5, stderr=subprocess.DEVNULL)
                    amount = int(raw.decode().strip())
                    self.result.setdefault("device_memory_baseline_mib", amount)
                    self.result["sampled_device_memory_used_max_mib"] = max(amount, self.result.get("sampled_device_memory_used_max_mib", 0))
                    self.result["device_memory_samples"] = self.result.get("device_memory_samples", 0) + 1
                    self.result.pop("cuda_memory_sampling", None)
                except (OSError, ValueError, subprocess.SubprocessError):
                    self.result["cuda_memory_sampling"] = "Unavailable from nvidia-smi"
            self.stop.wait(.5)

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=7)


def timeline(frames, first_count):
    return {"schema_version": 1, "name": "Native 8K exact RGB16 two-clip verification", "width": WIDTH,
        "height": HEIGHT, "fps": f"{RATE}/1", "background": "#000000", "color_space": "bt709",
        "audio_sample_rate": AUDIO_RATE, "audio_layout": "stereo", "markers": [], "tracks": [
            {"id": "video", "kind": "video", "clips": [
                {"id": "first", "media_id": "source-a", "duration": first_count / RATE},
                {"id": "second", "media_id": "source-b", "start": first_count / RATE, "duration": (frames - first_count) / RATE}]},
            {"id": "audio", "kind": "audio", "clips": [{"id": "tone", "media_id": "tone", "duration": frames / RATE}]}]}


def progress(percent, message):
    print(f"  {percent * 100:5.1f}% {message}", flush=True)


def run_render(editor, project, records, output, mode, expected, audio_expected, *, yuv=False):
    started = time.perf_counter()
    result = editor.render_project(project, records, output, mode=mode, progress_callback=progress)
    render_seconds = time.perf_counter() - started
    verify_start = time.perf_counter()
    decoded = decode_video(output, expected, canonical_yuv=yuv)
    if yuv:
        if decoded["sample_sha256"] != result.get("yuv10_sha256"):
            raise AssertionError("Independent decoded canonical YUV10 differs from the engine receipt")
    elif decoded["sample_sha256"] != result.get("rgb16_sha256"):
        raise AssertionError("Independent decoded RGB16 differs from the engine receipt")
    if decoded["frames"] != sum(round(c["duration"] * RATE) for c in project["tracks"][0]["clips"]):
        raise AssertionError("Timeline output frame count changed")
    audio = decode_audio(output, audio_expected, editor.media.FFMPEG, editor.media.FFPROBE)
    return {"name": output.name, "bytes": output.stat().st_size, "file_sha256": file_hash(output),
            "render_and_engine_verify_seconds": render_seconds,
            "independent_verify_seconds": time.perf_counter() - verify_start,
            "engine_receipt": result, "independent_video": decoded, "independent_audio": audio,
            "precision_scope": "Target YUV10 codec-lossless; RGB-to-YUV conversion is not lossless RGB" if yuv else "Exact decoded RGB16 source identity / processed RGB16 master"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--frames", type=int, default=2, help="Total sequential frames across two clips, 2..240; Mac endurance example:24")
    parser.add_argument("--device", choices=("cuda", "mps", "cpu"), default="cuda")
    parser.add_argument("--nvenc", action="store_true", help="Also verify target-YUV10 codec-lossless HEVC on NVIDIA")
    parser.add_argument("--effects", action="store_true", help="Also render two native8K frames with lens, overlay and title")
    parser.add_argument("--output", type=Path, help="New directory only; defaults to a fresh out/native-8k-* directory")
    args = parser.parse_args(argv)
    if not 2 <= args.frames <= 240:
        parser.error("--frames must be 2..240")
    if args.nvenc and args.device != "cuda":
        parser.error("--nvenc requires --device cuda")
    os.environ["STUDIO_DEVICE"] = args.device
    import editor_pipeline as editor
    import av
    import numpy as np
    import torch
    from project_model import validate_project
    from runtime_backend import select_device, backend_info
    device = select_device(args.device)
    output = (args.output or ROOT / "out" / ("native-8k-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))).resolve()
    output.mkdir(parents=True, exist_ok=False)
    receipt = {"schema_version": 1, "status": "running", "started_utc": datetime.now(timezone.utc).isoformat(),
               "command": sys.argv, "backend": backend_info(device), "width": WIDTH, "height": HEIGHT,
               "frames": args.frames, "fps_exact": f"{RATE}/1", "checks": {},
               "runtime": {"python": platform.python_version(), "system": platform.system(), "machine": platform.machine(),
                           "torch": torch.__version__, "pyav": av.__version__, "numpy": np.__version__},
               "code_sha256": {name: file_hash(ROOT / name) for name in (
                   "scripts/validate_native_8k.py", "engine/editor_pipeline.py", "engine/project_model.py",
                   "engine/media_pipeline.py", "engine/studio_engine.py", "engine/runtime_backend.py", "engine/format_catalog.py")},
               "scope": "Synthetic offline native8K editing/export proof; no sensor, capture, real-time, or universal codec claim.",
               "fixture": "Original integer RGB16 gradients; 0/65535 pixel checker;10000/10001 green and30000/30001 blue LSBs;5290..5320 BT709 breakpoint values."}
    started = time.perf_counter()
    sources, before = [], {}
    try:
        with ResourceSampler(device) as sampler:
            cumulative, first_count = hashlib.sha256(), (args.frames + 1) // 2
            for name, first, count in (("source-a", 0, first_count), ("source-b", first_count, args.frames - first_count)):
                print(f"Generating {name}: {count} native8K RGB16 frame(s)", flush=True)
                sources.append(generate_video(output / (name + ".nut"), first, count, cumulative))
            audio_expected = generate_audio(output / "source-tone.wav", args.frames)
            receipt["sources"], receipt["audio_source"] = sources, audio_expected
            before = {s["name"]: s["file_sha256"] for s in [*sources, audio_expected]}
            expected = [value for source in sources for value in source["rgb16_frame_sha256"]]
            records = {}
            for source in sources:
                path = output / source["name"]
                public = editor.media.inspect_media(path)
                public.update(kind="video", duration=source["frames"] / RATE)
                records[path.stem] = {"path": str(path), "public": public}
                source["independent_decode"] = decode_video(path, source["rgb16_frame_sha256"])
            records["tone"] = {"path": str(output / audio_expected["name"]), "public": {
                "kind": "audio", "duration": args.frames / RATE, "audio_streams": 1}}
            project = validate_project(timeline(args.frames, first_count), records)
            (output / "timeline.json").write_text(json.dumps(project, indent=2), encoding="utf-8")
            print("Exporting native8K RGB16 FFV1 master", flush=True)
            receipt["checks"]["rgb16_master"] = run_render(editor, project, records, output / "master-rgb16.mkv", "lossless", expected, audio_expected)
            if receipt["checks"]["rgb16_master"]["independent_video"]["sample_sha256"] != cumulative.hexdigest():
                raise AssertionError("Master aggregate differs from original generated RGB16 bytes")
            if args.nvenc:
                print("Exporting native8K NVIDIA target-YUV10 codec-lossless HEVC", flush=True)
                receipt["checks"]["nvenc_yuv10"] = run_render(editor, project, records, output / "master-nvenc-yuv10.mkv", "nvenc-lossless", None, audio_expected, yuv=True)
            if args.effects:
                effect = timeline(2, 1)
                effect["name"] = "Native8K two-frame multitrack title and lens proof"
                effect["tracks"][0]["clips"][0]["lens"] = {"amount": .65, "flare": .75, "bloom": .15, "grain": .08}
                effect["tracks"].insert(1, {"id": "overlay", "kind": "video", "clips": [
                    {"id": "overlay-a", "media_id": "source-b", "duration": 1 / RATE, "scale": .4,
                     "x": 1500, "opacity": .55, "volume": 0}]})
                effect["tracks"].append({"id": "title", "kind": "title", "clips": [
                    {"id": "title-a", "duration": 2 / RATE, "text": "NATIVE 8K / RGB16 MASTER", "font_size": 180,
                     "y": 1300, "box_opacity": .5}]})
                effect = validate_project(effect, records)
                short_audio = {**audio_expected, "samples_per_channel": 4000, "pcm_bytes": 32000}
                if args.frames != 2:
                    short_path = output / "expected-effects-tone.wav"
                    short_audio = generate_audio(short_path, 2)
                    before[short_path.name] = short_audio["file_sha256"]
                print("Exporting separate native8K lens / overlay / title proof", flush=True)
                effect_result = run_render(editor, effect, records, output / "effects-rgb16.mkv", "lossless", None, short_audio)
                if effect_result["independent_video"]["frame_sha256"][0] == expected[0]:
                    raise AssertionError("Lens/multitrack/title proof unexpectedly equals the untreated source")
                effect_result["changed_from_untreated_source"] = True
                receipt["checks"]["effects_multitrack"] = effect_result
            receipt["resources"] = sampler.result
        receipt["status"] = "passed"
    except BaseException as error:
        receipt["status"], receipt["error"] = "failed", f"{type(error).__name__}: {error}"
        raise
    finally:
        if "sampler" in locals():
            receipt["resources"] = sampler.result
        after = {name: file_hash(output / name) for name in before}
        receipt["source_file_sha256_before"], receipt["source_file_sha256_after"] = before, after
        receipt["sources_unchanged"] = before == after
        if before and before != after:
            receipt["status"] = "failed"
            receipt["source_integrity_error"] = "At least one owned source file changed"
        receipt["elapsed_wall_seconds"] = time.perf_counter() - started
        receipt["finished_utc"] = datetime.now(timezone.utc).isoformat()
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        print(f"Receipt: {output / 'receipt.json'}", flush=True)
    if receipt["status"] != "passed":
        raise AssertionError("Validation failed; see receipt")
    print(f"PASS: {args.frames} native8K frames on {device}; sources unchanged. Offline timings only.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
