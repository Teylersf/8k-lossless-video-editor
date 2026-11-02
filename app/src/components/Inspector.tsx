import { useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Diamond,
  LockKeyhole,
  RotateCcw,
  SlidersHorizontal,
  Trash2,
} from "lucide-react";
import type { Clip, KeyframeKey, MediaMap, Project } from "../types";
import {
  animated,
  clamp,
  findClip,
  frameTime,
  putKeyframe,
  retimeClip,
  timecode,
  toggleKeyframe,
  updateClip,
} from "../editor_state";
import { lensControls, lensDefaults, lensProfiles } from "../lens";

type Props = {
  project: Project;
  selected: string | null;
  media: MediaMap;
  playhead: number;
  onChange: (project: Project) => void;
  onSeek: (time: number) => void;
  onError: (error: unknown) => void;
};
type NumberFieldProps = {
  label: string;
  value: number;
  min?: number;
  max?: number;
  step?: number;
  onChange: (value: number) => void;
  unit?: string;
  children?: React.ReactNode;
};
export function NumberField({
  label,
  value,
  min,
  max,
  step = 0.01,
  onChange,
  unit,
  children,
}: NumberFieldProps) {
  const [draft, setDraft] = useState<string | null>(null);
  return (
    <label className="number-field">
      <span>
        {label}
        {children}
      </span>
      <div>
        <input
          type="number"
          aria-label={label}
          min={min}
          max={max}
          step={step}
          value={draft ?? Number(value.toFixed(5))}
          onFocus={() => setDraft(String(Number(value.toFixed(5))))}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => {
            if (draft !== null) {
              const n = Number(draft);
              if (draft.trim() && Number.isFinite(n))
                onChange(clamp(n, min ?? -Infinity, max ?? Infinity));
              setDraft(null);
            }
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
            if (e.key === "Escape") {
              setDraft(null);
              e.currentTarget.blur();
            }
          }}
        />
        {unit && <small>{unit}</small>}
      </div>
    </label>
  );
}
export function Inspector({
  project,
  selected,
  media,
  playhead,
  onChange,
  onSeek,
  onError,
}: Props) {
  const [tab, setTab] = useState(() =>
      findClip(project, selected)?.track.kind === "title" ? "Title" : "Video",
    ),
    [chosenKey, setKey] = useState<KeyframeKey>("opacity");
  const found = findClip(project, selected),
    clip = found?.clip,
    track = found?.track,
    source = media[clip?.media_id || ""],
    local = clip ? clamp(playhead - clip.start, 0, clip.duration) : 0;
  const key: KeyframeKey =
    track?.kind === "audio"
      ? "volume"
      : track?.kind === "title" && chosenKey === "volume"
        ? "opacity"
        : chosenKey;
  const activeTab =
    track?.kind === "audio"
      ? "Audio"
      : track?.kind === "title" && tab === "Audio"
        ? "Title"
        : track?.kind !== "title" && tab === "Title"
          ? "Video"
          : tab;
  const change = (patch: Partial<Clip>) => {
    if (!clip) return;
    try {
      onChange(updateClip(project, clip.id, patch));
    } catch (e) {
      onError(e);
    }
  };
  const field = (
    label: string,
    k: keyof Clip,
    min: number,
    max: number,
    step = 0.01,
    unit?: string,
  ) => (
    <NumberField
      key={k}
      label={label}
      value={clip![k] as number}
      min={min}
      max={max}
      step={step}
      unit={unit}
      onChange={(n) => change({ [k]: n })}
    />
  );
  const animatedField = (
    label: string,
    k: KeyframeKey,
    min: number,
    max: number,
    step = 0.01,
  ) => {
    const points = clip!.keyframes[k] || [],
      near = points.some(
        (p) => Math.abs(p.time - local) <= frameTime(project) / 2,
      );
    return (
      <NumberField
        label={label}
        key={k}
        value={animated(clip!, k, local)}
        min={min}
        max={max}
        step={step}
        onChange={(n) => {
          try {
            onChange(
              updateClip(
                project,
                clip!.id,
                points.length
                  ? putKeyframe(clip!, k, local, n, frameTime(project) / 2)
                  : { [k]: n },
              ),
            );
          } catch (e) {
            onError(e);
          }
        }}
      >
        <button
          type="button"
          className={`key-toggle ${near ? "active" : ""}`}
          title={`${near ? "Remove" : "Add"} ${label} keyframe at playhead`}
          aria-label={`${near ? "Remove" : "Add"} ${label} keyframe`}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            try {
              onChange(
                updateClip(
                  project,
                  clip!.id,
                  toggleKeyframe(clip!, k, local, frameTime(project) / 2),
                ),
              );
              setKey(k);
            } catch (error) {
              onError(error);
            }
          }}
        >
          <Diamond size={11} fill={near ? "currentColor" : "none"} />
        </button>
      </NumberField>
    );
  };
  return (
    <aside className="inspector panel">
      <div className="panel-heading">
        <span>INSPECTOR</span>
        <SlidersHorizontal size={14} />
      </div>
      {!clip ? (
        <div className="inspector-empty">
          <SlidersHorizontal size={27} />
          <strong>Every detail, in reach</strong>
          <p>
            Select a timeline clip to adjust its picture, sound, and motion.
          </p>
          <div className="shortcut-list">
            <span>
              Split clip <kbd>S</kbd>
            </span>
            <span>
              Play program <kbd>Space</kbd>
            </span>
            <span>
              Undo <kbd>⌘ / Ctrl Z</kbd>
            </span>
          </div>
        </div>
      ) : (
        <>
          <div className="inspector-title">
            <span className={`kind-dot ${track!.kind}`} />
            <div>
              <strong>
                {track!.kind === "title"
                  ? "Title"
                  : source?.name || "Offline media"}
              </strong>
              <small>
                {track!.name} · {clip.duration.toFixed(3)}s
              </small>
            </div>
            {track!.locked && <LockKeyhole size={15} />}
          </div>
          <div className="inspector-tabs">
            {(track!.kind === "title"
              ? ["Title", "Video", "Color", "Lens"]
              : track!.kind === "audio"
                ? ["Audio"]
                : ["Video", "Color", "Audio", "Lens"]
            ).map((t) => (
              <button
                key={t}
                className={activeTab === t ? "active" : ""}
                onClick={() => setTab(t)}
              >
                {t}
              </button>
            ))}
          </div>
          <div className="inspector-scroll">
            <fieldset disabled={track!.locked}>
              <details open>
                <summary>Timing</summary>
                <div className="fields">
                  {field(
                    "Timeline start",
                    "start",
                    0,
                    86400 - clip.duration,
                    frameTime(project),
                    "s",
                  )}
                  {track!.kind !== "title" &&
                    field(
                      "Source in",
                      "in",
                      0,
                      source && source.kind !== "image"
                        ? Math.max(
                            0,
                            source.duration - clip.duration * clip.speed,
                          )
                        : 86400,
                      frameTime(project),
                      "s",
                    )}
                  {field(
                    "Duration",
                    "duration",
                    0.001,
                    source && source.kind !== "image"
                      ? Math.max(
                          0.001,
                          (source.duration - clip.in) / clip.speed,
                        )
                      : 86400 - clip.start,
                    frameTime(project),
                    "s",
                  )}
                  {track!.kind !== "title" && (
                    <NumberField
                      label="Playback speed"
                      value={clip.speed}
                      min={0.01}
                      max={64}
                      step={0.05}
                      unit="×"
                      onChange={(n) => {
                        const next = retimeClip(clip, n);
                        if (next.start + next.duration > 86400) {
                          onError(
                            new Error(
                              "The retimed clip exceeds the 24-hour timeline.",
                            ),
                          );
                          return;
                        }
                        change(next);
                      }}
                    />
                  )}
                  {track!.kind !== "title" && (
                    <label className="check-field">
                      <input
                        type="checkbox"
                        checked={clip.reverse}
                        onChange={(e) => {
                          if (
                            e.target.checked &&
                            source &&
                            (track!.kind === "audio" ||
                              source.audio_streams > 0) &&
                            (clip.volume > 0 ||
                              clip.keyframes.volume?.length) &&
                            clip.duration * clip.speed > 60
                          ) {
                            onError(
                              new Error(
                                "Reverse with audio is limited to 60 seconds of source. Trim the clip or set volume to zero.",
                              ),
                            );
                            return;
                          }
                          change({ reverse: e.target.checked });
                        }}
                      />{" "}
                      Reverse source
                    </label>
                  )}
                </div>
              </details>
              {activeTab === "Video" && (
                <>
                  <details open>
                    <summary>
                      Transform & compositing{" "}
                      <button
                        className="icon-button"
                        title="Reset transform"
                        aria-label="Reset transform"
                        onClick={(e) => {
                          e.preventDefault();
                          change({
                            x: 0,
                            y: 0,
                            scale: 1,
                            rotation: 0,
                            flip_h: false,
                            flip_v: false,
                            opacity: 1,
                            keyframes: {
                              ...clip.keyframes,
                              x: [],
                              y: [],
                              scale: [],
                              opacity: [],
                            },
                          });
                        }}
                      >
                        <RotateCcw size={12} />
                      </button>
                    </summary>
                    <div className="fields">
                      {animatedField("Position X", "x", -131072, 131072, 1)}
                      {animatedField("Position Y", "y", -131072, 131072, 1)}
                      {animatedField("Scale", "scale", 0.01, 100, 0.01)}
                      {field("Rotation", "rotation", -36000, 36000, 0.1, "°")}
                      {animatedField("Opacity", "opacity", 0, 1, 0.01)}
                      <div className="check-pair">
                        <label>
                          <input
                            type="checkbox"
                            checked={clip.flip_h}
                            onChange={(e) =>
                              change({ flip_h: e.target.checked })
                            }
                          />{" "}
                          Flip H
                        </label>
                        <label>
                          <input
                            type="checkbox"
                            checked={clip.flip_v}
                            onChange={(e) =>
                              change({ flip_v: e.target.checked })
                            }
                          />{" "}
                          Flip V
                        </label>
                      </div>
                    </div>
                  </details>
                  <details>
                    <summary>Crop</summary>
                    <div className="fields">
                      {(["left", "right", "top", "bottom"] as const).map(
                        (side) => (
                          <NumberField
                            key={side}
                            label={`Crop ${side}`}
                            value={clip.crop[side] * 100}
                            min={0}
                            max={49}
                            step={0.1}
                            unit="%"
                            onChange={(n) =>
                              change({
                                crop: { ...clip.crop, [side]: n / 100 },
                              })
                            }
                          />
                        ),
                      )}
                    </div>
                  </details>
                  <details open>
                    <summary>Fades</summary>
                    <div className="fields">
                      {field("Fade in", "fade_in", 0, clip.duration, 0.05, "s")}
                      {field(
                        "Fade out",
                        "fade_out",
                        0,
                        clip.duration,
                        0.05,
                        "s",
                      )}
                    </div>
                    <p className="field-note">
                      Fades affect picture and sound. Overlap clips on separate
                      tracks for a crossfade.
                    </p>
                  </details>
                </>
              )}
              {activeTab === "Color" && (
                <>
                  <details open>
                    <summary>
                      Primary correction{" "}
                      <button
                        className="icon-button"
                        title="Reset correction"
                        aria-label="Reset correction"
                        onClick={(e) => {
                          e.preventDefault();
                          change({
                            exposure: 0,
                            contrast: 1,
                            saturation: 1,
                            temperature: 0,
                            blur: 0,
                            sharpen: 0,
                          });
                        }}
                      >
                        <RotateCcw size={12} />
                      </button>
                    </summary>
                    <div className="fields">
                      {field("Exposure", "exposure", -10, 10, 0.1, "EV")}
                      {field("Contrast", "contrast", 0, 4)}
                      {field("Saturation", "saturation", 0, 4)}
                      {field("Temperature", "temperature", -1, 1)}
                      {field("Blur", "blur", 0, 100, 0.25, "px")}
                      {field("Sharpen", "sharpen", 0, 5, 0.05)}
                    </div>
                  </details>
                  {track!.kind !== "title" && (
                    <details open>
                      <summary>Source interpretation</summary>
                      <div className="fields">
                        <label className="select-field">
                          Transfer
                          <select
                            value={clip.input_transfer}
                            onChange={(e) =>
                              change({
                                input_transfer: e.target
                                  .value as Clip["input_transfer"],
                              })
                            }
                          >
                            {[
                              ["auto", "Use source metadata"],
                              ["linear", "Linear"],
                              ["srgb", "sRGB"],
                              ["bt709", "Rec.709"],
                              ["pq", "PQ / ST2084"],
                              ["hlg", "HLG"],
                            ].map(([id, label]) => (
                              <option key={id} value={id}>
                                {label}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="select-field">
                          Primaries
                          <select
                            value={clip.input_primaries}
                            onChange={(e) =>
                              change({
                                input_primaries: e.target
                                  .value as Clip["input_primaries"],
                              })
                            }
                          >
                            {[
                              ["auto", "Use source metadata"],
                              ["bt709", "Rec.709"],
                              ["bt2020", "Rec.2020"],
                              ["smpte432", "Display P3 D65"],
                              ["acescg", "ACEScg / AP1"],
                            ].map(([id, label]) => (
                              <option key={id} value={id}>
                                {label}
                              </option>
                            ))}
                          </select>
                        </label>
                        {field(
                          "Reference white",
                          "reference_white_nits",
                          1,
                          10000,
                          1,
                          "nits",
                        )}
                      </div>
                      <p className="field-note">
                        Overrides apply to this clip. Assign the actual source
                        encoding for EXR or missing metadata; these controls do
                        not recover clipped detail.
                      </p>
                    </details>
                  )}
                </>
              )}
              {activeTab === "Audio" && (
                <>
                  <details open>
                    <summary>Mix</summary>
                    <div className="fields">
                      {animatedField("Volume", "volume", 0, 8, 0.01)}
                      {field("Fade in", "fade_in", 0, clip.duration, 0.05, "s")}
                      {field(
                        "Fade out",
                        "fade_out",
                        0,
                        clip.duration,
                        0.05,
                        "s",
                      )}
                      <button
                        onClick={() =>
                          change({ volume: clip.volume === 0 ? 1 : 0 })
                        }
                      >
                        {clip.volume === 0
                          ? "Restore source audio"
                          : "Mute this clip"}
                      </button>
                    </div>
                    <p className="field-note">
                      {track!.kind === "title" ||
                      (source &&
                        !source.audio_streams &&
                        source.kind !== "audio")
                        ? "This clip has no source audio."
                        : "Source audio follows clip trims, speed, reverse, and fades. Track mute silences all its clips."}
                    </p>
                  </details>
                </>
              )}
              {activeTab === "Title" && track!.kind === "title" && (
                <>
                  <details open>
                    <summary>Typography</summary>
                    <div className="fields">
                      <label className="textarea-field">
                        Title text
                        <textarea
                          value={clip.text}
                          maxLength={4000}
                          rows={4}
                          onChange={(e) => change({ text: e.target.value })}
                        />
                      </label>
                      {field("Font size", "font_size", 8, 1024, 1, "px")}
                      <label className="color-field">
                        Text color
                        <input
                          type="color"
                          value={clip.color}
                          onChange={(e) => change({ color: e.target.value })}
                        />
                      </label>
                      <label className="color-field">
                        Box color
                        <input
                          type="color"
                          value={clip.box_color}
                          onChange={(e) =>
                            change({ box_color: e.target.value })
                          }
                        />
                      </label>
                      {field("Box opacity", "box_opacity", 0, 1)}
                    </div>
                  </details>
                  <details open>
                    <summary>Position & motion</summary>
                    <div className="fields">
                      {animatedField("Position X", "x", -131072, 131072, 1)}
                      {animatedField("Position Y", "y", -131072, 131072, 1)}
                      {animatedField("Scale", "scale", 0.01, 100)}
                      {animatedField("Opacity", "opacity", 0, 1)}
                      {field("Fade in", "fade_in", 0, clip.duration, 0.05, "s")}
                      {field(
                        "Fade out",
                        "fade_out",
                        0,
                        clip.duration,
                        0.05,
                        "s",
                      )}
                    </div>
                  </details>
                </>
              )}
              {activeTab === "Lens" && (
                <>
                  <details open>
                    <summary>Lens & film</summary>
                    <div className="fields">
                      <div className="preset-buttons">
                        {Object.entries(lensProfiles).map(
                          ([name, settings]) => (
                            <button
                              key={name}
                              className={
                                !Object.keys(clip.lens).length && name === "off"
                                  ? "active"
                                  : ""
                              }
                              onClick={() => change({ lens: { ...settings } })}
                            >
                              {name}
                            </button>
                          ),
                        )}
                      </div>
                      {!!Object.keys(clip.lens).length &&
                        Object.entries(lensControls).map(([name, r]) => (
                          <label className="range-field" key={name}>
                            <span>
                              {r.label}
                              <output>
                                {(
                                  clip.lens[name] ??
                                  lensDefaults[
                                    name as keyof typeof lensDefaults
                                  ]
                                ).toFixed(2)}
                              </output>
                            </span>
                            <input
                              type="range"
                              min={r.min}
                              max={r.max}
                              step={0.01}
                              value={
                                clip.lens[name] ??
                                lensDefaults[name as keyof typeof lensDefaults]
                              }
                              onChange={(e) =>
                                change({
                                  lens: {
                                    ...clip.lens,
                                    [name]: Number(e.target.value),
                                  },
                                })
                              }
                            />
                          </label>
                        ))}
                    </div>
                    <p className="field-note">
                      Linear-light flares, bloom, ghosts, grain, and lens
                      shaping. Oval bokeh uses a focus-band approximation,
                      without depth estimation or optical reconstruction.
                    </p>
                  </details>
                </>
              )}
              <details
                open={Object.values(clip.keyframes).some((p) => p?.length)}
              >
                <summary>Keyframe animation</summary>
                <div className="fields">
                  <select
                    aria-label="Animated property"
                    value={key}
                    onChange={(e) => setKey(e.target.value as KeyframeKey)}
                  >
                    {(track!.kind === "audio"
                      ? ["volume"]
                      : track!.kind === "title"
                        ? ["opacity", "x", "y", "scale"]
                        : ["opacity", "x", "y", "scale", "volume"]
                    ).map((k) => (
                      <option key={k}>{k}</option>
                    ))}
                  </select>
                  <div className="key-tools">
                    <button
                      className="icon-button bordered"
                      aria-label="Previous keyframe"
                      onClick={() => {
                        const point = (clip.keyframes[key] || [])
                          .filter(
                            (k) => k.time < local - frameTime(project) / 2,
                          )
                          .at(-1);
                        if (point) onSeek(clip.start + point.time);
                      }}
                    >
                      <ChevronLeft size={15} />
                    </button>
                    <button
                      onClick={() => {
                        change(
                          toggleKeyframe(
                            clip,
                            key,
                            local,
                            frameTime(project) / 2,
                          ),
                        );
                      }}
                    >
                      <Diamond size={12} /> Toggle at playhead
                    </button>
                    <button
                      className="icon-button bordered"
                      aria-label="Next keyframe"
                      onClick={() => {
                        const point = (clip.keyframes[key] || []).find(
                          (k) => k.time > local + frameTime(project) / 2,
                        );
                        if (point) onSeek(clip.start + point.time);
                      }}
                    >
                      <ChevronRight size={15} />
                    </button>
                  </div>
                  {(clip.keyframes[key] || []).map((point, i) => (
                    <div className="key-row" key={i}>
                      <button onClick={() => onSeek(clip.start + point.time)}>
                        {timecode(point.time, project.fps)}
                      </button>
                      <span>{point.value.toFixed(3)}</span>
                      <button
                        className="icon-button"
                        aria-label={`Delete ${key} keyframe ${i + 1}`}
                        onClick={() => {
                          const keys = {
                            ...clip.keyframes,
                            [key]: clip.keyframes[key]!.filter(
                              (_, n) => n !== i,
                            ),
                          };
                          change({ keyframes: keys });
                        }}
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                  ))}
                  <small className="field-note">
                    Linear interpolation · clip-local time · current{" "}
                    {local.toFixed(3)}s
                  </small>
                </div>
              </details>
            </fieldset>
          </div>
          {track!.locked && (
            <div className="locked-note">
              <LockKeyhole size={12} /> Unlock the track to edit.
            </div>
          )}
        </>
      )}
    </aside>
  );
}
