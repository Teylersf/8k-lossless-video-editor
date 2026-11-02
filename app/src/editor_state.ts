import type {
  Clip,
  KeyframeKey,
  Media,
  MediaMap,
  Project,
  Track,
  TrackKind,
} from "./types.ts";
import { lensControls, lensDefaults } from "./lens.ts";

export const uid = (kind = "item") =>
  `${kind}_${crypto.randomUUID().replaceAll("-", "")}`;
export const clamp = (n: number, low: number, high: number) =>
  Math.max(low, Math.min(high, n));
export const fpsValue = (fps: string) => {
  const [n, d = 1] = fps.split("/").map(Number);
  return n / d;
};
export const duration = (p: Project) =>
  p.tracks.reduce(
    (end, t) =>
      t.clips.reduce((v, c) => Math.max(v, c.start + c.duration), end),
    0,
  );
export const clipCount = (p: Project) =>
  p.tracks.reduce((n, t) => n + t.clips.length, 0);
export const frameTime = (p: Project) => 1 / fpsValue(p.fps);
export const roundFrame = (seconds: number, p: Project) =>
  Math.round(seconds * fpsValue(p.fps)) / fpsValue(p.fps);
export const findClip = (p: Project, id: string | null) => {
  for (const track of p.tracks) {
    const clip = track.clips.find((c) => c.id === id);
    if (clip) return { track, clip };
  }
  return null;
};
export const renderKey = (p: Project) =>
  JSON.stringify({ ...p, id: undefined, name: "" });
export function timecode(seconds: number, fps = "24/1") {
  const rate = Math.round(fpsValue(fps)),
    count = Math.max(0, Math.round(seconds * fpsValue(fps)));
  const h = Math.floor(count / (rate * 3600)),
    m = Math.floor(count / (rate * 60)) % 60,
    s = Math.floor(count / rate) % 60,
    f = count % rate;
  return [h, m, s, f].map((x) => String(x).padStart(2, "0")).join(":");
}
export function parseTimecode(value: string, fps: string) {
  if (/^\d+(\.\d+)?$/.test(value.trim())) return Number(value);
  const parts = value.trim().split(":").map(Number);
  if (
    parts.length !== 4 ||
    parts.some((n) => !Number.isInteger(n) || n < 0) ||
    parts[1] > 59 ||
    parts[2] > 59 ||
    parts[3] >= Math.round(fpsValue(fps))
  )
    throw new Error("Use seconds or HH:MM:SS:FF timecode.");
  return (
    (((parts[0] * 60 + parts[1]) * 60 + parts[2]) * Math.round(fpsValue(fps)) +
      parts[3]) /
    fpsValue(fps)
  );
}
export function makeTrack(kind: TrackKind, name: string): Track {
  return {
    id: uid("track"),
    name,
    kind,
    muted: false,
    locked: false,
    clips: [],
  };
}
export function newProject(name = "Untitled sequence"): Project {
  return {
    schema_version: 1,
    name,
    width: 1920,
    height: 1080,
    fps: "24/1",
    background: "#080b0e",
    color_space: "bt709",
    audio_sample_rate: 48000,
    audio_layout: "stereo",
    tracks: [
      makeTrack("video", "Video 1"),
      makeTrack("video", "Video 2"),
      makeTrack("title", "Titles"),
      makeTrack("audio", "Audio 1"),
    ],
    markers: [],
  };
}
export function makeClip(media?: Media, start = 0): Clip {
  return {
    id: uid("clip"),
    ...(media ? { media_id: media.id } : {}),
    start,
    in: 0,
    duration:
      media && media.kind !== "image"
        ? clamp(media.duration, 0.001, 86400 - start)
        : Math.min(5, 86400 - start),
    speed: 1,
    reverse: false,
    opacity: 1,
    volume: 1,
    fade_in: 0,
    fade_out: 0,
    x: 0,
    y: 0,
    scale: 1,
    rotation: 0,
    flip_h: false,
    flip_v: false,
    crop: { left: 0, right: 0, top: 0, bottom: 0 },
    exposure: 0,
    contrast: 1,
    saturation: 1,
    temperature: 0,
    blur: 0,
    sharpen: 0,
    text: media ? "" : "Your title",
    font_size: 64,
    color: "#ffffff",
    box_color: "#000000",
    box_opacity: 0,
    input_transfer: "auto",
    input_primaries: "auto",
    reference_white_nits: 100,
    keyframes: {},
    lens: {},
  };
}
export function accepts(track: Track, media?: Media) {
  return track.kind === "title"
    ? !media
    : !!media &&
        (track.kind === "video"
          ? media.kind !== "audio"
          : media.kind === "audio" || media.audio_streams > 0);
}
function writable(track: Track) {
  if (track.locked)
    throw new Error(
      `${track.name} is locked. Unlock the track to edit its clips.`,
    );
}
export function updateClip(
  p: Project,
  id: string,
  change: Partial<Clip> | ((clip: Clip) => Clip),
): Project {
  return {
    ...p,
    tracks: p.tracks.map((t) => {
      const found = t.clips.some((c) => c.id === id);
      if (!found) return t;
      writable(t);
      return {
        ...t,
        clips: t.clips.map((c) =>
          c.id === id
            ? typeof change === "function"
              ? change(c)
              : { ...c, ...change }
            : c,
        ),
      };
    }),
  };
}
export function updateTrack(
  p: Project,
  id: string,
  change: Partial<Track>,
): Project {
  return {
    ...p,
    tracks: p.tracks.map((t) => (t.id === id ? { ...t, ...change } : t)),
  };
}
export function insertClip(
  p: Project,
  clip: Clip,
  trackId: string,
  media?: Media,
): Project {
  const target = p.tracks.find((t) => t.id === trackId);
  if (!target) throw new Error("Choose a destination track.");
  writable(target);
  if (!accepts(target, media))
    throw new Error("That media type needs a compatible video or audio track.");
  if (clipCount(p) >= 2000)
    throw new Error("A project supports up to 2,000 clips.");
  return {
    ...p,
    tracks: p.tracks.map((t) =>
      t.id === trackId ? { ...t, clips: [...t.clips, clip] } : t,
    ),
  };
}
export function moveClip(
  p: Project,
  id: string,
  start: number,
  toTrackId?: string,
  mediaMap: MediaMap = {},
): Project {
  const found = findClip(p, id);
  if (!found) return p;
  writable(found.track);
  const target = p.tracks.find((t) => t.id === (toTrackId || found.track.id));
  if (!target) throw new Error("Destination track not found.");
  writable(target);
  const media = mediaMap[found.clip.media_id || ""];
  if (
    found.track.kind === "title"
      ? target.kind !== "title"
      : target.kind === "title" ||
        (media ? !accepts(target, media) : target.kind !== found.track.kind)
  )
    throw new Error("Move this clip to a compatible track.");
  const next = {
    ...found.clip,
    start: clamp(start, 0, 86400 - found.clip.duration),
  };
  if (target.id === found.track.id) return updateClip(p, id, next);
  return {
    ...p,
    tracks: p.tracks.map((t) =>
      t.id === target.id
        ? { ...t, clips: [...t.clips, next] }
        : t.id === found.track.id
          ? { ...t, clips: t.clips.filter((c) => c.id !== id) }
          : t,
    ),
  };
}
export function removeClip(p: Project, id: string): Project {
  const found = findClip(p, id);
  if (!found) return p;
  writable(found.track);
  return {
    ...p,
    tracks: p.tracks.map((t) =>
      t.id === found.track.id
        ? { ...t, clips: t.clips.filter((c) => c.id !== id) }
        : t,
    ),
  };
}
export function duplicateClip(p: Project, id: string) {
  const found = findClip(p, id);
  if (!found) return { project: p, id };
  writable(found.track);
  const copy = {
    ...structuredClone(found.clip),
    id: uid("clip"),
    start: found.clip.start + found.clip.duration,
  };
  if (copy.start + copy.duration > 86400)
    throw new Error("The duplicate would exceed the 24-hour timeline.");
  return {
    project: {
      ...p,
      tracks: p.tracks.map((t) =>
        t.id === found.track.id ? { ...t, clips: [...t.clips, copy] } : t,
      ),
    },
    id: copy.id,
  };
}
export function animated(c: Clip, key: KeyframeKey, time: number) {
  const points = c.keyframes[key] || [];
  if (!points.length) return c[key];
  if (time <= points[0].time) return points[0].value;
  for (let i = 1; i < points.length; i++) {
    if (time < points[i].time) {
      const a = points[i - 1],
        b = points[i];
      return (
        a.value + ((b.value - a.value) * (time - a.time)) / (b.time - a.time)
      );
    }
  }
  return points.at(-1)!.value;
}
function slicedKeys(c: Clip, from: number, to: number): Clip["keyframes"] {
  const result: Clip["keyframes"] = {};
  for (const key of Object.keys(c.keyframes) as KeyframeKey[]) {
    if (!c.keyframes[key]?.length) continue;
    result[key] = [
      { time: 0, value: animated(c, key, from) },
      ...c.keyframes[key]!.filter((k) => k.time > from && k.time < to).map(
        (k) => ({ ...k, time: k.time - from }),
      ),
      { time: to - from, value: animated(c, key, to) },
    ];
  }
  return result;
}
export function splitClip(p: Project, id: string, position: number) {
  const found = findClip(p, id);
  if (!found) throw new Error("Select a clip to split.");
  writable(found.track);
  const c = found.clip,
    cut = roundFrame(position - c.start, p),
    minimum = Math.max(0.001, frameTime(p) / 2);
  if (cut < minimum || c.duration - cut < minimum)
    throw new Error(
      "Place the playhead inside the selected clip, away from its edges.",
    );
  const left = {
    ...c,
    duration: cut,
    in: c.reverse ? c.in + (c.duration - cut) * c.speed : c.in,
    fade_in: Math.min(c.fade_in, cut),
    fade_out: 0,
    keyframes: slicedKeys(c, 0, cut),
  };
  const right = {
    ...structuredClone(c),
    id: uid("clip"),
    start: c.start + cut,
    in:
      found.track.kind === "title"
        ? 0
        : c.reverse
          ? c.in
          : c.in + cut * c.speed,
    duration: c.duration - cut,
    fade_in: 0,
    fade_out: Math.min(c.fade_out, c.duration - cut),
    keyframes: slicedKeys(c, cut, c.duration),
  };
  return {
    project: {
      ...p,
      tracks: p.tracks.map((t) =>
        t.id === found.track.id
          ? {
              ...t,
              clips: t.clips.flatMap((v) =>
                v.id === id ? [left, right] : [v],
              ),
            }
          : t,
      ),
    },
    id: right.id,
  };
}
export function trimClip(
  p: Project,
  id: string,
  edge: "left" | "right",
  requestedDelta: number,
  mediaMap: MediaMap = {},
): Project {
  const found = findClip(p, id);
  if (!found) return p;
  writable(found.track);
  const c = found.clip,
    item = mediaMap[c.media_id || ""],
    timed = !!item && item.kind !== "image",
    min = Math.min(c.duration, Math.max(0.001, frameTime(p)));
  const sourceEnd = timed ? item.duration : Infinity,
    availableBefore = timed ? c.in / c.speed : Infinity,
    availableAfter = timed
      ? (sourceEnd - c.in - c.duration * c.speed) / c.speed
      : Infinity;
  let delta = roundFrame(requestedDelta, p),
    next: Clip;
  if (edge === "left") {
    delta = clamp(
      delta,
      Math.max(-c.start, c.reverse ? -availableAfter : -availableBefore),
      c.duration - min,
    );
    const newDuration = c.duration - delta;
    next = {
      ...c,
      start: c.start + delta,
      duration: newDuration,
      in: timed && !c.reverse ? Math.max(0, c.in + delta * c.speed) : c.in,
      keyframes: slicedKeys(c, delta, c.duration),
      fade_in: Math.min(c.fade_in, newDuration),
      fade_out: Math.min(c.fade_out, newDuration),
    };
  } else {
    delta = clamp(
      delta,
      min - c.duration,
      Math.min(
        86400 - c.start - c.duration,
        c.reverse ? availableBefore : availableAfter,
      ),
    );
    const newDuration = c.duration + delta;
    next = {
      ...c,
      duration: newDuration,
      in: timed && c.reverse ? Math.max(0, c.in - delta * c.speed) : c.in,
      keyframes: slicedKeys(c, 0, newDuration),
      fade_in: Math.min(c.fade_in, newDuration),
      fade_out: Math.min(c.fade_out, newDuration),
    };
  }
  return updateClip(p, id, next);
}
export function retimeClip(c: Clip, speed: number): Clip {
  const factor = c.speed / speed,
    newDuration = c.duration * factor;
  return {
    ...c,
    speed,
    duration: newDuration,
    fade_in: Math.min(c.fade_in * factor, newDuration),
    fade_out: Math.min(c.fade_out * factor, newDuration),
    keyframes: Object.fromEntries(
      Object.entries(c.keyframes).map(([k, points]) => [
        k,
        points!.map((p) => ({ ...p, time: p.time * factor })),
      ]),
    ),
  };
}
export function putKeyframe(
  c: Clip,
  key: KeyframeKey,
  time: number,
  value: number,
  tolerance = 1e-6,
): Clip {
  const points = (c.keyframes[key] || []).filter(
    (k) => Math.abs(k.time - time) > tolerance,
  );
  return {
    ...c,
    keyframes: {
      ...c.keyframes,
      [key]: [...points, { time: clamp(time, 0, c.duration), value }].sort(
        (a, b) => a.time - b.time,
      ),
    },
  };
}
export function toggleKeyframe(
  c: Clip,
  key: KeyframeKey,
  time: number,
  tolerance: number,
): Clip {
  const points = c.keyframes[key] || [],
    near = points.find((k) => Math.abs(k.time - time) <= tolerance);
  if (!near)
    return putKeyframe(c, key, time, animated(c, key, time), tolerance);
  const remaining = points.filter((k) => k !== near),
    keys = { ...c.keyframes };
  if (remaining.length) keys[key] = remaining;
  else delete keys[key];
  return { ...c, [key]: animated(c, key, time), keyframes: keys };
}
export function snapTime(
  value: number,
  candidates: number[],
  threshold: number,
) {
  let best = value,
    distance = threshold;
  for (const c of candidates) {
    const d = Math.abs(c - value);
    if (d < distance) {
      best = c;
      distance = d;
    }
  }
  return best;
}
export function snapCandidates(p: Project, except: string, playhead: number) {
  return [
    0,
    playhead,
    ...p.markers.map((m) => m.time),
    ...p.tracks.flatMap((t) =>
      t.clips
        .filter((c) => c.id !== except)
        .flatMap((c) => [c.start, c.start + c.duration]),
    ),
  ];
}
export function relinkMedia(
  p: Project,
  oldId: string,
  newMedia: Media,
): Project {
  return {
    ...p,
    tracks: p.tracks.map((t) => ({
      ...t,
      clips: t.clips.map((c) => {
        if (c.media_id !== oldId) return c;
        if (!accepts(t, newMedia))
          throw new Error("The replacement is not compatible with this track.");
        if (
          newMedia.kind !== "image" &&
          newMedia.duration > 0 &&
          c.in + c.duration * c.speed > newMedia.duration + frameTime(p)
        )
          throw new Error(
            "The replacement is too short for the existing source in/out points.",
          );
        return { ...c, media_id: newMedia.id };
      }),
    })),
  };
}
export type History = { past: Project[]; present: Project; future: Project[] };
export type HistoryAction =
  | { type: "commit"; project: Project }
  | { type: "transient"; project: Project }
  | { type: "gesture"; before: Project; project: Project }
  | { type: "undo" }
  | { type: "redo" }
  | { type: "replace"; project: Project };
export function historyReducer(state: History, action: HistoryAction): History {
  if (action.type === "undo") {
    if (!state.past.length) return state;
    return {
      past: state.past.slice(0, -1),
      present: state.past.at(-1)!,
      future: [state.present, ...state.future],
    };
  }
  if (action.type === "redo") {
    if (!state.future.length) return state;
    return {
      past: [...state.past, state.present].slice(-60),
      present: state.future[0],
      future: state.future.slice(1),
    };
  }
  if (action.type === "replace")
    return { past: [], present: action.project, future: [] };
  if (action.type === "transient") return { ...state, present: action.project };
  const before = action.type === "gesture" ? action.before : state.present;
  if (JSON.stringify(before) === JSON.stringify(action.project))
    return { ...state, present: action.project };
  return {
    past: [...state.past, before].slice(-60),
    present: action.project,
    future: [],
  };
}

const numericRanges: Partial<Record<keyof Clip, [number, number]>> = {
  start: [0, 86400],
  in: [0, 86400],
  duration: [0.001, 86400],
  speed: [0.01, 64],
  opacity: [0, 1],
  volume: [0, 8],
  fade_in: [0, 86400],
  fade_out: [0, 86400],
  x: [-131072, 131072],
  y: [-131072, 131072],
  scale: [0.01, 100],
  rotation: [-36000, 36000],
  exposure: [-10, 10],
  contrast: [0, 4],
  saturation: [0, 4],
  temperature: [-1, 1],
  blur: [0, 100],
  sharpen: [0, 5],
  font_size: [8, 1024],
  box_opacity: [0, 1],
  reference_white_nits: [1, 10000],
};
function object(raw: unknown, name: string): Record<string, unknown> {
  if (!raw || typeof raw !== "object" || Array.isArray(raw))
    throw new Error(`${name} must be an object.`);
  return raw as Record<string, unknown>;
}
function text(raw: unknown, name: string, limit = 200) {
  if (typeof raw !== "string" || raw.length > limit || raw.includes("\0"))
    throw new Error(`Invalid ${name}.`);
  return raw;
}
function number(raw: unknown, name: string, lo: number, hi: number) {
  if (typeof raw !== "number" || !Number.isFinite(raw) || raw < lo || raw > hi)
    throw new Error(`${name} must be between ${lo} and ${hi}.`);
  return raw;
}
function color(raw: unknown) {
  if (typeof raw !== "string" || !/^#[0-9a-f]{6}$/i.test(raw))
    throw new Error("Colors must use #RRGGBB.");
  return raw.toLowerCase();
}
export function validateProject(raw: unknown): Project {
  const source = object(raw, "Project"),
    allowed = [
      "schema_version",
      "id",
      "name",
      "width",
      "height",
      "fps",
      "background",
      "color_space",
      "audio_sample_rate",
      "audio_layout",
      "tracks",
      "markers",
    ];
  for (const k of Object.keys(source))
    if (!allowed.includes(k)) throw new Error(`Unknown project field: ${k}`);
  if (source.schema_version !== 1)
    throw new Error("This project needs schema_version 1.");
  const width = number(source.width, "Width", 16, 8192),
    height = number(source.height, "Height", 16, 8192);
  if (
    !Number.isInteger(width) ||
    !Number.isInteger(height) ||
    width * height > 35651584
  )
    throw new Error("Use an integer canvas within 8192 per axis / 35.65 MP.");
  const fps = text(source.fps, "Frame rate", 32),
    rate = fpsValue(fps);
  if (
    !/^\d+(\/\d+)?$/.test(fps) ||
    !Number.isFinite(rate) ||
    rate < 1 ||
    rate > 120
  )
    throw new Error("Frame rate must be a rational number from 1 to 120.");
  if (
    !["bt709", "bt2020-pq", "bt2020-hlg"].includes(String(source.color_space))
  )
    throw new Error("Unsupported project color space.");
  if (
    ![44100, 48000, 96000].includes(
      Number(source.audio_sample_rate ?? 48000),
    ) ||
    !["mono", "stereo", "5.1", "7.1"].includes(
      String(source.audio_layout ?? "stereo"),
    )
  )
    throw new Error("Unsupported project audio format.");
  if (!Array.isArray(source.tracks) || source.tracks.length > 64)
    throw new Error("Use up to 64 tracks.");
  const ids = new Set<string>();
  const id = (v: unknown) => {
    const n = text(v, "timeline ID", 128);
    if (!n || ids.has(n))
      throw new Error("Timeline IDs must be present and unique.");
    ids.add(n);
    return n;
  };
  let count = 0;
  const tracks = source.tracks.map((item) => {
    const t = object(item, "Track");
    if (
      Object.keys(t).some(
        (k) => !["id", "name", "kind", "muted", "locked", "clips"].includes(k),
      )
    )
      throw new Error("Unknown track setting.");
    if (
      !["video", "audio", "title"].includes(String(t.kind)) ||
      typeof t.muted !== "boolean" ||
      typeof t.locked !== "boolean" ||
      !Array.isArray(t.clips)
    )
      throw new Error("Invalid track.");
    const track: Track = {
      id: id(t.id),
      name: text(t.name, "track name"),
      kind: t.kind as TrackKind,
      muted: t.muted,
      locked: t.locked,
      clips: [],
    };
    track.clips = t.clips.map((value) => {
      const v = object(value, "Clip"),
        defaults = { ...makeClip(), text: "" };
      if (
        Object.keys(v).some(
          (k) => !Object.hasOwn(defaults, k) && k !== "media_id",
        )
      )
        throw new Error("Unknown clip setting.");
      const c = { ...defaults, ...v, id: id(v.id) } as Clip;
      for (const [key, bounds] of Object.entries(numericRanges))
        number(c[key as keyof Clip], key, bounds[0], bounds[1]);
      for (const key of ["reverse", "flip_h", "flip_v"] as const)
        if (typeof c[key] !== "boolean") throw new Error(`Invalid ${key}.`);
      if (
        c.start + c.duration > 86400 ||
        Math.max(c.fade_in, c.fade_out) > c.duration
      )
        throw new Error("Clip timing or fades exceed the clip duration.");
      c.text = text(c.text, "title text", 4000);
      c.color = color(c.color);
      c.box_color = color(c.box_color);
      if (
        !["auto", "linear", "srgb", "bt709", "pq", "hlg"].includes(
          c.input_transfer,
        ) ||
        !["auto", "bt709", "bt2020", "smpte432", "acescg"].includes(
          c.input_primaries,
        )
      )
        throw new Error("Unsupported source color interpretation.");
      if (track.kind === "title") {
        if (v.media_id)
          throw new Error("A title cannot reference a media file.");
        delete c.media_id;
      } else c.media_id = text(v.media_id, "media ID", 128);
      const crop = object(c.crop, "Crop");
      if (
        Object.keys(crop).some(
          (k) => !["left", "right", "top", "bottom"].includes(k),
        )
      )
        throw new Error("Unknown crop field.");
      c.crop = {
        left: number(crop.left ?? 0, "Crop", 0, 0.49),
        right: number(crop.right ?? 0, "Crop", 0, 0.49),
        top: number(crop.top ?? 0, "Crop", 0, 0.49),
        bottom: number(crop.bottom ?? 0, "Crop", 0, 0.49),
      };
      const keys = object(c.keyframes, "Keyframes");
      c.keyframes = {};
      for (const [key, points] of Object.entries(keys)) {
        if (
          !["opacity", "x", "y", "scale", "volume"].includes(key) ||
          !Array.isArray(points) ||
          points.length > 1000
        )
          throw new Error("Invalid keyframes.");
        const seen = new Set<number>();
        c.keyframes[key as KeyframeKey] = points
          .map((point) => {
            const k = object(point, "Keyframe"),
              time = number(k.time, "Keyframe time", 0, c.duration),
              bounds = numericRanges[key as keyof Clip]!;
            if (
              seen.has(time) ||
              Object.keys(k).some((n) => !["time", "value"].includes(n))
            )
              throw new Error("Duplicate or unknown keyframe.");
            seen.add(time);
            return {
              time,
              value: number(k.value, "Keyframe value", ...bounds),
            };
          })
          .sort((a, b) => a.time - b.time);
      }
      const lens = object(c.lens, "Lens settings");
      c.lens = {};
      if (Object.keys(lens).length) {
        c.lens = { ...lensDefaults };
        for (const [key, value] of Object.entries(lens)) {
          if (!Object.hasOwn(lensControls, key))
            throw new Error(`Unknown lens control ${key}.`);
          const r = lensControls[key as keyof typeof lensControls];
          c.lens[key] = number(value, key, r.min, r.max);
        }
      }
      if (track.kind === "title") {
        if (
          c.in ||
          c.speed !== 1 ||
          c.reverse ||
          c.volume !== 1 ||
          c.keyframes.volume?.length
        )
          throw new Error(
            "Title clips do not have source trimming, speed, or audio.",
          );
      } else {
        if (
          c.text ||
          c.font_size !== 64 ||
          c.color !== "#ffffff" ||
          c.box_color !== "#000000" ||
          c.box_opacity
        )
          throw new Error("Text styling requires a title track.");
        if (track.kind === "audio") {
          const audioFields = [
            "id",
            "media_id",
            "start",
            "in",
            "duration",
            "speed",
            "reverse",
            "volume",
            "fade_in",
            "fade_out",
            "keyframes",
          ];
          for (const [key, value] of Object.entries(defaults))
            if (
              !audioFields.includes(key) &&
              JSON.stringify(c[key as keyof Clip]) !== JSON.stringify(value)
            )
              throw new Error(
                "Audio tracks accept sound settings only. Move this clip to a video track for picture effects.",
              );
          if (Object.keys(c.keyframes).some((k) => k !== "volume"))
            throw new Error("Audio tracks support volume keyframes only.");
        }
      }
      count++;
      return c;
    });
    return track;
  });
  if (count > 2000) throw new Error("Use up to 2,000 clips.");
  if (!Array.isArray(source.markers) || source.markers.length > 2000)
    throw new Error("Invalid markers.");
  const markers = source.markers.map((item) => {
    const m = object(item, "Marker");
    if (
      Object.keys(m).some((k) => !["id", "time", "label", "color"].includes(k))
    )
      throw new Error("Unknown marker setting.");
    return {
      id: text(m.id, "marker ID", 128),
      time: number(m.time, "Marker time", 0, 86400),
      label: text(m.label, "marker label"),
      color: color(m.color),
    };
  });
  return {
    schema_version: 1,
    ...(typeof source.id === "string" ? { id: source.id } : {}),
    name: text(source.name, "project name"),
    width,
    height,
    fps,
    background: color(source.background),
    color_space: source.color_space as Project["color_space"],
    audio_sample_rate: (source.audio_sample_rate ??
      48000) as Project["audio_sample_rate"],
    audio_layout: (source.audio_layout ?? "stereo") as Project["audio_layout"],
    tracks,
    markers,
  };
}
