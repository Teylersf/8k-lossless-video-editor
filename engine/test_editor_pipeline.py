"""Real small timeline renders and independent decoded-sample assertions."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest import mock
from fractions import Fraction

import editor_pipeline as editor
from project_model import validate_project, project_duration, animated


def project(clips, **extra):
    result = dict(schema_version=1, name="Test", width=160, height=96, fps="8/1", background="#000000",
                  color_space="bt709", tracks=[dict(id="video", kind="video", clips=clips)])
    result.update(extra)
    return result


class ModelTests(unittest.TestCase):
    def test_pq_hlg_neutral_white_uses_explicit_physical_reference(self):
        import math
        import torch
        p = .01 ** (2610 / 16384)
        pq100 = ((3424 / 4096 + (2413 / 128) * p) / (1 + (2392 / 128) * p)) ** (2523 / 32)
        scene = .1 ** (1 / 1.2)
        hlg100 = .17883277 * math.log(12 * scene - .28466892) + .55991073
        with tempfile.TemporaryDirectory() as folder:
            hlg_project = validate_project(project([], color_space="bt2020-hlg"))
            compositor = editor._Compositor(hlg_project, {}, torch.device("cpu"), Path(folder))
            actual = compositor._linear(torch.full((3, 2, 2), pq100), "pq", "bt2020")
            torch.testing.assert_close(actual, torch.full_like(actual, scene), rtol=1e-4, atol=1e-6)
            pq_project = validate_project(project([], color_space="bt2020-pq"))
            compositor = editor._Compositor(pq_project, {}, torch.device("cpu"), Path(folder))
            actual = compositor._linear(torch.full((3, 2, 2), hlg100), "hlg", "bt2020")
            torch.testing.assert_close(actual, torch.full_like(actual, .01), rtol=1e-4, atol=1e-6)
    def test_normalization_save_load_and_no_input_mutation(self):
        raw = project([dict(id="a", media_id="red", duration=.5)])
        normalized = validate_project(raw)
        self.assertNotIn("opacity", raw["tracks"][0]["clips"][0])
        self.assertEqual(validate_project(json.loads(json.dumps(normalized))), normalized)
        self.assertEqual(project_duration(normalized), .5)

    def test_validation_does_not_ignore_unknown_or_nonfinite_settings(self):
        for clip in (dict(id="a", media_id="red", duration=.5, unknown=True),
                     dict(id="a", media_id="red", duration=float("nan")),
                     dict(id="a", media_id="red", duration=.5, crop=dict(left=.8))):
            with self.subTest(clip=clip), self.assertRaises(ValueError):
                validate_project(project([clip]))
        title_project = project([])
        title_project["tracks"].append(dict(id="title", kind="title", clips=[dict(id="t", text="Title", input_transfer="pq")]))
        with self.assertRaisesRegex(ValueError, "Title colors"):
            validate_project(title_project)

    def test_keyframes_are_clip_local_and_linear(self):
        clip = validate_project(project([dict(id="a", media_id="red", duration=1,
            keyframes={"opacity": [dict(time=0, value=0), dict(time=1, value=1)]})]))["tracks"][0]["clips"][0]
        self.assertEqual(animated(clip, "opacity", .25), .25)

    def test_duplicate_ids_and_missing_media_rejected(self):
        with self.assertRaises(ValueError):
            validate_project(project([dict(id="x", media_id="a"), dict(id="x", media_id="a")]))
        with self.assertRaisesRegex(ValueError, "Offline"):
            validate_project(project([dict(id="x", media_id="a")]), {})


class TimelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import av
        import numpy as np
        cls.environment = mock.patch.dict(os.environ, {"STUDIO_DEVICE": "cpu"})
        cls.environment.start()
        cls.temporary = tempfile.TemporaryDirectory(prefix="editor-real-test-")
        cls.folder = Path(cls.temporary.name)
        cls.media = {}
        for name, color in (("red", (60000, 1000, 1000)), ("blue", (1000, 1000, 60000))):
            path = cls.folder / (name + ".mkv")
            with av.open(str(path), "w", format="matroska") as container:
                stream = container.add_stream("ffv1", rate=8)
                stream.width, stream.height, stream.pix_fmt = 160, 96, "gbrp16le"
                stream.time_base = stream.codec_context.time_base = Fraction(1, 8)
                stream.codec_context.max_b_frames = 0
                stream.codec_context.color_range = 2
                stream.codec_context.color_trc = 1
                stream.codec_context.color_primaries = 1
                stream.codec_context.colorspace = 0
                for index in range(4):
                    array = np.empty((96, 160, 3), dtype=np.uint16)
                    array[:] = color
                    # A changing green component makes trim/reverse/speed testable.
                    array[:, :, 1] = 2000 + index * 8000
                    frame = av.VideoFrame.from_ndarray(array, format="rgb48le")
                    frame.pts, frame.time_base, frame.duration = index, Fraction(1, 8), 1
                    for packet in stream.encode(frame):
                        container.mux(packet)
                for packet in stream.encode():
                    container.mux(packet)
            public = editor.media.inspect_media(path)
            public.update(id=name, kind="video", duration=.5)
            cls.media[name] = dict(path=str(path), public=public)
        audio = cls.folder / "tone.wav"
        subprocess.run([editor.media.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                        "-t", "0.5", "-c:a", "pcm_s24le", str(audio)], check=True)
        cls.media["tone"] = dict(path=str(audio), public=dict(id="tone", kind="audio", duration=.5, audio_streams=1))
        cls.hashes = {key: editor._file_hash(record["path"]) for key, record in cls.media.items()}

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()
        cls.environment.stop()

    def output(self, suffix=".mkv"):
        return self.folder / (self.id().split(".")[-1] + suffix)

    def decode(self, path):
        import av
        with av.open(str(path)) as container:
            return [frame.to_ndarray(format="rgb48le") for frame in container.decode(video=0)]

    def assert_sources_unchanged(self):
        self.assertEqual({key: editor._file_hash(record["path"]) for key, record in self.media.items()}, self.hashes)

    def test_trim_sequence_pcm_and_independent_rgb16_hash(self):
        timeline = project([dict(id="a", media_id="red", **{"in": .125}, duration=.25),
                            dict(id="b", media_id="blue", start=.25, duration=.25)])
        timeline["tracks"].append(dict(id="audio", kind="audio", clips=[dict(id="tone", media_id="tone", duration=.5, volume=.5)]))
        output = self.output()
        result = editor.render_project(timeline, self.media, output)
        frames = self.decode(output)
        self.assertEqual(result["frames"], 4)
        self.assertEqual(result["pixel_format"], "gbrp16le")
        self.assertIn("RGB16 SHA256", result["verification"])
        self.assertEqual(hashlib.sha256(b"".join(frame.tobytes() for frame in frames)).hexdigest(), result["rgb16_sha256"])
        self.assertGreater(int(frames[0][48, 80, 0]), 58000)
        self.assertGreater(int(frames[2][48, 80, 2]), 58000)
        self.assertGreater(int(frames[0][48, 80, 1]), 9000)
        digest, count = editor._pcm_hash(output)
        self.assertEqual(count, 24000 * 8)
        self.assertNotEqual(digest, hashlib.sha256(bytes(count)).hexdigest())
        self.assert_sources_unchanged()

    def test_multitrack_overlap_opacity_keyframes_and_title(self):
        timeline = project([dict(id="a", media_id="red", duration=.5)])
        timeline["tracks"] += [dict(id="overlay", kind="video", clips=[dict(id="b", media_id="blue", duration=.5,
            keyframes={"opacity": [dict(time=0, value=0), dict(time=.5, value=1)]})]),
            dict(id="titles", kind="title", clips=[dict(id="title", duration=.25, text="8K", font_size=24, y=25)])]
        output = self.output()
        editor.render_project(timeline, self.media, output)
        frames = self.decode(output)
        self.assertGreater(int(frames[0][20, 80, 0]), 58000)
        self.assertLess(int(frames[0][20, 80, 2]), 5000)
        self.assertGreater(int(frames[2][20, 80, 0]), 40000)
        self.assertGreater(int(frames[2][20, 80, 2]), 40000)
        def to_linear(value):
            value /= 65535
            return value / 4.5 if value < .081 else ((value + .099) / 1.099) ** (1 / .45)
        midpoint = (to_linear(60000) + to_linear(1000)) / 2
        expected = round((1.099 * midpoint ** .45 - .099) * 65535)
        self.assertLessEqual(abs(int(frames[2][20, 80, 0]) - expected), 2)
        self.assertLessEqual(abs(int(frames[2][20, 80, 2]) - expected), 2)
        white = (frames[0].min(axis=2) > 60000).sum()
        self.assertGreater(int(white), 10)

    def test_identity_preserves_all_high_frequency_rgb16_samples(self):
        import av
        import numpy as np
        source = self.output("-precision-source.mkv")
        y, x = np.indices((96, 160))
        array = np.empty((96, 160, 3), dtype=np.uint16)
        array[:, :, 0] = np.where((x + y) % 2, 65535, 0)
        array[:, :, 1] = 10000 + x % 2
        array[:, :, 2] = 5290 + y % 30  # Include the BT.709 piecewise breakpoint.
        with av.open(str(source), "w", format="matroska") as container:
            stream = container.add_stream("ffv1", rate=8)
            stream.width, stream.height, stream.pix_fmt = 160, 96, "gbrp16le"
            stream.time_base = stream.codec_context.time_base = Fraction(1, 8)
            stream.codec_context.max_b_frames = 0
            stream.codec_context.color_range, stream.codec_context.color_trc = 2, 1
            stream.codec_context.color_primaries, stream.codec_context.colorspace = 1, 0
            for index in range(2):
                frame = av.VideoFrame.from_ndarray(array, format="rgb48le")
                frame.pts, frame.time_base, frame.duration = index, Fraction(1, 8), 1
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        public = editor.media.inspect_media(source)
        public.update(kind="video", duration=.25)
        records = {**self.media, "precision": dict(path=str(source), public=public)}
        output = self.output()
        editor.render_project(project([dict(id="precise", media_id="precision", duration=.25)]), records, output)
        decoded = self.decode(output)
        self.assertEqual(len(decoded), 2)
        for frame in decoded:
            np.testing.assert_array_equal(frame, array)

    def test_float32_audio_mix_preserves_sub24bit_samples_and_51_channels(self):
        import numpy as np
        rate, samples = 48000, 24000
        point = np.arange(samples, dtype=np.float32)[:, None]
        frequency = np.arange(6, dtype=np.float32)[None] * 90 + 120
        first = np.sin(point * frequency * np.float32(2 * np.pi / rate)).astype(np.float32) * np.float32(1e-10)
        second = np.cos(point * frequency * np.float32(2 * np.pi / rate)).astype(np.float32) * np.float32(2e-10)
        records = dict(self.media)
        for name, data in (("first", first), ("second", second)):
            raw, wave = self.output("-" + name + ".f32"), self.output("-" + name + ".wav")
            raw.write_bytes(data.tobytes())
            subprocess.run([editor.media.FFMPEG, "-v", "error", "-f", "f32le", "-ar", str(rate), "-ac", "6",
                            "-channel_layout", "5.1", "-i", str(raw), "-c:a", "pcm_f32le", str(wave)], check=True)
            records[name] = dict(path=str(wave), public=dict(kind="audio", duration=.5, audio_streams=1, channels=6))
        timeline = project([dict(id="base", media_id="red", duration=.5)])
        timeline["audio_layout"] = "5.1"
        timeline["tracks"] += [dict(id="audio-a", kind="audio", clips=[dict(id="a", media_id="first", duration=.5, volume=.5)]),
                               dict(id="audio-b", kind="audio", clips=[dict(id="b", media_id="second", duration=.5, volume=.25)])]
        output = self.output()
        editor.render_project(timeline, records, output)
        decoded = editor.media._run([editor.media.FFMPEG, "-v", "error", "-i", str(output), "-map", "0:a:0", "-c:a", "pcm_f32le", "-f", "f32le", "pipe:1"])
        actual = np.frombuffer(decoded, dtype=np.float32).reshape(-1, 6)
        expected = first * np.float32(.5) + second * np.float32(.25)
        np.testing.assert_array_equal(actual, expected)

    def test_reverse_and_speed_sample_correct_source_frames(self):
        timeline = project([dict(id="a", media_id="red", duration=.5, reverse=True, volume=0)])
        editor.render_project(timeline, self.media, self.output())
        green = [int(frame[48, 80, 1]) for frame in self.decode(self.output())]
        self.assertEqual(green, sorted(green, reverse=True))
        self.assertGreater(green[0] - green[-1], 20000)

    def test_dpx16_embeds_correct_sdr_interpretation_and_exact_pixels(self):
        import zipfile
        import numpy as np
        timeline = project([dict(id="dpx", media_id="red", duration=.125)])
        result = editor.render_project(timeline, self.media, self.output(".zip"), mode="dpx-sequence")
        with zipfile.ZipFile(self.output(".zip")) as archive:
            name = next(n for n in archive.namelist() if n.endswith(".dpx"))
            data = archive.read(name)
        self.assertEqual(data[801:804], bytes((6, 6, 16)))
        image = self.output(".dpx")
        image.write_bytes(data)
        tags = editor.media._video(editor._probe_editor(image))
        self.assertEqual((tags.get("color_transfer"), tags.get("color_primaries")), ("bt709", "bt709"))
        np.testing.assert_array_equal(self.decode(image)[0], self.decode(self.media["red"]["path"])[0])
        self.assertEqual(result["frames"], 1)

    def test_24fps_millisecond_container_timestamps_do_not_repeat_frames(self):
        import av
        import numpy as np
        source = self.output("-24fps-source.mkv")
        arrays = []
        with av.open(str(source), "w", format="matroska") as container:
            stream = container.add_stream("ffv1", rate=24)
            stream.width, stream.height, stream.pix_fmt = 160, 96, "gbrp16le"
            stream.time_base = stream.codec_context.time_base = Fraction(1, 24)
            stream.codec_context.max_b_frames = 0
            stream.codec_context.color_range, stream.codec_context.color_trc = 2, 1
            stream.codec_context.color_primaries, stream.codec_context.colorspace = 1, 0
            for index in range(2):
                array = np.full((96, 160, 3), 10000 + index * 20000, dtype=np.uint16)
                arrays.append(array)
                frame = av.VideoFrame.from_ndarray(array, format="rgb48le")
                frame.pts, frame.time_base, frame.duration = index, Fraction(1, 24), 1
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        public = editor.media.inspect_media(source); public.update(kind="video", duration=2 / 24)
        records = {**self.media, "24fps": dict(path=str(source), public=public)}
        timeline = project([dict(id="a", media_id="24fps", duration=2 / 24),
                            dict(id="b", media_id="24fps", start=2 / 24, duration=2 / 24)])
        timeline["fps"] = "24/1"
        editor.render_project(timeline, records, self.output())
        decoded = self.decode(self.output())
        self.assertEqual(len(decoded), 4)
        for actual, expected in zip(decoded, arrays * 2):
            np.testing.assert_array_equal(actual, expected)

    def test_transform_crop_grade_and_actual_jpeg_preview(self):
        timeline = project([dict(id="a", media_id="red", duration=.5, x=35, scale=.5, exposure=-1,
                                 crop=dict(left=.2), flip_h=True, rotation=10, saturation=.5, temperature=.4, blur=1, sharpen=.2)])
        output = self.output(".jpg")
        result = editor.render_frame(timeline, self.media, .2, 160, output)
        from PIL import Image
        with Image.open(output) as image:
            self.assertEqual(image.size, (160, 96))
            self.assertLess(max(image.getpixel((10, 48))), 10)
            self.assertGreater(image.getpixel((115, 48))[0], 70)
        self.assertEqual(result["time"], .2)

    def test_preview_is_full_timeline_and_preserves_sources(self):
        timeline = project([dict(id="a", media_id="red", duration=.5)])
        result = editor.render_project(timeline, self.media, self.output(".mp4"), preview=True)
        self.assertEqual(result["frames"], 4)
        self.assertTrue(result["preview"])
        self.assert_sources_unchanged()

    def test_lens_is_really_applied(self):
        timeline = project([dict(id="a", media_id="red", duration=.25, lens=dict(exposure=-1, amount=1, flare=0,
            bloom=0, ghosts=0, halation=0, grain=0, distortion=0, chromatic=0, vignette=0))])
        result = editor.render_project(timeline, self.media, self.output())
        self.assertLess(int(self.decode(self.output())[0][48, 80, 0]), 50000)
        self.assertEqual(result["backend"]["device"], "cpu")

    def test_cancel_and_existing_file_preservation(self):
        timeline = project([dict(id="a", media_id="red", duration=.5)])
        event = threading.Event(); event.set()
        with self.assertRaises(InterruptedError):
            editor.render_project(timeline, self.media, self.output(), cancel_event=event)
        self.assertFalse(self.output().exists())
        self.output().write_bytes(b"foreign")
        with self.assertRaises(FileExistsError):
            editor.render_project(timeline, self.media, self.output())
        self.assertEqual(self.output().read_bytes(), b"foreign")

    def test_cancel_stops_actual_running_owned_worker(self):
        timeline = project([])
        timeline["tracks"].append(dict(id="title", kind="title", clips=[dict(id="text", text="Cancel test", duration=30, font_size=24)]))
        event = threading.Event()
        def progress(value, message):
            if value > 0:
                event.set()
        with self.assertRaises(InterruptedError):
            editor.render_project(timeline, self.media, self.output(), cancel_event=event, progress_callback=progress)
        self.assertTrue(event.is_set())
        self.assertFalse(self.output().exists())

    def test_catalog_rgb_lossless_and_image_sequence_exports(self):
        import zipfile
        import av
        timeline = project([dict(id="a", media_id="red", duration=.25)])
        for mode, suffix in (("jpeg2000", ".mov"), ("uncompressed", ".nut"), ("png-sequence", ".zip"),
                             ("tiff-sequence", ".zip"), ("dpx-sequence", ".zip"), ("exr-sequence", ".zip")):
            with self.subTest(mode=mode):
                profile = next(profile for profile in editor.format_catalog.profiles() if profile["id"] == mode)
                if not profile["available"]:
                    self.skipTest(profile["reason"])
                output = self.output("-" + mode + suffix)
                result = editor.render_project(timeline, self.media, output, mode=mode)
                self.assertEqual(result["frames"], 2)
                self.assertIn("SHA256", result["verification"])
                if suffix == ".zip":
                    with zipfile.ZipFile(output) as archive:
                        self.assertIsNone(archive.testzip())
                        frames = [name for name in archive.namelist() if name.startswith("frames/")]
                        self.assertEqual(len(frames), 2)
                        self.assertIn("mix.wav", archive.namelist())
                        self.assertEqual(json.loads(archive.read("timeline.json"))["frame_count"], 2)

    def test_generic_prores_and_h264_profiles_are_actual_decodable_exports(self):
        timeline = project([dict(id="a", media_id="red", duration=.25)])
        timeline["tracks"].append(dict(id="audio", kind="audio", clips=[dict(id="tone", media_id="tone", duration=.25, volume=.3)]))
        for mode, suffix, codec in (("prores", ".mov", "prores"), ("h264", ".mp4", "h264")):
            with self.subTest(mode=mode):
                profile = next(profile for profile in editor.format_catalog.profiles() if profile["id"] == mode)
                if not profile["available"]:
                    self.skipTest(profile["reason"])
                output = self.output("-" + mode + suffix)
                result = editor.render_project(timeline, self.media, output, mode=mode)
                self.assertEqual(result["codec"], codec)
                self.assertEqual(len(self.decode(output)), 2)

    def test_exr_scene_linear_values_above_one_survive_until_exposure(self):
        import av
        import numpy as np
        source = self.output("-linear.exr")
        pixels = np.empty((96, 160, 3), dtype=np.float32); pixels[:] = (2, .5, .25)
        context = av.CodecContext.create("exr", "w")
        context.width, context.height, context.pix_fmt = 160, 96, "gbrpf32le"
        context.time_base = Fraction(1, 8)
        frame = av.VideoFrame.from_ndarray(pixels, format="gbrpf32le"); frame.pts = 0
        with source.open("wb") as target:
            for packet in list(context.encode(frame)) + list(context.encode()):
                target.write(memoryview(packet))
        records = {**self.media, "linear": dict(path=str(source), public=dict(kind="image", duration=0, transfer="linear", color_primaries="unknown"))}
        timeline = project([dict(id="a", media_id="linear", duration=.25, exposure=-2, input_transfer="linear", input_primaries="bt709")])
        editor.render_project(timeline, records, self.output())
        actual = self.decode(self.output())[0][48, 80].astype(np.int64)
        linear = np.array((.5, .125, .0625), dtype=np.float64)
        expected = np.round((1.099 * linear ** .45 - .099) * 65535).astype(np.int64)
        np.testing.assert_allclose(actual, expected, atol=2, rtol=0)

    def test_image_sequence_samples_explicit_owned_frame_order(self):
        from PIL import Image
        frames = []
        for index, color in enumerate(((255, 0, 0), (0, 0, 255))):
            path = self.output(f"-sequence-{index}.png")
            Image.new("RGB", (160, 96), color).save(path)
            frames.append(str(path))
        records = {**self.media, "sequence": dict(path=frames[0], frames=frames,
            public=dict(kind="sequence", duration=.25, fps_exact="8/1", transfer="bt709", color_primaries="bt709"))}
        editor.render_project(project([dict(id="seq", media_id="sequence", duration=.25)]), records, self.output())
        decoded = self.decode(self.output())
        self.assertGreater(int(decoded[0][48, 80, 0]), 65000)
        self.assertGreater(int(decoded[1][48, 80, 2]), 65000)

    def test_packet_copy_is_byte_identical_and_rejects_nonkeyframe_trim(self):
        timeline = project([dict(id="a", media_id="red", duration=.5)])
        result = editor.render_project(timeline, self.media, self.output(), mode="copy")
        self.assertEqual(editor._file_hash(self.output()), self.hashes["red"])
        timeline["tracks"][0]["clips"][0]["in"] = .125
        timeline["tracks"][0]["clips"][0]["duration"] = .25
        with self.assertRaisesRegex(ValueError, "whole clips"):
            editor.render_project(timeline, self.media, self.output("-unsafe.mkv"), mode="copy")

    def test_rounded_349_frame_source_duration_does_not_add_frame(self):
        source = self.output("-349.mov")
        subprocess.run([editor.media.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=red:s=160x96:r=30000/1001",
                        "-frames:v", "349", *editor.media._cli_h264_encoder(editor.media.FFMPEG), str(source)], check=True)
        info = editor.media.inspect_media(source)
        info.update(kind="video", duration=11.644967)
        records = {"source": dict(path=str(source), public=info)}
        timeline = validate_project(project([dict(id="a", media_id="source", duration=11.644967)], fps="30000/1001"), records)
        self.assertEqual(editor._frame_count(timeline, records), 349)
        matroska = self.output("-349.mkv")
        subprocess.run([editor.media.FFMPEG, "-v", "error", "-i", str(source), "-c", "copy", str(matroska)], check=True)
        matroska_info = editor.media.inspect_media(matroska); matroska_info.update(kind="video")
        self.assertEqual(editor._frame_count(validate_project(project([dict(id="a", media_id="mkv", duration=matroska_info["duration"])], fps="30000/1001")),
                                           {"mkv": dict(path=str(matroska), public=matroska_info)}), 349)
        timeline["tracks"][0]["clips"][0]["duration"] = .033367
        self.assertEqual(editor._frame_count(timeline, records), 2, "An intentional off-grid partial edit must ceil")
        timeline["fps"] = "24/1"
        timeline["tracks"][0]["clips"][0]["duration"] = 10 / 24
        self.assertEqual(editor._frame_count(timeline, records), 10, "Binary float arithmetic should not add a frame")

    def test_copy_rejects_color_conversion_and_reports_original_audio(self):
        source = self.output("-hdr.mkv")
        subprocess.run([editor.media.FFMPEG, "-v", "error", "-i", self.media["red"]["path"], "-i", self.media["tone"]["path"],
                        "-map", "0:v:0", "-map", "1:a:0", "-vf", "setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=gbr:range=full",
                        "-c:v", "ffv1", "-pix_fmt", "gbrp16le", "-c:a", "copy", str(source)], check=True)
        info = editor.media.inspect_media(source); info.update(kind="video", duration=.5)
        self.assertEqual(info["transfer"], "pq")
        records = {"hdr": dict(path=str(source), public=info)}
        timeline = project([dict(id="a", media_id="hdr", duration=.5)])
        with self.assertRaisesRegex(ValueError, "color space"):
            editor.render_project(timeline, records, self.output(), mode="copy")
        timeline["color_space"] = "bt2020-pq"
        result = editor.render_project(timeline, records, self.output("-original.mkv"), mode="copy")
        self.assertEqual(result["audio_details"][0]["codec"], "pcm_s24le")
        self.assertIn("Original", result["audio"])
        self.assertEqual(editor._file_hash(source), editor._file_hash(self.output("-original.mkv")))

    def test_many_audio_clips_use_bounded_actual_format_arguments(self):
        import numpy as np
        normalized = validate_project(project([]))
        normalized["tracks"].append(dict(id="audio", kind="audio", muted=False, locked=False,
            clips=[dict(id=f"c{i}", media_id="tone", duration=.125, start=i * .125) for i in range(24)]))
        normalized = validate_project(normalized, self.media)
        original, arguments = editor.media._run, []
        def measured_run(args, timeout=120):
            arguments.append(args)
            return original(args, timeout)
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(editor.media, "_run", side_effect=measured_run):
            path, samples = editor._audio(normalized, self.media, 3, Path(folder))
            output = original([editor.media.FFMPEG, "-v", "error", "-i", str(path), "-f", "f32le", "pipe:1"])
            self.assertEqual(len(output), samples * 8)
            self.assertGreater(float(np.abs(np.frombuffer(output, dtype=np.float32)).max()), .01)
            render_argv = next(args for args in arguments if "-/filter_complex" in args or "-filter_complex_script" in args)
        self.assertLess(len(subprocess.list2cmdline(render_argv)), 32767)
        self.assertEqual(sum(1 for args in arguments if "-show_streams" in args), 1)

    def test_hdr_master_embeds_independently_probed_project_color_tags(self):
        for color_space, transfer in (("bt2020-pq", "smpte2084"), ("bt2020-hlg", "arib-std-b67")):
            with self.subTest(color_space=color_space):
                timeline = project([dict(id="a", media_id="red", duration=.25)], color_space=color_space)
                output = self.output("-" + color_space + ".mkv")
                editor.render_project(timeline, self.media, output)
                raw = json.loads(subprocess.check_output([editor.media.FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(output)]))
                video = next(stream for stream in raw["streams"] if stream["codec_type"] == "video")
                self.assertEqual((video["color_primaries"], video["color_transfer"], video["color_space"], video["color_range"]),
                                 ("bt2020", transfer, "gbr", "pc"))


if __name__ == "__main__":
    unittest.main()
