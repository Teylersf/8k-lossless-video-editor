import { strict as assert } from "node:assert";
import { test } from "node:test";
import {
  accepts,
  adoptImportedSettings,
  animated,
  duplicateClip,
  findClip,
  historyReducer,
  insertClip,
  makeClip,
  moveClip,
  newProject,
  newImportSettings,
  parseTimecode,
  putKeyframe,
  relinkMedia,
  removeClip,
  retimeClip,
  snapTime,
  splitClip,
  timecode,
  trimClip,
  validateProject,
} from "../src/editor_state.ts";
import type { Media, Project } from "../src/types.ts";

const media: Media = {
  id: "media-one",
  name: "Actual test source.mov",
  kind: "video",
  width: 1920,
  height: 1080,
  fps: 24,
  fps_exact: "24/1",
  duration: 20,
  codec: "h264",
  container: ".mov",
  pixel_format: "yuv420p",
  bit_depth: 8,
  audio_streams: 1,
  transfer: "bt709",
  color_primaries: "bt709",
  color_space: "bt709",
  color_range: "tv",
  bytes: 100,
  sha256: "test",
  waveform: [0, 0.25, 0.5],
  warnings: [],
};
function sequence() {
  let p = newProject("Tests");
  const clip = { ...makeClip(media, 2), in: 3, duration: 10 };
  p = insertClip(p, clip, p.tracks[0].id, media);
  return { p, id: clip.id };
}
test("first audio import adopts supported audio without consuming first-video defaults", () => {
  const p = newProject(),
    settings = newImportSettings();
  const audio = adoptImportedSettings(
    p,
    {
      ...media,
      kind: "audio",
      width: 0,
      height: 0,
      audio_layout: "5.1",
      audio_sample_rate: 96000,
    },
    settings,
  );
  assert.equal(audio.project.audio_layout, "5.1");
  assert.equal(audio.project.audio_sample_rate, 96000);
  assert.equal(audio.project.width, 1920);
  assert.deepEqual(audio.settings, {
    video: false,
    audioLayout: true,
    audioSampleRate: true,
  });
  const video = adoptImportedSettings(
    audio.project,
    {
      ...media,
      width: 7680,
      height: 4320,
      fps_exact: "30000/1001",
      audio_layout: "stereo",
      audio_sample_rate: 48000,
    },
    audio.settings,
  );
  assert.equal(video.project.width, 7680);
  assert.equal(video.project.height, 4320);
  assert.equal(video.project.fps, "30000/1001");
  assert.equal(video.project.audio_layout, "5.1");
  assert.equal(video.project.audio_sample_rate, 96000);
  assert.equal(video.settings.video, true);
  assert.equal(p.audio_layout, "stereo");
  assert.deepEqual(settings, newImportSettings());
  validateProject(video.project);
});
test("unknown or unsupported audio metadata leaves each default open for a known source", () => {
  const p = newProject();
  for (const metadata of [
    {},
    { audio_layout: "unknown", audio_sample_rate: 0 },
    { audio_layout: "7.1.4", audio_sample_rate: 192000 },
    { audio_layout: "mono", audio_sample_rate: 44100, audio_streams: 0 },
  ]) {
    const result = adoptImportedSettings(
      p,
      { ...media, kind: "audio", ...metadata },
      newImportSettings(),
    );
    assert.equal(result.project, p);
    assert.deepEqual(result.settings, newImportSettings());
  }
  const partial = adoptImportedSettings(
    p,
    {
      ...media,
      kind: "audio",
      audio_layout: "unknown",
      audio_sample_rate: 44100,
    },
    newImportSettings(),
  );
  assert.equal(partial.settings.audioLayout, false);
  assert.equal(partial.settings.audioSampleRate, true);
  const known = adoptImportedSettings(
    partial.project,
    {
      ...media,
      kind: "audio",
      audio_layout: "mono",
      audio_sample_rate: 96000,
    },
    partial.settings,
  );
  assert.equal(known.project.audio_layout, "mono");
  assert.equal(known.project.audio_sample_rate, 44100);
});
test("explicit sequence settings and restored projects retain their audio and video choices", () => {
  const p: Project = {
    ...newProject(),
    width: 3840,
    height: 2160,
    fps: "25/1",
    audio_layout: "7.1",
    audio_sample_rate: 96000,
  };
  const result = adoptImportedSettings(
    p,
    { ...media, audio_layout: "mono", audio_sample_rate: 44100 },
    newImportSettings(true),
  );
  assert.equal(result.project, p);
  assert.deepEqual(result.settings, newImportSettings(true));
});
test("subsequent sources cannot overwrite independently adopted settings", () => {
  const first = adoptImportedSettings(
    newProject(),
    { ...media, audio_layout: "mono", audio_sample_rate: 44100 },
    newImportSettings(),
  );
  const next = adoptImportedSettings(
    first.project,
    {
      ...media,
      width: 7680,
      height: 4320,
      fps_exact: "60/1",
      audio_layout: "5.1",
      audio_sample_rate: 96000,
    },
    first.settings,
  );
  assert.equal(next.project, first.project);
  assert.equal(next.project.width, 1920);
  assert.equal(next.project.audio_layout, "mono");
  assert.equal(next.project.audio_sample_rate, 44100);
});
test("project validation rejects unknown, nonfinite, duplicate and excessive values", () => {
  const { p, id } = sequence();
  assert.equal(validateProject(p).tracks[0].clips[0].id, id);
  for (const mutate of [
    (p: Project) => Object.assign(p, { rogue: 1 }),
    (p: Project) => {
      p.width = 9000;
    },
    (p: Project) => {
      p.tracks[0].clips[0].duration = NaN;
    },
    (p: Project) => {
      p.tracks[1].id = p.tracks[0].id;
    },
    (p: Project) => {
      p.tracks[0].clips[0].lens = { unknown: 1 };
    },
    (p: Project) => {
      p.tracks[0].clips[0].fade_in = 12;
    },
  ]) {
    const v = structuredClone(p);
    mutate(v);
    assert.throws(() => validateProject(v));
  }
  assert.throws(() => validateProject({ ...p, fps: "24/0" }));
  assert.throws(() => validateProject({ ...p, width: 8192, height: 8192 }));
});
test("split keeps normal and reverse source intervals and interpolated animation", () => {
  for (const reverse of [false, true]) {
    const { p, id } = sequence();
    const c = p.tracks[0].clips[0];
    c.reverse = reverse;
    c.keyframes = {
      opacity: [
        { time: 0, value: 0 },
        { time: 10, value: 1 },
      ],
    };
    const before = JSON.stringify(p);
    const result = splitClip(p, id, 6);
    const [left, right] = result.project.tracks[0].clips;
    assert.equal(left.duration, 4);
    assert.equal(right.duration, 6);
    assert.equal(right.start, 6);
    assert.equal(left.in, reverse ? 9 : 3);
    assert.equal(right.in, reverse ? 3 : 7);
    assert.equal(animated(left, "opacity", 4), 0.4);
    assert.equal(animated(right, "opacity", 0), 0.4);
    assert.equal(JSON.stringify(p), before);
    assert.notEqual(right.id, left.id);
    validateProject(result.project);
  }
  const { p, id } = sequence();
  assert.throws(() => splitClip(p, id, 2));
  assert.throws(() => splitClip(p, id, 12));
});
test("trims respect source bounds in both playback directions", () => {
  for (const reverse of [false, true]) {
    const { p, id } = sequence();
    p.tracks[0].clips[0].reverse = reverse;
    const left = trimClip(p, id, "left", 2, { [media.id]: media }).tracks[0]
      .clips[0];
    assert.equal(left.start, 4);
    assert.equal(left.duration, 8);
    assert.equal(left.in, reverse ? 3 : 5);
    const right = trimClip(p, id, "right", -2, { [media.id]: media }).tracks[0]
      .clips[0];
    assert.equal(right.duration, 8);
    assert.equal(right.in, reverse ? 5 : 3);
    const extended = trimClip(p, id, "right", 100, { [media.id]: media })
      .tracks[0].clips[0];
    assert.equal(extended.duration, reverse ? 13 : 17);
    assert.equal(extended.in, reverse ? 0 : 3);
  }
});
test("speed changes preserve source consumption and retime keyframes", () => {
  const c = makeClip(media);
  c.duration = 10;
  c.keyframes = {
    x: [
      { time: 0, value: 0 },
      { time: 10, value: 100 },
    ],
  };
  c.fade_in = 2;
  const faster = retimeClip(c, 2);
  assert.equal(faster.duration, 5);
  assert.equal(faster.duration * faster.speed, c.duration * c.speed);
  assert.equal(faster.fade_in, 1);
  assert.equal(faster.keyframes.x![1].time, 5);
});
test("history commits whole gestures once, clears redo and stays bounded", () => {
  const { p, id } = sequence();
  let h = { past: [] as Project[], present: p, future: [] as Project[] };
  const moved = moveClip(p, id, 4);
  h = historyReducer(h, { type: "transient", project: moved });
  assert.equal(h.past.length, 0);
  h = historyReducer(h, { type: "gesture", before: p, project: moved });
  assert.equal(h.past.length, 1);
  h = historyReducer(h, { type: "undo" });
  assert.equal(findClip(h.present, id)!.clip.start, 2);
  h = historyReducer(h, { type: "redo" });
  assert.equal(findClip(h.present, id)!.clip.start, 4);
  for (let i = 0; i < 80; i++)
    h = historyReducer(h, {
      type: "commit",
      project: { ...h.present, name: String(i) },
    });
  assert.equal(h.past.length, 60);
  assert.equal(h.future.length, 0);
});
test("locks protect edits and cross-track moves enforce media compatibility", () => {
  const { p, id } = sequence();
  p.tracks[0].locked = true;
  assert.throws(() => moveClip(p, id, 3));
  assert.throws(() => removeClip(p, id));
  assert.throws(() => splitClip(p, id, 5));
  assert.throws(() => duplicateClip(p, id));
  p.tracks[0].locked = false;
  const moved = moveClip(p, id, 4, p.tracks[3].id, { [media.id]: media });
  assert.equal(moved.tracks[0].clips.length, 0);
  assert.equal(moved.tracks[3].clips.length, 1);
  assert.throws(() =>
    moveClip(p, id, 3, p.tracks[2].id, { [media.id]: media }),
  );
  assert.equal(
    accepts(p.tracks[3], { ...media, kind: "image", audio_streams: 0 }),
    false,
  );
  assert.equal(
    accepts(p.tracks[3], { ...media, kind: "video", audio_streams: 0 }),
    false,
  );
});
test("relink preserves edit points and rejects incompatible or too-short replacements", () => {
  const { p } = sequence();
  const next = relinkMedia(p, media.id, { ...media, id: "replacement" });
  assert.equal(next.tracks[0].clips[0].media_id, "replacement");
  assert.equal(next.tracks[0].clips[0].in, 3);
  assert.throws(() =>
    relinkMedia(p, media.id, { ...media, id: "short", duration: 4 }),
  );
  assert.throws(() => relinkMedia(p, media.id, { ...media, kind: "audio" }));
});
test("keyframe upsert and snapping are deterministic", () => {
  let c = makeClip(media);
  c = putKeyframe(c, "opacity", 1, 0.25);
  c = putKeyframe(c, "opacity", 1, 0.5);
  assert.equal(c.keyframes.opacity!.length, 1);
  assert.equal(animated(c, "opacity", 1), 0.5);
  assert.equal(snapTime(3.04, [1, 3, 8], 0.1), 3);
  assert.equal(snapTime(3.2, [3], 0.1), 3.2);
});
test("timecode and JSON round trips retain the contract", () => {
  assert.equal(timecode(1, "24/1"), "00:00:01:00");
  assert.equal(parseTimecode("00:00:01:12", "24/1"), 1.5);
  assert.equal(parseTimecode("2.25", "24/1"), 2.25);
  const { p } = sequence();
  assert.deepEqual(
    validateProject(JSON.parse(JSON.stringify(p))),
    validateProject(p),
  );
  assert.throws(() => parseTimecode("00:00:00:24", "24/1"));
});
test("source interpretation and multichannel audio settings survive project round trips", () => {
  const { p } = sequence();
  p.audio_layout = "5.1";
  p.audio_sample_rate = 96000;
  Object.assign(p.tracks[0].clips[0], {
    input_transfer: "linear",
    input_primaries: "acescg",
    reference_white_nits: 203,
  });
  const parsed = validateProject(JSON.parse(JSON.stringify(p)));
  assert.equal(parsed.audio_layout, "5.1");
  assert.equal(parsed.audio_sample_rate, 96000);
  assert.equal(parsed.tracks[0].clips[0].input_primaries, "acescg");
  assert.throws(() => validateProject({ ...p, audio_layout: "surround" }));
  assert.throws(() => validateProject({ ...p, audio_sample_rate: 32000 }));
  p.tracks[0].clips[0].reference_white_nits = 0;
  assert.throws(() => validateProject(p));
});
test("audio and title clips reject controls that have no rendered meaning", () => {
  const p = newProject();
  const audio = { ...makeClip({ ...media, kind: "audio" }), duration: 4 };
  p.tracks[3].clips = [audio];
  validateProject(p);
  for (const patch of [
    { x: 1 },
    { lens: { amount: 0.5 } },
    { input_transfer: "linear" },
    { keyframes: { opacity: [{ time: 0, value: 0.5 }] } },
    { text: "Not an audio setting" },
  ]) {
    const bad = structuredClone(p);
    Object.assign(bad.tracks[3].clips[0], patch);
    assert.throws(() => validateProject(bad));
  }
  p.tracks[2].clips = [makeClip()];
  validateProject(p);
  p.tracks[2].clips[0].speed = 2;
  assert.throws(() => validateProject(p));
});
test("extreme trims preserve valid source consumption and do not mutate their input", () => {
  for (const reverse of [false, true])
    for (const edge of ["left", "right"] as const)
      for (const delta of [-100, -3, -0.5, 0, 0.5, 3, 100]) {
        const { p, id } = sequence();
        p.tracks[0].clips[0].reverse = reverse;
        p.tracks[0].clips[0].keyframes = {
          x: [
            { time: 0, value: 1 },
            { time: 4, value: 3 },
            { time: 10, value: 0 },
          ],
        };
        const before = JSON.stringify(p);
        const next = trimClip(p, id, edge, delta, { [media.id]: media }),
          c = next.tracks[0].clips[0];
        validateProject(next);
        assert.ok(c.in >= -1e-9);
        assert.ok(c.in + c.duration * c.speed <= media.duration + 1e-9);
        assert.ok(c.start >= 0);
        assert.equal(JSON.stringify(p), before);
      }
});
test("project color and lens controls reject missing, unknown and out-of-range inputs", () => {
  const { p } = sequence();
  for (const lens of [
    { amount: NaN },
    { flare_length: 0 },
    { amount: 2 },
    { fakeDepth: 1 },
  ]) {
    const bad = structuredClone(p);
    bad.tracks[0].clips[0].lens = lens;
    assert.throws(() => validateProject(bad));
  }
  const valid = structuredClone(p);
  valid.tracks[0].clips[0].lens = { amount: 0.4 };
  assert.equal(validateProject(valid).tracks[0].clips[0].lens.amount, 0.4);
  assert.equal(validateProject(valid).tracks[0].clips[0].lens.flare, 0.55);
});
test("title splitting keeps source-free timing and animated position continuity", () => {
  const p = newProject(),
    title = makeClip();
  title.duration = 8;
  title.keyframes = {
    x: [
      { time: 0, value: -20 },
      { time: 8, value: 20 },
    ],
  };
  p.tracks[2].clips = [title];
  const next = splitClip(p, title.id, 3).project,
    [left, right] = next.tracks[2].clips;
  validateProject(next);
  assert.equal(right.in, 0);
  assert.equal(right.media_id, undefined);
  assert.equal(left.duration, 3);
  assert.equal(right.duration, 5);
  assert.equal(animated(left, "x", 3), animated(right, "x", 0));
  assert.equal(right.text, title.text);
});
