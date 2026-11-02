"""Streaming local timeline compositor/exporter. GPL-2.0-only.

Each output frame is composed in float32 linear project RGB. PyAV decodes
source frames; row-sized sampling grids implement transforms. Audio is mixed
by FFmpeg. Native calls run in an owned, cancellable worker process. No source
file is modified. Codec-lossless YUV and verified RGB16 are distinct modes.
"""
from __future__ import annotations
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from project_model import validate_project, project_duration, animated
import media_pipeline as media
import format_catalog

_INPUT_OPTIONS = ["-protocol_whitelist", "file,pipe", "-format_whitelist", ",".join(format_catalog.safe_demuxer_names())]
_MODES = [("lossless", "Lossless RGB16 master", ".mkv", "ffv1"),
          ("nvenc-lossless", "NVIDIA lossless HEVC (target YUV)", ".mkv", "hevc_nvenc"),
          ("copy", "Whole-clip packet copy", "source", None),
          ("hevc", "High-quality HEVC delivery", ".mp4", None),
          ("prores", "ProRes editing master", ".mov", "prores_ks")]
_ACESCG_TO_709 = ((1.7050509926579844, -.6217921206570052, -.0832588720009787),
                 (-.1302564175070434, 1.1408047365754013, -.010548319068357955),
                 (-.024003356804618035, -.12896897606497057, 1.1529723328695887))
_CHANNELS = {"mono": 1, "stereo": 2, "5.1": 6, "7.1": 8}


def _progress(work, value, message):
    # Windows readers may briefly deny replacement of an opened status file.
    # Progress is advisory; retry its publication without failing a valid render.
    for _ in range(5):
        try:
            media._progress(work, value, message)
            return
        except PermissionError:
            time.sleep(.01)


def capabilities():
    import av
    from runtime_backend import select_device, backend_info
    device = select_device()
    encoders, decoders = [], []
    for name in sorted(av.codecs_available):
        for mode, destination in (("w", encoders), ("r", decoders)):
            try:
                if av.Codec(name, mode).type in ("video", "audio"):
                    destination.append(name)
            except (ValueError, av.error.FFmpegError):
                pass
    modes = []
    for profile in format_catalog.profiles():
        identifier, label, extension, required = profile["id"], profile["label"], profile["extension"], profile["encoder"]
        available = required is None or required in encoders
        if identifier == "nvenc-lossless":
            available = available and device.type == "cuda"
        modes.append(dict(id=identifier, name=label, extension=extension, available=available,
                          reason="Geometry/format checked at export" if available else "Required encoder/device unavailable"))
    return dict(modes=modes, encoders=encoders, decoders=decoders, acceleration=backend_info(device),
                export_profiles=format_catalog.profiles(), formats=format_catalog.capabilities()["formats"],
                compositing="Float32 linear RGB timeline compositor; source codecs/audio use CPU or available hardware",
                reverse_audio_limit_seconds=60, preview="Full timeline, maximum 1280 pixels wide")


def _record(media_map, identifier):
    record = media_map[identifier]
    path = media._regular(record["path"])
    return path, record.get("public", record)


def _probe_editor(path):
    return json.loads(media._run([media.FFPROBE, "-v", "error", *_INPUT_OPTIONS, "-show_streams", "-show_format", "-of", "json", str(path)], 60))


def _detected_input_options(path, cache):
    key = str(path)
    if key not in cache:
        metadata = _probe_editor(path)
        detected = set(metadata.get("format", {}).get("format_name", "").split(","))
        safe = detected.intersection(format_catalog.safe_demuxer_names())
        if not safe or detected.intersection(format_catalog.BLOCKED_DEMUXERS):
            raise ValueError("Source uses a blocked or unrecognized media demuxer")
        cache[key] = (metadata, ["-protocol_whitelist", "file,pipe", "-format_whitelist", ",".join(sorted(safe))])
    return cache[key]


def _frame_count(project, media_map):
    """Ceil real edits; correct only float ULPs or rounded whole-source ends."""
    fps, duration = Fraction(project["fps"]), Fraction(str(project_duration(project)))
    numerical_quantum = Fraction.from_float(math.ulp(float(duration))) * 2
    latest = []
    for track in project["tracks"]:
        for clip in track["clips"]:
            end = Fraction(str(clip["start"])) + Fraction(str(clip["duration"]))
            if abs(end - duration) <= numerical_quantum:
                latest.append((track, clip, end))
    corrected, cache = [], {}
    for track, clip, end in latest:
        candidate = end
        record = media_map.get(clip.get("media_id"))
        public = record.get("public", record) if record else None
        if public and public.get("kind", "video") in ("video", "audio", "sequence"):
            consumed = Fraction(str(clip["in"])) + Fraction(str(clip["duration"])) * Fraction(str(clip["speed"]))
            published = Fraction(str(public.get("duration", 0)))
            published_ulp = Fraction.from_float(math.ulp(float(published))) * 2 if published else Fraction(0)
            # A user-defined partial trim is never snapped to a source endpoint.
            if published and abs(consumed - published) <= published_ulp:
                exact, quantum = None, None
                if public.get("kind") == "sequence" and record.get("frames"):
                    source_fps = Fraction(str(public["fps_exact"]))
                    exact, quantum = Fraction(len(record["frames"]), 1) / source_fps, 1 / source_fps
                else:
                    path, _ = _record(media_map, clip["media_id"])
                    metadata, _ = _detected_input_options(path, cache)
                    stream = next((s for s in metadata["streams"] if s.get("codec_type") == ("audio" if public.get("kind") == "audio" else "video")), None)
                    if stream and stream.get("duration_ts") not in (None, "N/A"):
                        quantum = Fraction(stream["time_base"])
                        exact = int(stream["duration_ts"]) * quantum
                    elif stream and stream.get("time_base"):
                        quantum = Fraction(stream["time_base"])
                        nearest_end = Fraction(round(end * fps), 1) / fps
                        if quantum > 0 and abs(end - nearest_end) <= quantum / (2 * Fraction(str(clip["speed"]))):
                            candidate = nearest_end
                if exact is not None and quantum > 0 and abs(consumed - exact) <= quantum / 2:
                    candidate = Fraction(str(clip["start"])) + (exact - Fraction(str(clip["in"]))) / Fraction(str(clip["speed"]))
        corrected.append(candidate)
    effective = max(corrected, default=duration)
    frames, nearest = effective * fps, round(effective * fps)
    if abs(effective - Fraction(nearest, 1) / fps) <= numerical_quantum:
        frames = Fraction(nearest)
    return max(1, math.ceil(frames))


def _project_transfer(project):
    return {"bt709": "bt709", "bt2020-pq": "pq", "bt2020-hlg": "hlg"}[project["color_space"]]


def _opacity(clip, local):
    value = animated(clip, "opacity", local)
    if clip["fade_in"]:
        value *= min(1, max(0, local / clip["fade_in"]))
    if clip["fade_out"]:
        value *= min(1, max(0, (clip["duration"] - local) / clip["fade_out"]))
    return value


class _Source:
    def __init__(self, path, public, frames=None):
        import av
        self.av = av
        self.path, self.public = path, public
        self.container = av.open(str(path), options={"protocol_whitelist": "file,pipe", "format_whitelist": _INPUT_OPTIONS[-1]})
        self.stream = next((s for s in self.container.streams.video if not s.disposition.value & 1024), None)
        if self.stream is None:
            raise ValueError("Source has no video/image stream")
        if max(self.stream.width, self.stream.height) > 8192 or self.stream.width * self.stream.height > 35_651_584:
            raise ValueError("Source raster exceeds the supported 8K processing envelope")
        self.stream.codec_context.thread_count = 1
        self.decoder = iter(self.container.decode(self.stream))
        self.current = self.following = None
        self.last = -1.0
        self.frame_paths, self.frame_index = frames, 0
        self.origin = float(self.stream.start_time * self.stream.time_base) if self.stream.start_time is not None else 0
        self.reformatter = av.video.reformatter.VideoReformatter()

    def frame_at(self, seconds, exclusive=False):
        if self.frame_paths:
            frame_position = seconds * float(Fraction(self.public["fps_exact"]))
            index = min(len(self.frame_paths) - 1, max(0, math.ceil(frame_position - 1e-8) - 1 if exclusive else math.floor(frame_position + 1e-8)))
            if index != self.frame_index:
                self.container.close()
                self.container = self.av.open(self.frame_paths[index], options={"protocol_whitelist": "file,pipe", "format_whitelist": _INPUT_OPTIONS[-1]})
                self.stream = self.container.streams.video[0]
                self.stream.codec_context.thread_count = 1
                self.decoder = iter(self.container.decode(self.stream))
                self.current = None
                self.frame_index = index
            if self.current is None:
                self.current = next(self.decoder, None)
            if self.current is None:
                raise ValueError("Sequence frame cannot be decoded")
            return self.current
        if self.public.get("kind") == "image" and self.current is not None:
            return self.current
        if seconds < self.last - 1e-7 or self.current is None and seconds > 1:
            self.container.seek(max(0, int((seconds + self.origin) / self.stream.time_base)), stream=self.stream, backward=True)
            self.decoder = iter(self.container.decode(self.stream))
            self.current = self.following = None
        self.last = seconds
        if self.current is None:
            self.current = next(self.decoder, None)
        if self.current is None:
            raise ValueError("Source decoded no frames")
        if self.public.get("kind") == "image":
            return self.current
        if self.following is None:
            self.following = next(self.decoder, None)
        while self.following is not None:
            if self.following.pts is None:
                raise ValueError("Source frame is missing its timestamp")
            point = float(self.following.pts * self.following.time_base) - self.origin
            tolerance = float(self.following.time_base) / 2
            tolerance = -tolerance - 1e-8 if exclusive else tolerance + 1e-8
            if point > seconds + tolerance:
                break
            self.current, self.following = self.following, next(self.decoder, None)
        return self.current

    def rgb(self, frame, device, preview_axis=None, interpretation=None):
        import numpy as np
        import torch
        fmt = frame.format
        if frame.interlaced_frame:
            raise ValueError("Interlaced source requires explicit deinterlacing before timeline import")
        alpha = len(fmt.components) == 4
        ratio = min(1, preview_axis / max(frame.width, frame.height)) if preview_axis else 1
        target_width, target_height = max(1, round(frame.width * ratio)), max(1, round(frame.height * ratio))
        if "f32" in fmt.name or "f16" in fmt.name:
            transfer, primaries = interpretation or (self.public.get("transfer"), self.public.get("color_primaries"))
            if transfer != "linear" or primaries in (None, "unknown", "unspecified"):
                raise ValueError("Floating-point EXR/scene-linear media needs an explicit linear transfer and known input color primaries")
            image = self.reformatter.reformat(frame, width=target_width, height=target_height, format="gbrapf32le" if alpha else "gbrpf32le")
            value = torch.from_numpy(image.to_ndarray().copy()).to(device).permute(2, 0, 1)
            if not bool(torch.isfinite(value).all()):
                raise ValueError("Scene-linear source contains non-finite pixels")
            rotation = int(self.public.get("rotation", 0)) // 90
            if rotation:
                value = torch.rot90(value, rotation, (1, 2))
            return value
        matrix = frame.colorspace
        if matrix in (0, 2) and not fmt.is_rgb:
            matrix = 1 if frame.height >= 720 else 5
        image = self.reformatter.reformat(frame, width=target_width, height=target_height, format="rgba64le" if alpha else "rgb48le", src_colorspace=matrix,
            src_color_range=frame.color_range or (2 if fmt.is_rgb or fmt.name.startswith("yuvj") else 1), dst_color_range=2)
        array = image.to_ndarray()
        value = torch.from_numpy(array.astype(np.float32)).to(device).permute(2, 0, 1)
        value.div_(65535)
        rotation = int(self.public.get("rotation", 0)) // 90
        if rotation:
            value = torch.rot90(value, rotation, (1, 2))
        return value

    def close(self):
        self.container.close()


def _font(size):
    from PIL import ImageFont
    for path in ("C:/Windows/Fonts/segoeui.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
                 "/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


class _Compositor:
    def __init__(self, project, media_map, device, work):
        self.project, self.media_map, self.device, self.work = project, media_map, device, work
        self.sources, self.lenses = {}, {}
        self.warnings = {"Timeline uses the selected project CFR; source frame timing is sampled onto that grid."}
        self.transfer = _project_transfer(project)

    def _title(self, clip):
        import numpy as np
        import torch
        from PIL import Image, ImageDraw
        width, height = self.project["width"], self.project["height"]
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        text_path = self.work / ("title-" + hashlib.sha256(clip["id"].encode()).hexdigest()[:16] + ".txt")
        text_path.write_text(clip["text"], encoding="utf-8")
        text, font = text_path.read_text(encoding="utf-8"), _font(max(8, round(clip["font_size"])))
        bounds = draw.multiline_textbbox((width / 2, height / 2), text, font=font, anchor="mm", align="center")
        if clip["box_opacity"]:
            draw.rectangle((bounds[0] - 16, bounds[1] - 10, bounds[2] + 16, bounds[3] + 10),
                           fill=clip["box_color"] + f"{round(255 * clip['box_opacity']):02x}")
        draw.multiline_text((width / 2, height / 2), text, font=font, fill=clip["color"], anchor="mm", align="center")
        return torch.from_numpy(np.array(image).astype(np.float32)).to(self.device).permute(2, 0, 1).div_(255)

    def _linear(self, value, transfer, primaries, reference_white_nits=100):
        import torch
        from studio_engine import _decode, _PREVIEW_MATRICES
        if transfer not in ("bt709", "srgb", "pq", "hlg", "linear"):
            raise ValueError("Unsupported source transfer: " + str(transfer))
        rgb = value if transfer == "linear" else _decode(value, transfer)
        if transfer == "linear":
            self.warnings.add("Scene-linear values are graded before conversion into the project display range; RGB16 is a processed master, not an EXR-float archive.")
        if primaries not in ("bt709", "unknown", "unspecified", None, "bt2020", "acescg") and primaries not in _PREVIEW_MATRICES:
            raise ValueError("Unsupported source primaries: " + str(primaries))
        target_primaries = "bt709" if self.project["color_space"] == "bt709" else "bt2020"
        if primaries != target_primaries and (primaries in _PREVIEW_MATRICES or primaries == "acescg"):
            rows = _ACESCG_TO_709 if primaries == "acescg" else _PREVIEW_MATRICES[primaries]
            r, g, b = rgb.unbind(0)
            rgb = torch.stack(tuple(row[0] * r + row[1] * g + row[2] * b for row in rows))
        if target_primaries == "bt2020" and primaries != "bt2020":
            # BT709 linear RGB -> BT2020 linear RGB (CIE chromaticity matrix).
            r, g, b = rgb.unbind(0)
            rgb = torch.stack((.627404*r + .329283*g + .043313*b,
                               .069097*r + .919540*g + .011362*b,
                               .016391*r + .088013*g + .895595*b))
        if transfer != self.transfer and not (transfer in ("bt709", "srgb", "linear") and self.transfer == "bt709"):
            self.warnings.add("Mixed transfers use explicit reference white, HLG 1000-nit peak and system gamma 1.2; this is not calibrated HDR mastering.")
            nits = rgb * 10000 if transfer == "pq" else rgb.clamp_min(0).pow(1.2) * 1000 if transfer == "hlg" else rgb * reference_white_nits
            if self.transfer == "pq":
                rgb = nits / 10000
            elif self.transfer == "hlg":
                rgb = (nits / 1000).clamp_min(0).pow(1 / 1.2)
            else:
                rgb = nits / 100
                luminance = (rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722).clamp_min(0)
                rgb = rgb / (1 + luminance[None])
        elif transfer == "linear" and self.transfer == "bt709":
            rgb = rgb * (reference_white_nits / 100)
        return rgb

    def _grade(self, rgb, clip):
        import torch
        from studio_engine import _box
        rgb = rgb * (2 ** clip["exposure"])
        if clip["contrast"] != 1:
            rgb = .18 + (rgb - .18) * clip["contrast"]
        if clip["temperature"]:
            rgb = rgb * rgb.new_tensor((1 + .2 * clip["temperature"], 1, 1 - .2 * clip["temperature"]))[:, None, None]
        if clip["saturation"] != 1:
            y = rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722
            rgb = y[None] + (rgb - y[None]) * clip["saturation"]
        if clip["blur"]:
            radius = max(1, round(clip["blur"]))
            rgb = _box(_box(rgb, radius, -1), radius, -2)
        if clip["sharpen"]:
            blur = _box(_box(rgb, 1, -1), 1, -2)
            rgb = rgb + (rgb - blur) * clip["sharpen"]
        return rgb.clamp_min(0)

    def _overlay(self, canvas, layer, clip, local, sar=1):
        import torch
        import torch.nn.functional as F
        h, w = layer.shape[1:]
        crop = clip["crop"]
        left, right = round(w * crop["left"]), round(w * (1 - crop["right"]))
        top, bottom = round(h * crop["top"]), round(h * (1 - crop["bottom"]))
        layer = layer[:, top:bottom, left:right]
        h, w = layer.shape[1:]
        ph, pw = canvas.shape[1:]
        fit = min(pw / (w * sar), ph / h) * animated(clip, "scale", local)
        shown_w, shown_h = max(1e-5, w * sar * fit), max(1e-5, h * fit)
        x, y = animated(clip, "x", local), animated(clip, "y", local)
        # Preview has scaled project geometry; offsets are normalized likewise.
        x *= pw / self.project["width"]
        y *= ph / self.project["height"]
        angle = math.radians(clip["rotation"])
        cosine, sine = math.cos(angle), math.sin(angle)
        opacity = _opacity(clip, local)
        if (h, w) == (ph, pw) and sar == 1 and animated(clip, "scale", local) == 1 and x == y == 0 and clip["rotation"] % 360 == 0 and not clip["flip_h"] and not clip["flip_v"]:
            # No sampling grid for a same-raster identity placement. Float grid
            # coordinates can interpolate sharp 16-bit edges even at nominal
            # pixel centers, especially at 8K.
            if layer.shape[0] == 3:
                canvas.copy_(layer) if opacity == 1 else canvas.lerp_(layer, opacity)
            else:
                alpha = layer[3:4] * opacity
                canvas.mul_(1 - alpha).add_(layer[:3] * alpha)
            return
        xs = torch.arange(pw, device=self.device, dtype=torch.float32) + .5 - pw / 2 - x
        for start in range(0, ph, 128):
            ys = torch.arange(start, min(ph, start + 128), device=self.device, dtype=torch.float32) + .5 - ph / 2 - y
            xx = (cosine * xs[None] + sine * ys[:, None]) * (2 / shown_w)
            yy = (-sine * xs[None] + cosine * ys[:, None]) * (2 / shown_h)
            if clip["flip_h"]:
                xx = -xx
            if clip["flip_v"]:
                yy = -yy
            grid = torch.stack((xx, yy), -1)
            tile = F.grid_sample(layer[None], grid[None], align_corners=False, padding_mode="zeros")[0]
            alpha = ((xx.abs() <= 1) & (yy.abs() <= 1)).to(torch.float32) * opacity
            if layer.shape[0] == 4:
                alpha = alpha * tile[3]
            destination = canvas[:, start:start + len(ys)]
            destination.mul_(1 - alpha[None]).add_(tile[:3] * alpha[None])

    def frame(self, seconds, width=None, linear_output=False):
        import torch
        from studio_engine import StudioRenderer, _decode, _encode
        pw, ph = self.project["width"], self.project["height"]
        width = min(pw, width or pw)
        height = max(1, round(ph * width / pw))
        background = tuple(int(self.project["background"][i:i + 2], 16) / 255 for i in (1, 3, 5))
        canvas = _decode(torch.tensor(background, dtype=torch.float32, device=self.device), "srgb")[:, None, None].expand(3, height, width).clone()
        active_visual_count = sum(1 for track in self.project["tracks"] if not track["muted"] and track["kind"] != "audio"
                                  for clip in track["clips"] if clip["start"] - 1e-7 <= seconds < clip["start"] + clip["duration"] - 1e-8)
        active_sources = set()
        for track in self.project["tracks"]:
            if track["muted"] or track["kind"] == "audio":
                continue
            for clip in track["clips"]:
                local = seconds - clip["start"]
                if local < -1e-7 or local >= clip["duration"] - 1e-8:
                    continue
                if track["kind"] == "title":
                    layer = self._title(clip)
                    transfer, primaries, sar = "srgb", "bt709", 1
                else:
                    active_sources.add(clip["id"])
                    if clip["id"] not in self.sources:
                        if len(self.sources) >= 2:
                            # Bound decoder reference frames even with many
                            # overlapping layers. Evicted sources seek again.
                            oldest = next(iter(self.sources))
                            self.sources.pop(oldest).close()
                        path, public = _record(self.media_map, clip["media_id"])
                        self.warnings.update(public.get("warnings", []))
                        self.sources[clip["id"]] = _Source(path, public, self.media_map[clip["media_id"]].get("frames"))
                    source = self.sources.pop(clip["id"])
                    self.sources[clip["id"]] = source
                    target = clip["in"] + ((clip["duration"] - local) * clip["speed"] if clip["reverse"] else local * clip["speed"])
                    transfer = clip["input_transfer"] if clip["input_transfer"] != "auto" else source.public.get("transfer", "bt709")
                    primaries = clip["input_primaries"] if clip["input_primaries"] != "auto" else source.public.get("color_primaries", "unknown")
                    if primaries in (None, "unknown", "unspecified") and transfer != "linear":
                        self.warnings.add("Unspecified source primaries are interpreted as BT.709; set the clip source interpretation to override.")
                    layer = source.rgb(source.frame_at(max(clip["in"], target), exclusive=clip["reverse"]), self.device, max(width, height) if width != pw else None, (transfer, primaries))
                    sar = float(Fraction(str(source.public.get("sample_aspect_ratio", "1/1")).replace(":", "/")))
                    if source.public.get("rotation", 0) % 180:
                        sar = 1 / sar
                    same_primaries = primaries in ("bt709", "unknown", "unspecified", None) if self.project["color_space"] == "bt709" else primaries == "bt2020"
                    unchanged = active_visual_count == 1 and not linear_output and layer.shape == canvas.shape and sar == 1 and transfer == self.transfer and same_primaries
                    unchanged = unchanged and not clip["lens"] and _opacity(clip, local) == 1 and animated(clip, "x", local) == animated(clip, "y", local) == 0 and animated(clip, "scale", local) == 1
                    unchanged = unchanged and not any(clip["crop"].values()) and not clip["flip_h"] and not clip["flip_v"] and clip["rotation"] % 360 == 0
                    unchanged = unchanged and clip["exposure"] == 0 and clip["contrast"] == clip["saturation"] == 1 and clip["temperature"] == clip["blur"] == clip["sharpen"] == 0
                    if unchanged:
                        # Preserve all encoded RGB16 values when the timeline
                        # does not request a pixel operation. Nominal transfer
                        # inverse/forward pairs need not round-trip each code.
                        for identifier in list(self.sources):
                            if identifier != clip["id"]:
                                self.sources.pop(identifier).close()
                                self.lenses.pop(identifier, None)
                        return layer
                if clip["lens"]:
                    if transfer == "linear":
                        raise ValueError("Lens effects require a display-encoded source; develop scene-linear media before applying a lens")
                    if clip["id"] not in self.lenses:
                        self.lenses[clip["id"]] = StudioRenderer(clip["lens"], transfer=transfer)
                    rgb = self.lenses[clip["id"]].render(layer[:3], max(0, round(local * float(Fraction(self.project["fps"])))), local)
                else:
                    rgb = layer[:3]
                rgb = self._grade(self._linear(rgb, transfer, primaries, clip["reference_white_nits"]), clip)
                if layer.shape[0] == 4:
                    rgb = torch.cat((rgb, layer[3:4]), 0)
                self._overlay(canvas, rgb, clip, local, sar)
                del layer, rgb
        for identifier in list(self.sources):
            if identifier not in active_sources:
                self.sources.pop(identifier).close()
                self.lenses.pop(identifier, None)
        return canvas if linear_output else _encode(canvas, self.transfer)

    def close(self):
        for source in self.sources.values():
            source.close()
        self.sources.clear()


def _expression(clip, property_name, variable="t"):
    points = clip["keyframes"].get(property_name, [])
    if not points:
        return format(clip[property_name], ".12g")
    expression = format(points[-1]["value"], ".12g")
    for left, right in reversed(list(zip(points, points[1:]))):
        value = f"({left['value']:.12g}+({right['value']-left['value']:.12g})*({variable}-{left['time']:.12g})/{right['time']-left['time']:.12g})"
        expression = f"if(lt({variable},{right['time']:.12g}),{value},{expression})"
    return f"if(lt({variable},{points[0]['time']:.12g}),{points[0]['value']:.12g},{expression})"


def _audio(project, media_map, duration, work):
    rate, layout = project["audio_sample_rate"], project["audio_layout"]
    channels = _CHANNELS[layout]
    samples = math.ceil(duration * rate)
    arguments = [media.FFMPEG, "-hide_banner", "-v", "error", "-xerror", "-n", "-threads", "2", "-filter_complex_threads", "1"]
    chains, labels, inputs, input_cache = [], [], 0, {}
    for track in project["tracks"]:
        if track["muted"] or track["kind"] == "title":
            continue
        for clip in track["clips"]:
            if not clip["volume"] and not clip["keyframes"].get("volume"):
                continue
            path, public = _record(media_map, clip["media_id"])
            if public.get("kind") == "image" or not public.get("audio_streams", 1 if public.get("kind") == "audio" else 0):
                continue
            consumption = clip["duration"] * clip["speed"]
            if clip["reverse"] and consumption > 60:
                raise ValueError("Reverse audio is limited to 60 source seconds per clip; mute audio or split the clip")
            _, input_options = _detected_input_options(path, input_cache)
            arguments += [*input_options, "-i", str(path)]
            label = f"a{inputs}"
            filters = [f"atrim=start={clip['in']:.12g}:duration={consumption:.12g}", "asetpts=PTS-STARTPTS"]
            if clip["reverse"]:
                filters += ["areverse"]
            speed = clip["speed"]
            while speed > 2:
                filters += ["atempo=2"]
                speed /= 2
            while speed < .5:
                filters += ["atempo=0.5"]
                speed *= 2
            if speed != 1:
                filters += [f"atempo={speed:.12g}"]
            filters += [f"aformat=sample_fmts=fltp:sample_rates={rate}:channel_layouts={layout}"]
            if clip["keyframes"].get("volume"):
                expression = _expression(clip, "volume")
                values = "|".join(f"val({channel})*({expression})" for channel in range(channels))
                filters += [f"aeval=exprs='{values}':channel_layout={layout}"]
            elif clip["volume"] != 1:
                filters += [f"volume={clip['volume']:.12g}:precision=float"]
            if clip["fade_in"]:
                filters += [f"afade=t=in:st=0:d={clip['fade_in']:.12g}"]
            if clip["fade_out"]:
                filters += [f"afade=t=out:st={clip['duration']-clip['fade_out']:.12g}:d={clip['fade_out']:.12g}"]
            filters += [f"adelay={round(clip['start'] * rate)}S:all=1"]
            chains.append(f"[{inputs}:a:0]" + ",".join(filters) + f"[{label}]")
            labels.append(f"[{label}]")
            inputs += 1
    audio = work / "mix.wav"
    if labels:
        chains += ["".join(labels) + f"amix=inputs={len(labels)}:normalize=0,apad,atrim=end_sample={samples}[mix]"]
        graph = work / "audio.ffgraph"
        graph.write_text(";\n".join(chains), encoding="utf-8")
        arguments += ["-filter_complex_script", str(graph), "-map", "[mix]"]
    else:
        arguments += ["-f", "lavfi", "-i", f"anullsrc=r={rate}:cl={layout}", "-t", f"{samples / rate:.12f}"]
    media._run(arguments + ["-c:a", "pcm_f32le", "-ar", str(rate), "-ac", str(channels), str(audio)], 86400)
    return audio, samples


def _quantize(frame):
    import numpy as np
    import torch
    output = np.empty((frame.shape[1], frame.shape[2], 3), dtype=np.uint16)
    for start in range(0, frame.shape[1], 128):
        tile = frame[:, start:start + 128].clamp(0, 1).permute(1, 2, 0).mul(65535).round().to(torch.int32).cpu().numpy()
        output[start:start + len(tile)] = tile
    return output


def _file_hash(path):
    checksum = hashlib.sha256()
    with Path(path).open("rb") as source:
        while block := source.read(1024 * 1024):
            checksum.update(block)
    return checksum.hexdigest()


def _sample_planes(checksum, frame):
    canonical = frame.reformat(format="yuv420p10le")
    for plane in canonical.planes:
        data = memoryview(plane)
        for row in range(plane.height):
            checksum.update(data[row * plane.line_size:row * plane.line_size + plane.width * 2])


def _pcm_hash(path, pcm_format="f32le", rate=48000, channels=2):
    checksum, count = hashlib.sha256(), 0
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen([media.FFMPEG, "-v", "error", "-xerror", *_INPUT_OPTIONS, "-i", str(path),
             "-map", "0:a:0", "-c:a", "pcm_f32le" if pcm_format == "f32le" else "pcm_s24le", "-ar", str(rate), "-ac", str(channels), "-f", pcm_format, "pipe:1"], stdout=subprocess.PIPE, stderr=log)
        try:
            while block := process.stdout.read(1024 * 1024):
                checksum.update(block)
                count += len(block)
            if process.wait(timeout=60):
                raise ValueError("Independent audio decode failed")
        finally:
            process.stdout.close()
            media._stop_tree(process)
    return checksum.hexdigest(), count


def _validate_copy(project, media_map):
    clips = []
    baseline = validate_project(dict(schema_version=1, tracks=[dict(id="default", kind="video", clips=[dict(id="base", media_id="base")])]))["tracks"][0]["clips"][0]
    for track in project["tracks"]:
        if track["muted"]:
            continue
        if track["kind"] != "video" and track["clips"]:
            raise ValueError("Packet copy requires only a single sequence of unchanged video clips")
        clips.extend(track["clips"])
    clips.sort(key=lambda c: c["start"])
    if not clips:
        raise ValueError("Packet copy has no clips")
    cursor, signature, suffix = 0, None, None
    paths = []
    for clip in clips:
        path, public = _record(media_map, clip["media_id"])
        if public.get("kind", "video") != "video" or abs(clip["start"] - cursor) > 1e-6 or clip["in"] != 0 or abs(clip["duration"] - float(public["duration"])) > 1e-5:
            raise ValueError("Packet copy supports whole clips only, with no gaps, overlap or non-keyframe cuts")
        if any(clip[key] != baseline[key] for key in baseline if key not in ("id", "media_id", "start", "duration")):
            raise ValueError("Packet copy cannot include transforms, effects, fades, speed, volume or keyframes")
        metadata = _probe_editor(path)
        video = media._video(metadata)
        expected_transfer = _project_transfer(project)
        source_transfer = public.get("transfer") or media._transfer(video)
        source_primaries = public.get("color_primaries", video.get("color_primaries", "unknown"))
        expected_primaries = "bt709" if project["color_space"] == "bt709" else "bt2020"
        if source_transfer != expected_transfer or source_primaries not in (expected_primaries, "unknown", "unspecified", None):
            raise ValueError("Packet-copy project color space must match the source; color conversion requires rendering")
        current = [(s.get("codec_name"), s.get("codec_type"), s.get("width"), s.get("height"), s.get("pix_fmt"), s.get("sample_rate"), s.get("channels"), s.get("time_base"), s.get("extradata_size")) for s in metadata["streams"]]
        if (video["width"], video["height"], Fraction(video["avg_frame_rate"])) != (project["width"], project["height"], Fraction(project["fps"])):
            raise ValueError("Packet-copy project must match source dimensions and frame rate")
        if signature is not None and (current != signature or path.suffix.lower() != suffix):
            raise ValueError("Packet-copy sources must have homogeneous codec/container/stream parameters")
        signature, suffix = current, path.suffix.lower()
        cursor += clip["duration"]
        paths.append(path)
    # Cross-file codec extradata equality matters; a narrow, honest first
    # release supports a whole source, avoiding unsafe concat headers entirely.
    if len(paths) != 1:
        raise ValueError("Packet-copy export currently supports one unchanged whole source; use a lossless render for sequences")
    return paths[0]


def _sequence_export(project, media_map, profile, compositor, device, work, output, count, fps):
    import av
    import numpy as np
    import torch
    import zipfile
    from runtime_backend import backend_info, empty_cache
    checksum, recovered = hashlib.sha256(), hashlib.sha256()
    linear = profile["id"] == "exr-sequence"
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        with torch.inference_mode():
            for index in range(count):
                frame = compositor.frame(float(Fraction(index, 1) / fps), linear_output=linear)
                if linear:
                    array = np.ascontiguousarray(frame.permute(1, 2, 0).cpu().numpy(), dtype=np.float32)
                    raw = av.VideoFrame.from_ndarray(array, format="gbrpf32le")
                else:
                    array = _quantize(frame)
                    raw = av.VideoFrame.from_ndarray(array, format="rgb48le")
                checksum.update(array.tobytes())
                image = work / (f"frame-{index+1:08d}" + profile["sequence"])
                encoder = av.CodecContext.create(profile["encoder"], "w")
                encoder.width, encoder.height, encoder.pix_fmt = project["width"], project["height"], profile["pixel_format"]
                encoder.time_base, encoder.options = 1 / fps, profile["options"]
                encoded = raw.reformat(format=profile["pixel_format"])
                encoded.pts, encoded.time_base = 0, 1 / fps
                with image.open("xb") as target:
                    for packet in list(encoder.encode(encoded)) + list(encoder.encode()):
                        target.write(memoryview(packet))
                with av.open(str(image), options={"protocol_whitelist": "file,pipe", "format_whitelist": _INPUT_OPTIONS[-1]}) as decoder:
                    decoded = next(decoder.decode(video=0))
                    if (decoded.width, decoded.height) != (project["width"], project["height"]):
                        raise ValueError("Image-sequence decoded raster differs")
                    recovered.update(decoded.to_ndarray(format="gbrpf32le" if linear else "rgb48le").tobytes())
                archive.write(image, "frames/" + image.name)
                image.unlink()
                _progress(work, .8 * (index + 1) / count, f"Encoding and verifying image {index+1}/{count}")
                del frame, array, raw, encoded, encoder, decoded
        compositor.close()
        empty_cache(device)
        audio, samples = _audio(project, media_map, float(Fraction(count, 1) / fps), work)
        archive.write(audio, "mix.wav")
        archive.writestr("timeline.json", json.dumps(dict(schema_version=1, project=project, fps=project["fps"], frame_count=count,
            color_space=project["color_space"], transfer="linear" if linear else _project_transfer(project),
            audio_samples=samples, encoding=profile["id"], sample_sha256=checksum.hexdigest()), indent=2))
    if recovered.digest() != checksum.digest():
        raise ValueError("Decoded image-sequence samples do not match the processed " + ("float32" if linear else "RGB16") + " master")
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("Image-sequence ZIP CRC validation failed")
    return dict(mode=profile["id"], preview=False, width=project["width"], height=project["height"], fps=project["fps"],
        fps_exact=str(fps), bit_depth=32 if linear else 16, container="zip", frame_format=profile["sequence"].lstrip("."),
        frames=count, duration=float(Fraction(count, 1) / fps), codec=profile["codec"], encoder=profile["encoder"],
        pixel_format=profile["pixel_format"], audio=f"Float32 PCM {project['audio_layout']} {project['audio_sample_rate']}Hz mix.wav", backend=backend_info(device),
        encode_quality="Lossless processed linear float32" if linear else "Lossless processed RGB16",
        verification="Every decoded image sample SHA256 matches rendered samples; archive CRC verified", sample_sha256=checksum.hexdigest(),
        warnings=sorted(compositor.warnings), compositing="Float32 linear project RGB")


def _worker(request):
    import av
    import numpy as np
    import torch
    from runtime_backend import select_device, backend_info, empty_cache
    from studio_engine import to_display_rgb
    project = validate_project(request["project"], request["media_map"])
    work, output = Path(request["work"]), Path(request["staged"])
    mode, preview = request["mode"], request["preview"]
    profile = format_catalog.get_profile(mode, project)
    frame_request = request.get("frame")
    device = select_device()
    torch.set_num_threads(2)
    fps = Fraction(project["fps"])
    duration = project_duration(project)
    if duration <= 0:
        raise ValueError("Timeline is empty")
    if mode == "copy" and frame_request is None and not preview:
        source = _validate_copy(project, request["media_map"])
        # A whole-source copy preserves all streams, tags and packet timing.
        import shutil
        shutil.copyfile(source, output)
        if _file_hash(source) != _file_hash(output):
            raise ValueError("Packet-copy byte verification failed")
        media._run([media.FFMPEG, "-v", "error", "-xerror", *_INPUT_OPTIONS, "-i", str(output), "-f", "null", "-"], 86400)
        copied = _probe_editor(output)
        video = media._video(copied)
        audio = [dict(codec=s["codec_name"], sample_rate=int(s.get("sample_rate", 0)), channels=s.get("channels"), layout=s.get("channel_layout", "unknown")) for s in copied["streams"] if s.get("codec_type") == "audio"]
        result = dict(mode=mode, bytes=output.stat().st_size, width=video["width"], height=video["height"],
                      fps_exact=video["avg_frame_rate"], fps=video["avg_frame_rate"], bit_depth=media._depth(video["pix_fmt"]),
                      container=output.suffix.lstrip("."), codec=video["codec_name"], encoder="packet copy", encode_quality="Original encoded bytes",
                      audio="Original streams copied" if audio else "No source audio", audio_streams=len(audio), audio_details=audio,
                      verification="Whole-file SHA256 byte copy and independent decode",
                      warnings=["Packet copy retains original audio streams and formats; project mix/sample-rate/layout settings are not applied."] if audio else [])
        (work / "result.json").write_text(json.dumps(result), encoding="utf-8")
        return
    width = min(project["width"], 1280) if preview else project["width"]
    if frame_request is not None:
        width = min(project["width"], int(frame_request["width"]))
    height = max(1, round(project["height"] * width / project["width"]))
    compositor = _Compositor(project, request["media_map"], device, work)
    if frame_request is not None:
        from PIL import Image
        point = min(frame_request["time"], max(0, duration - 1 / float(fps)))
        started = time.perf_counter()
        with torch.inference_mode():
            frame = compositor.frame(point, width)
            frame = to_display_rgb(frame, _project_transfer(project), "bt709" if project["color_space"] == "bt709" else "bt2020")
            array = (frame.permute(1, 2, 0).clamp(0, 1) * 255).round().to(torch.uint8).cpu().numpy()
            Image.fromarray(array).save(output, quality=90)
        compositor.close()
        result = dict(width=width, height=height, time=point, render_ms=round((time.perf_counter() - started) * 1000, 2))
        (work / "result.json").write_text(json.dumps(result), encoding="utf-8")
        return
    count = _frame_count(project, request["media_map"])
    rendered_duration = float(Fraction(count, 1) / fps)
    if profile.get("sequence") and not preview:
        result = _sequence_export(project, request["media_map"], profile, compositor, device, work, output, count, fps)
        (work / "result.json").write_text(json.dumps(result), encoding="utf-8")
        return
    info = dict(codec="hevc", pixel_format="yuv420p10le", bit_depth=10, width=width, height=height, fps_exact=str(fps))
    if preview:
        info.update(codec="h264", pixel_format="yuv420p", bit_depth=8)
        plan = media._encoder_plan(info, "preview", device_type=device.type)
    elif mode == "lossless":
        plan = media._encoder_plan(info, "lossless", device_type=device.type)
    elif mode == "nvenc-lossless":
        if device.type != "cuda":
            raise ValueError("NVIDIA lossless requires an available CUDA/NVENC device")
        plan = media._encoder_plan(info, "match", device_type=device.type)
        if plan["encoder"] != "hevc_nvenc":
            raise ValueError("NVENC cannot encode this requested geometry/pixel format losslessly")
    elif mode == "hevc":
        plan = media._encoder_plan(info, mode, device_type=device.type)
    elif mode == "h264":
        info.update(codec="h264", pixel_format="yuv420p", bit_depth=8)
        plan = media._encoder_plan(info, "preview", device_type=device.type)
    else:
        plan = dict(encoder=profile["encoder"], codec=profile["codec"], pixel=profile["pixel_format"],
                    options=profile["options"], quality="lossless RGB16" if profile["rgb_lossless"] else "highest quality")
    verify_rgb = bool(profile.get("rgb_lossless")) and not preview
    audio_codec = "aac" if preview else profile["audio_codec"]
    intermediate = "mov" if plan["codec"] in ("prores", "dnxhd", "cfhd") else "nut"
    video_path, checksum = work / ("video." + intermediate), hashlib.sha256()
    trc = 1 if project["color_space"] == "bt709" else 16 if project["color_space"] == "bt2020-pq" else 18
    with av.open(str(video_path), "w", format=intermediate) as container:
        stream = container.add_stream(plan["encoder"], rate=fps)
        stream.width, stream.height, stream.pix_fmt = width, height, plan["pixel"]
        stream.time_base = stream.codec_context.time_base = 1 / fps
        stream.codec_context.max_b_frames = 0
        stream.options = plan["options"]
        rgb_target = av.VideoFormat(plan["pixel"]).is_rgb
        stream.codec_context.color_range = 2 if rgb_target else 1
        stream.codec_context.color_primaries = 1 if preview or project["color_space"] == "bt709" else 9
        stream.codec_context.color_trc = 13 if preview else trc
        stream.codec_context.colorspace = 0 if rgb_target else 1 if preview or project["color_space"] == "bt709" else 9
        reformatter = av.video.reformatter.VideoReformatter()
        with torch.inference_mode():
            for index in range(count):
                frame = compositor.frame(float(Fraction(index, 1) / fps), width)
                if preview:
                    frame = to_display_rgb(frame, _project_transfer(project), "bt709" if project["color_space"] == "bt709" else "bt2020")
                array = _quantize(frame)
                if verify_rgb:
                    checksum.update(array.tobytes())
                rgb_frame = av.VideoFrame.from_ndarray(array, format="rgb48le")
                encoded = reformatter.reformat(rgb_frame, format=plan["pixel"], src_color_range=2,
                    dst_color_range=stream.codec_context.color_range, dst_colorspace=stream.codec_context.colorspace)
                encoded.pts, encoded.time_base, encoded.duration = index, 1 / fps, 1
                if mode == "nvenc-lossless" and not preview:
                    _sample_planes(checksum, encoded)
                for packet in stream.encode(encoded):
                    container.mux(packet)
                _progress(work, .75 * (index + 1) / count, f"Compositing frame {index+1}/{count}")
                del frame, array, rgb_frame, encoded
        compositor.close()
        empty_cache(device)
        for packet in stream.encode():
            container.mux(packet)
    _progress(work, .78, "Mixing timeline audio")
    audio_path, samples = _audio(project, request["media_map"], rendered_duration, work)
    arguments = [media.FFMPEG, "-hide_banner", "-v", "error", "-xerror", "-n", *_INPUT_OPTIONS, "-i", str(video_path),
                 *_INPUT_OPTIONS, "-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                 "-c:a", audio_codec]
    # NUT intermediates preserve rational timestamps but do not preserve all
    # RGB colorimetry fields. Restore the declared processed-signal metadata on
    # the final muxer, then independently verify it below.
    output_primaries = "bt709" if preview or project["color_space"] == "bt709" else "bt2020"
    output_transfer = "iec61966-2-1" if preview else "bt709" if trc == 1 else "smpte2084" if trc == 16 else "arib-std-b67"
    output_matrix = "rgb" if rgb_target else "bt709" if preview or project["color_space"] == "bt709" else "bt2020nc"
    arguments += ["-color_primaries", output_primaries, "-color_trc", output_transfer,
                  "-colorspace", output_matrix, "-color_range", "pc" if rgb_target else "tv"]
    if audio_codec == "flac":
        arguments += ["-sample_fmt", "s32", "-bits_per_raw_sample", "24"]
    if output.suffix in (".mp4", ".mov"):
        arguments += ["-movflags", "+faststart+write_colr", "-video_track_timescale", str(fps.numerator)]
        if plan["codec"] == "hevc":
            arguments += ["-tag:v", "hvc1"]
    media._run(arguments + [str(output)], 86400)
    _progress(work, .9, "Verifying decoded video samples, timing and audio")
    final = media._probe(output)
    video = media._video(final)
    if (video["width"], video["height"], video["codec_name"]) != (width, height, plan["codec"]):
        raise ValueError("Final video geometry/codec verification failed")
    expected_color = ("bt709" if preview or project["color_space"] == "bt709" else "bt2020",
                      "iec61966-2-1" if preview else "bt709" if trc == 1 else "smpte2084" if trc == 16 else "arib-std-b67",
                      "gbr" if rgb_target else "bt709" if preview or project["color_space"] == "bt709" else "bt2020nc",
                      "pc" if rgb_target else "tv")
    actual_color = tuple(video.get(key) for key in ("color_primaries", "color_transfer", "color_space"))
    if actual_color != expected_color[:3]:
        if output.suffix == ".nut" and project["color_space"] == "bt709" and all(value in (None, "unknown", "unspecified", "gbr") for value in actual_color):
            compositor.warnings.add("NUT RGB does not retain explicit colorimetry tags in this build; the processed samples are BT.709. Use a Matroska RGB master for embedded color metadata.")
        else:
            raise ValueError("Final video did not retain the declared project color metadata; choose a Matroska/MOV master for HDR")
    if video.get("color_range") not in (None, "unknown", "unspecified", expected_color[3]):
        raise ValueError("Final video range disagrees with the encoded project samples")
    if video.get("color_range") in (None, "unknown", "unspecified"):
        compositor.warnings.add("This codec/container omits an explicit range tag; processed RGB samples use full range and processed YUV samples use limited range.")
    recovered, actual_count = hashlib.sha256(), 0
    with av.open(str(output), options={"protocol_whitelist": "file,pipe"}) as check:
        tolerance = Fraction(video["time_base"]) / 2 + Fraction(1, 1000000)
        for decoded in check.decode(video=0):
            if decoded.pts is None or abs(decoded.pts * decoded.time_base - Fraction(actual_count, 1) / fps) > tolerance:
                raise ValueError("Timeline output frame cadence changed")
            if verify_rgb:
                recovered.update(media._active_bytes(decoded))
            elif mode == "nvenc-lossless" and not preview:
                _sample_planes(recovered, decoded)
            actual_count += 1
    if actual_count != count:
        raise ValueError("Final output frame count differs from timeline")
    if verify_rgb and recovered.digest() != checksum.digest():
        raise ValueError("Independent RGB16 decoded hash did not match rendered samples")
    if mode == "nvenc-lossless" and not preview and recovered.digest() != checksum.digest():
        raise ValueError("Independent decoded YUV10 samples differ from lossless encoder input")
    if not any(s.get("codec_type") == "audio" and int(s.get("sample_rate", 0)) == project["audio_sample_rate"] and s.get("channels") == _CHANNELS[project["audio_layout"]] for s in final["streams"]):
        raise ValueError("Final mixed audio verification failed")
    canonical_pcm = "f32le" if audio_codec == "pcm_f32le" else "s24le"
    audio_arguments = dict(pcm_format=canonical_pcm, rate=project["audio_sample_rate"], channels=_CHANNELS[project["audio_layout"]])
    if audio_codec != "aac" and _pcm_hash(audio_path, **audio_arguments) != _pcm_hash(output, **audio_arguments):
        raise ValueError("Independent decoded PCM samples differ from the timeline mix")
    media._run([media.FFMPEG, "-v", "error", "-xerror", *_INPUT_OPTIONS, "-i", str(output), "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], 86400)
    result = dict(mode=mode, preview=preview, width=width, height=height, frames=count, fps=project["fps"], duration=rendered_duration,
                  fps_exact=str(fps), bit_depth=media._depth(video["pix_fmt"]), container=output.suffix.lstrip("."),
                  codec=plan["codec"], encoder=plan["encoder"], pixel_format=video["pix_fmt"], audio=f"{audio_codec} {project['audio_layout']} {project['audio_sample_rate']}Hz",
                  encode_quality=plan["quality"], backend=backend_info(device),
                  compositing="Float32 linear project RGB", verification="Independent decoded RGB16 SHA256 matches rendered samples" if verify_rgb else "Full independent decode, frame count, geometry and CFR timestamps",
                  rgb16_sha256=checksum.hexdigest() if verify_rgb else None,
                  warnings=sorted(compositor.warnings | set(plan.get("warnings", []))))
    if mode == "nvenc-lossless":
        result["yuv10_sha256"] = checksum.hexdigest()
        result["verification"] = "Independent decoded YUV10 SHA256 matches target encoder-input samples; full audio/video decode verified"
        result["warnings"].append("NVENC is codec-lossless for target YUV10 samples; RGB-to-420 conversion is not lossless RGB.")
    if audio_codec not in ("pcm_f32le", "aac"):
        result["warnings"].append("This compatibility profile quantizes the float32 timeline audio mix to 24-bit integer samples.")
    result["warnings"].append(f"Audio clips use their first source audio stream and are mixed/resampled to project {project['audio_layout']} at {project['audio_sample_rate']} Hz.")
    (work / "result.json").write_text(json.dumps(result), encoding="utf-8")


def _invoke(project, media_map, output_path, mode, progress_callback, cancel_event, preview, frame=None):
    project = validate_project(project, media_map)
    profile = format_catalog.get_profile(mode, project)
    if project_duration(project) <= 0:
        raise ValueError("Timeline is empty")
    output = media._new_output(output_path)
    suffix = ".jpg" if frame is not None else ".mp4" if preview else profile["extension"]
    if mode == "copy" and not preview and frame is None:
        suffix = _validate_copy(project, media_map).suffix.lower()
    if output.suffix.lower() != suffix:
        raise ValueError("Output extension must be " + suffix)
    callback, cancel = progress_callback or (lambda p, m: None), cancel_event or threading.Event()
    if cancel.is_set():
        raise InterruptedError("Render canceled")
    # Pass only owned private paths and plain JSON metadata to the subprocess.
    records = {key: {"path": str(media._regular(value["path"])), "public": value.get("public", {k: v for k, v in value.items() if k not in ("path", "frames")}),
                     **({"frames": [str(media._regular(path)) for path in value["frames"]]} if value.get("frames") else {})} for key, value in media_map.items()}
    with tempfile.TemporaryDirectory(prefix=".editor-render-", dir=output.parent) as folder:
        work = Path(folder)
        request = dict(project=project, media_map=records, mode=mode, preview=bool(preview), frame=frame, work=str(work), staged=str(work / ("output" + suffix)))
        path = work / "request.json"
        path.write_text(json.dumps(request), encoding="utf-8")
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        with (work / "worker.log").open("wb") as log:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker", str(path)], stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=log, **options)
            started, previous = time.monotonic(), ""
            try:
                while process.poll() is None:
                    if cancel.is_set():
                        raise InterruptedError("Render canceled")
                    if time.monotonic() - started > (120 if frame is not None else 86400):
                        raise TimeoutError("Render exceeded its processing deadline")
                    try:
                        text = (work / "progress.json").read_text(encoding="utf-8")
                        if text != previous:
                            progress = json.loads(text)
                            callback(progress["progress"], progress["message"])
                            previous = text
                    except (FileNotFoundError, json.JSONDecodeError, PermissionError):
                        pass
                    time.sleep(.1)
                if process.returncode:
                    raise ValueError((work / "worker.log").read_text(errors="replace")[-6000:])
            finally:
                media._stop_tree(process)
        if cancel.is_set():
            raise InterruptedError("Render canceled before publication")
        result = json.loads((work / "result.json").read_text(encoding="utf-8"))
        media._publish(Path(request["staged"]), output)
        result.update(output=str(output), bytes=output.stat().st_size)
        callback(1, "Verified render saved")
        return result


def render_project(project, media_map, output_path, mode="lossless", progress_callback=None, cancel_event=None, preview=False):
    return _invoke(project, media_map, output_path, mode, progress_callback, cancel_event, preview)


def render_frame(project, media_map, time_s, width, output_path, cancel_event=None):
    project = validate_project(project, media_map)
    if isinstance(time_s, bool) or not isinstance(time_s, (int, float)) or not math.isfinite(time_s) or time_s < 0 or time_s > project_duration(project):
        raise ValueError("Preview time must be within the timeline")
    if type(width) is not int or not 64 <= width <= 1920:
        raise ValueError("Frame preview width must be 64..1920")
    return _invoke(project, media_map, output_path, "lossless", None, cancel_event, True, frame=dict(time=time_s, width=width))


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--worker":
        raise SystemExit("Use the local editor API")
    try:
        _worker(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
    except Exception as error:
        print(type(error).__name__ + ": " + str(error), file=sys.stderr, flush=True)
        raise SystemExit(1)
