"""Validated, JSON-only timeline model for 8K Lossless Video Editor. GPL-2.0."""
from copy import deepcopy
from fractions import Fraction
import math
import re

_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_CLIP = dict(start=0, **{"in": 0}, duration=5, speed=1, reverse=False, opacity=1, volume=1,
             fade_in=0, fade_out=0, x=0, y=0, scale=1, rotation=0, flip_h=False, flip_v=False,
             crop=dict(left=0, right=0, top=0, bottom=0), exposure=0, contrast=1, saturation=1,
             temperature=0, blur=0, sharpen=0, text="", font_size=64, color="#ffffff",
             box_color="#000000", box_opacity=0, keyframes={}, lens={}, input_transfer="auto", input_primaries="auto", reference_white_nits=100)
_RANGES = dict(start=(0, 86400), **{"in": (0, 86400)}, duration=(.001, 86400), speed=(.01, 64),
               opacity=(0, 1), volume=(0, 8), fade_in=(0, 86400), fade_out=(0, 86400),
               x=(-131072, 131072), y=(-131072, 131072), scale=(.01, 100), rotation=(-36000, 36000),
               exposure=(-10, 10), contrast=(0, 4), saturation=(0, 4), temperature=(-1, 1),
               blur=(0, 100), sharpen=(0, 5), font_size=(8, 1024), box_opacity=(0, 1), reference_white_nits=(1, 10000))


def _number(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be a finite number in {low}..{high}")
    return float(value)


def _text(value, name, limit=200):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise ValueError(f"Invalid {name}")
    return value


def _id(value, name):
    value = _text(value, name, 128)
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _color(value, name):
    if not isinstance(value, str) or not _COLOR.fullmatch(value):
        raise ValueError(f"{name} must be #RRGGBB")
    return value.lower()


def project_duration(project):
    return max((float(c.get("start", 0)) + float(c.get("duration", 5)) for t in project.get("tracks", []) for c in t.get("clips", [])), default=0.0)


def validate_project(project, media_map=None):
    if not isinstance(project, dict):
        raise ValueError("Project must be an object")
    allowed = {"schema_version", "id", "name", "width", "height", "fps", "background", "color_space", "tracks", "markers", "audio_sample_rate", "audio_layout"}
    if set(project) - allowed:
        raise ValueError("Unknown project settings: " + ", ".join(sorted(set(project) - allowed)))
    if project.get("schema_version", 1) != 1:
        raise ValueError("Unsupported project schema")
    output = dict(schema_version=1, name=_text(project.get("name", "Untitled project"), "project name"),
                  width=project.get("width", 1920), height=project.get("height", 1080),
                  background=_color(project.get("background", "#000000"), "background"),
                  color_space=project.get("color_space", "bt709"), audio_sample_rate=project.get("audio_sample_rate", 48000),
                  audio_layout=project.get("audio_layout", "stereo"), tracks=[], markers=[])
    if type(output["audio_sample_rate"]) is not int or output["audio_sample_rate"] not in (44100, 48000, 96000) or output["audio_layout"] not in ("mono", "stereo", "5.1", "7.1"):
        raise ValueError("Unsupported project audio sample rate/channel layout")
    if "id" in project:
        output["id"] = _id(project["id"], "project id")
    for key in ("width", "height"):
        if type(output[key]) is not int or not 16 <= output[key] <= 8192:
            raise ValueError("Project dimensions must be integers in 16..8192")
    if output["width"] * output["height"] > 35_651_584:
        raise ValueError("Project exceeds the 35.65 megapixel envelope")
    try:
        rate = Fraction(str(project.get("fps", "24/1")))
    except (ValueError, ZeroDivisionError):
        raise ValueError("Invalid rational frame rate") from None
    if not 1 <= rate <= 120:
        raise ValueError("Frame rate must be 1..120")
    output["fps"] = f"{rate.numerator}/{rate.denominator}"
    if output["color_space"] not in ("bt709", "bt2020-pq", "bt2020-hlg"):
        raise ValueError("Unsupported project color space")
    tracks = project.get("tracks", [])
    if not isinstance(tracks, list) or len(tracks) > 64:
        raise ValueError("Project supports up to 64 tracks")
    identifiers = set()
    clip_count = 0
    for track in tracks:
        if not isinstance(track, dict) or set(track) - {"id", "name", "kind", "muted", "locked", "clips"}:
            raise ValueError("Invalid or unknown track settings")
        normalized = dict(id=_id(track.get("id"), "track id"), name=_text(track.get("name", "Track"), "track name"),
                          kind=track.get("kind", "video"), muted=track.get("muted", False), locked=track.get("locked", False), clips=[])
        if normalized["id"] in identifiers:
            raise ValueError("Duplicate timeline id")
        identifiers.add(normalized["id"])
        if normalized["kind"] not in ("video", "audio", "title") or type(normalized["muted"]) is not bool or type(normalized["locked"]) is not bool:
            raise ValueError("Invalid track kind/mute/lock")
        if not isinstance(track.get("clips", []), list):
            raise ValueError("Track clips must be an array")
        for clip in track.get("clips", []):
            if not isinstance(clip, dict) or set(clip) - set(_CLIP) - {"id", "media_id"}:
                raise ValueError("Unknown clip settings")
            current = deepcopy(_CLIP)
            current.update(deepcopy(clip))
            current["id"] = _id(current.get("id"), "clip id")
            if current["id"] in identifiers:
                raise ValueError("Duplicate timeline id")
            identifiers.add(current["id"])
            for key, bounds in _RANGES.items():
                current[key] = _number(current[key], *bounds, key)
            for key in ("reverse", "flip_h", "flip_v"):
                if type(current[key]) is not bool:
                    raise ValueError(f"{key} must be boolean")
            if current["input_transfer"] not in ("auto", "linear", "srgb", "bt709", "pq", "hlg") or current["input_primaries"] not in ("auto", "bt709", "bt2020", "smpte432", "acescg"):
                raise ValueError("Unsupported source interpretation")
            if current["start"] + current["duration"] > 86400 or max(current["fade_in"], current["fade_out"]) > current["duration"]:
                raise ValueError("Clip timing/fade exceeds its supported duration")
            current["text"] = _text(current["text"], "title text", 4000)
            current["color"] = _color(current["color"], "title color")
            current["box_color"] = _color(current["box_color"], "title box color")
            if not isinstance(current["crop"], dict) or set(current["crop"]) - {"left", "right", "top", "bottom"}:
                raise ValueError("Invalid crop")
            current["crop"] = {key: _number(current["crop"].get(key, 0), 0, .49, "crop " + key) for key in ("left", "right", "top", "bottom")}
            keyframes = current["keyframes"]
            if not isinstance(keyframes, dict) or set(keyframes) - {"opacity", "x", "y", "scale", "volume"}:
                raise ValueError("Unsupported keyframe property")
            for key, points in keyframes.items():
                if not isinstance(points, list) or len(points) > 1000:
                    raise ValueError("Too many keyframes")
                clean = []
                for point in points:
                    if not isinstance(point, dict) or set(point) != {"time", "value"}:
                        raise ValueError("Invalid keyframe")
                    clean.append(dict(time=_number(point["time"], 0, current["duration"], "keyframe time"),
                                      value=_number(point["value"], *_RANGES[key], "keyframe " + key)))
                clean.sort(key=lambda p: p["time"])
                if len({p["time"] for p in clean}) != len(clean):
                    raise ValueError("Duplicate keyframe time")
                current["keyframes"][key] = clean
            if not isinstance(current["lens"], dict):
                raise ValueError("Lens settings must be an object")
            if current["lens"]:
                from studio_engine import normalize_settings
                current["lens"] = normalize_settings(current["lens"])
            if normalized["kind"] == "title":
                if current.get("media_id"):
                    raise ValueError("Title clips cannot reference media")
                if current["in"] or current["speed"] != 1 or current["reverse"] or current["volume"] != 1 or current["keyframes"].get("volume"):
                    raise ValueError("Title clips do not have source trimming, speed or audio")
                if current["input_transfer"] != "auto" or current["input_primaries"] != "auto":
                    raise ValueError("Title colors use sRGB/BT.709; source interpretation overrides require media")
            else:
                if current["text"] or current["font_size"] != 64 or current["color"] != "#ffffff" or current["box_color"] != "#000000" or current["box_opacity"]:
                    raise ValueError("Text styling needs a title track")
                if normalized["kind"] == "audio":
                    audio_fields = {"id", "media_id", "start", "in", "duration", "speed", "reverse", "volume", "fade_in", "fade_out", "keyframes"}
                    if any(current[key] != default for key, default in _CLIP.items() if key not in audio_fields) or set(current["keyframes"]) - {"volume"}:
                        raise ValueError("Audio clips cannot contain visual effects or transform keyframes")
                current["media_id"] = _id(current.get("media_id"), "media id")
                if media_map is not None and not normalized["muted"]:
                    if current["media_id"] not in media_map:
                        raise ValueError("Offline media: " + current["media_id"])
                    item = media_map[current["media_id"]]
                    public = item.get("public", item)
                    kind = public.get("kind", "video")
                    if normalized["kind"] == "video" and kind == "audio":
                        raise ValueError("Audio media needs an audio track")
                    if normalized["kind"] == "audio" and kind == "image":
                        raise ValueError("Images have no audio")
                    duration = float(public.get("duration") or 0)
                    if kind != "image" and duration > 0 and current["in"] + current["duration"] * current["speed"] > duration + 1 / float(rate):
                        raise ValueError("Clip consumes media beyond its duration")
                    if current["reverse"] and current["duration"] * current["speed"] > 60 and (
                            normalized["kind"] == "audio" or public.get("audio_streams", 0)) and (current["volume"] or current["keyframes"].get("volume")):
                        raise ValueError("Reverse audio is limited to 60 source seconds per clip; mute audio or split the clip")
            normalized["clips"].append(current)
            clip_count += 1
        output["tracks"].append(normalized)
    if clip_count > 2000:
        raise ValueError("Project supports up to 2000 clips")
    markers = project.get("markers", [])
    if not isinstance(markers, list) or len(markers) > 2000:
        raise ValueError("Invalid markers")
    for marker in markers:
        if not isinstance(marker, dict) or set(marker) - {"id", "time", "label", "color"}:
            raise ValueError("Invalid marker")
        output["markers"].append(dict(id=_id(marker.get("id"), "marker id"),
            time=_number(marker.get("time", 0), 0, 86400, "marker time"),
            label=_text(marker.get("label", ""), "marker label"), color=_color(marker.get("color", "#f9bc60"), "marker color")))
    return output


def animated(clip, key, local_time):
    points = clip["keyframes"].get(key, [])
    if not points:
        return clip[key]
    if local_time <= points[0]["time"]:
        return points[0]["value"]
    for left, right in zip(points, points[1:]):
        if local_time < right["time"]:
            fraction = (local_time - left["time"]) / (right["time"] - left["time"])
            return left["value"] + fraction * (right["value"] - left["value"])
    return points[-1]["value"]
