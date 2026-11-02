import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, PointerEvent } from "react";
import {
  ArrowUp,
  ArrowDown,
  AudioLines,
  ChevronDown,
  Film,
  Flag,
  LockKeyhole,
  Magnet,
  Maximize2,
  MousePointer2,
  Plus,
  Scissors,
  Trash2,
  Type,
  UnlockKeyhole,
  Volume2,
  VolumeX,
  Eye,
  EyeOff,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import type {
  Clip,
  Media,
  MediaMap,
  Project,
  Track,
  TrackKind,
} from "../types";
import {
  clamp,
  duration,
  findClip,
  moveClip,
  roundFrame,
  snapCandidates,
  snapTime,
  timecode,
  trimClip,
} from "../editor_state";
import { assetUrl } from "../api";

const HEADER = 168,
  ROW = 70;
type Props = {
  project: Project;
  media: MediaMap;
  selected: string | null;
  playhead: number;
  pixels: number;
  snap: boolean;
  tool: "select" | "blade";
  onSelect: (id: string | null) => void;
  onSeek: (time: number) => void;
  onChange: (project: Project) => void;
  onTransient: (project: Project) => void;
  onGesture: (before: Project, project: Project) => void;
  onDragging: (dragging: boolean) => void;
  onError: (error: unknown) => void;
  onSplit: (id: string, time: number) => void;
  onDropMedia: (id: string, time: number, track: string) => void;
  onPixels: (value: number) => void;
  onSnap: () => void;
  onTool: (tool: "select" | "blade") => void;
  onTrack: (id: string, change: Partial<Track>) => void;
  onAddTrack: (kind: TrackKind) => void;
  onRemoveTrack: (id: string) => void;
  onReorder: (id: string, direction: -1 | 1) => void;
  onMarker: () => void;
  onEditMarker: (id: string) => void;
};
type Gesture = {
  before: Project;
  clip: Clip;
  trackId: string;
  target: string;
  mode: "move" | "left" | "right";
  clientX: number;
  scroll: number;
  next: Project;
};

function Waveform({ clip, media }: { clip: Clip; media?: Media }) {
  const points = media?.waveform;
  if (!media || !points?.length || !media.duration) return null;
  const count = 64,
    bars = Array.from({ length: count }, (_, i) => {
      const t = ((i + 0.5) / count) * clip.duration;
      const source =
        clip.in + (clip.reverse ? clip.duration - t : t) * clip.speed;
      const value =
        points[
          Math.min(
            points.length - 1,
            Math.max(0, Math.floor((source / media.duration) * points.length)),
          )
        ];
      return clamp(Math.abs(Number(value) || 0), 0, 1);
    });
  return (
    <svg
      className="clip-waveform"
      viewBox={`0 0 ${count * 3} 28`}
      preserveAspectRatio="none"
      role="img"
      aria-label="Source audio waveform"
    >
      {bars.map((v, i) => (
        <path key={i} d={`M${i * 3 + 1} ${14 - v * 12}v${v * 24}`} />
      ))}
    </svg>
  );
}

export function Timeline(props: Props) {
  const { project, media, selected, playhead, pixels, snap, tool } = props;
  const viewport = useRef<HTMLDivElement>(null),
    gesture = useRef<Gesture | null>(null),
    seeking = useRef(false);
  const [view, setView] = useState({ left: 0, width: 1000 }),
    [ghost, setGhost] = useState<{
      track: string;
      start: number;
      length: number;
    } | null>(null);
  const total = duration(project),
    length = Math.max(15, total + 5),
    content = Math.max(view.width - HEADER, length * pixels);
  const tracks = useMemo(
    () => [
      ...project.tracks.filter((t) => t.kind !== "audio").reverse(),
      ...project.tracks.filter((t) => t.kind === "audio"),
    ],
    [project.tracks],
  );
  const visibleStart = Math.max(0, (view.left - 80) / pixels),
    visibleEnd = (view.left + view.width + 120) / pixels;
  useEffect(() => {
    const el = viewport.current;
    if (!el) return;
    const observer = new ResizeObserver(() =>
      setView((v) => ({ ...v, width: el.clientWidth })),
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const tickStep = pixels >= 100 ? 1 : pixels >= 40 ? 2 : pixels >= 15 ? 5 : 10;
  const ticks = Array.from(
    {
      length: Math.min(
        300,
        Math.ceil((visibleEnd - visibleStart) / tickStep) + 3,
      ),
    },
    (_, i) => Math.floor(visibleStart / tickStep) * tickStep + i * tickStep,
  ).filter((t) => t >= 0 && t <= length);
  function at(clientX: number) {
    const el = viewport.current;
    if (!el) return 0;
    return clamp(
      roundFrame(
        (clientX - el.getBoundingClientRect().left + el.scrollLeft - HEADER) /
          pixels,
        project,
      ),
      0,
      Math.max(0, total),
    );
  }
  function begin(
    event: PointerEvent<HTMLDivElement>,
    clip: Clip,
    track: Track,
    mode: Gesture["mode"],
  ) {
    if (event.button !== 0) return;
    event.stopPropagation();
    event.preventDefault();
    props.onSelect(clip.id);
    if (track.locked) {
      props.onError(new Error(`${track.name} is locked.`));
      return;
    }
    if (tool === "blade" && mode === "move") {
      props.onSplit(clip.id, at(event.clientX));
      return;
    }
    event.currentTarget.setPointerCapture(event.pointerId);
    gesture.current = {
      before: project,
      clip,
      trackId: track.id,
      target: track.id,
      mode,
      clientX: event.clientX,
      scroll: viewport.current?.scrollLeft || 0,
      next: project,
    };
    props.onDragging(true);
  }
  function drag(event: PointerEvent<HTMLDivElement>) {
    const g = gesture.current,
      el = viewport.current;
    if (!g || !el) return;
    const bounds = el.getBoundingClientRect();
    if (event.clientX > bounds.right - 28) el.scrollLeft += 12;
    else if (event.clientX < bounds.left + HEADER + 28) el.scrollLeft -= 12;
    const delta =
        (event.clientX - g.clientX + el.scrollLeft - g.scroll) / pixels,
      candidates = snap ? snapCandidates(g.before, g.clip.id, playhead) : [];
    try {
      if (g.mode === "move") {
        let start = clamp(
            roundFrame(g.clip.start + delta, project),
            0,
            86400 - g.clip.duration,
          ),
          distance = 8 / pixels;
        for (const time of candidates)
          for (const edge of [0, g.clip.duration]) {
            const d = time - start - edge;
            if (Math.abs(d) < distance) {
              distance = Math.abs(d);
              start += d;
            }
          }
        const target = document
          .elementFromPoint(event.clientX, event.clientY)
          ?.closest<HTMLElement>("[data-track-id]")?.dataset.trackId;
        if (target) g.target = target;
        g.next = moveClip(g.before, g.clip.id, start);
        setGhost(
          g.target === g.trackId
            ? null
            : { track: g.target, start, length: g.clip.duration },
        );
      } else {
        const edge =
          g.mode === "left" ? g.clip.start : g.clip.start + g.clip.duration;
        const adjusted = snap
          ? snapTime(edge + delta, candidates, 8 / pixels) - edge
          : delta;
        g.next = trimClip(g.before, g.clip.id, g.mode, adjusted, media);
      }
      props.onTransient(g.next);
    } catch (error) {
      props.onError(error);
    }
  }
  function end(event: PointerEvent<HTMLDivElement>) {
    const g = gesture.current;
    if (!g) return;
    gesture.current = null;
    setGhost(null);
    props.onDragging(false);
    try {
      const final =
        g.mode === "move" && g.target !== g.trackId
          ? moveClip(
              g.before,
              g.clip.id,
              findClip(g.next, g.clip.id)!.clip.start,
              g.target,
              media,
            )
          : g.next;
      props.onGesture(g.before, final);
    } catch (error) {
      props.onTransient(g.before);
      props.onError(error);
    }
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
  }
  function cancel() {
    const g = gesture.current;
    if (g) props.onTransient(g.before);
    gesture.current = null;
    setGhost(null);
    props.onDragging(false);
  }
  function drop(event: React.DragEvent<HTMLDivElement>, track: Track) {
    event.preventDefault();
    const id = event.dataTransfer.getData("application/x-8k-editor-media");
    if (id)
      props.onDropMedia(
        id,
        Math.max(
          0,
          roundFrame(
            (event.clientX - event.currentTarget.getBoundingClientRect().left) /
              pixels,
            project,
          ),
        ),
        track.id,
      );
  }
  return (
    <section className="timeline-panel" aria-label="Timeline editor">
      <div className="timeline-toolbar">
        <div className="section-id">
          <Film size={14} />
          <strong>Timeline</strong>
          <span>{project.name}</span>
        </div>
        <div className="timeline-tools">
          <button
            className={tool === "select" ? "active" : ""}
            aria-label="Selection tool"
            title="Selection tool"
            onClick={() => props.onTool("select")}
          >
            <MousePointer2 size={15} />
          </button>
          <button
            className={tool === "blade" ? "active" : ""}
            aria-label="Blade tool"
            title="Blade tool · click a clip to split"
            onClick={() => props.onTool("blade")}
          >
            <Scissors size={15} />
          </button>
          <i />
          <button
            className={snap ? "active" : ""}
            aria-pressed={snap}
            title="Toggle snapping"
            aria-label="Toggle snapping"
            onClick={props.onSnap}
          >
            <Magnet size={15} />
          </button>
          <button
            aria-label="Add marker"
            title="Add marker at playhead · M"
            onClick={props.onMarker}
          >
            <Flag size={14} />
          </button>
          <i />
          <button
            aria-label="Zoom out timeline"
            onClick={() => props.onPixels(clamp(pixels / 1.4, 4, 240))}
          >
            <ZoomOut size={14} />
          </button>
          <input
            aria-label="Timeline zoom"
            type="range"
            min={4}
            max={240}
            value={pixels}
            onChange={(e) => props.onPixels(Number(e.target.value))}
          />
          <button
            aria-label="Zoom in timeline"
            onClick={() => props.onPixels(clamp(pixels * 1.4, 4, 240))}
          >
            <ZoomIn size={14} />
          </button>
          <button
            aria-label="Fit timeline"
            title="Fit sequence to timeline"
            onClick={() =>
              props.onPixels(
                clamp((view.width - HEADER - 40) / Math.max(total, 8), 4, 240),
              )
            }
          >
            <Maximize2 size={13} />
          </button>
        </div>
        <div className="add-track">
          <Plus size={13} />
          <select
            value=""
            aria-label="Add track"
            onChange={(e) => props.onAddTrack(e.target.value as TrackKind)}
          >
            <option value="" disabled>
              Add track
            </option>
            <option value="video">Video track</option>
            <option value="audio">Audio track</option>
            <option value="title">Title track</option>
          </select>
          <ChevronDown size={11} />
        </div>
      </div>
      <div
        ref={viewport}
        className={`timeline-scroll ${tool === "blade" ? "blade-mode" : ""}`}
        onScroll={(e) =>
          setView((v) => ({ ...v, left: e.currentTarget.scrollLeft }))
        }
      >
        <div className="timeline-inner" style={{ width: HEADER + content }}>
          <div
            className="timeline-ruler"
            style={{ width: content, marginLeft: HEADER }}
            onPointerDown={(e) => {
              if (e.button !== 0) return;
              seeking.current = true;
              e.currentTarget.setPointerCapture(e.pointerId);
              props.onSeek(at(e.clientX));
            }}
            onPointerMove={(e) => {
              if (seeking.current) props.onSeek(at(e.clientX));
            }}
            onPointerUp={() => {
              seeking.current = false;
            }}
            onPointerCancel={() => {
              seeking.current = false;
            }}
            role="slider"
            aria-label="Timeline playhead"
            aria-valuemin={0}
            aria-valuemax={Math.max(total, 1)}
            aria-valuenow={playhead}
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "ArrowRight") {
                e.preventDefault();
                e.stopPropagation();
                props.onSeek(Math.min(total, playhead + 1));
              }
              if (e.key === "ArrowLeft") {
                e.preventDefault();
                e.stopPropagation();
                props.onSeek(Math.max(0, playhead - 1));
              }
            }}
          >
            {ticks.map((t) => (
              <span className="ruler-tick" key={t} style={{ left: t * pixels }}>
                <b>{timecode(t, project.fps)}</b>
              </span>
            ))}
            {project.markers
              .filter((m) => m.time >= visibleStart && m.time <= visibleEnd)
              .map((m) => (
                <button
                  key={m.id}
                  className="marker"
                  title={`${m.label || "Marker"} · double-click to edit`}
                  style={{ left: m.time * pixels, color: m.color }}
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={() => props.onSeek(m.time)}
                  onDoubleClick={() => props.onEditMarker(m.id)}
                >
                  <Flag size={12} />
                </button>
              ))}
          </div>
          {tracks.map((track, index) => (
            <div
              className={`timeline-row ${track.muted ? "muted" : ""} ${ghost?.track === track.id ? "drop-target" : ""}`}
              data-track-id={track.id}
              key={track.id}
              style={{ height: ROW }}
            >
              <div className={`track-header ${track.kind}`}>
                <span className="track-badge">
                  {track.kind === "audio" ? (
                    <AudioLines size={13} />
                  ) : track.kind === "title" ? (
                    <Type size={13} />
                  ) : (
                    <Film size={13} />
                  )}
                </span>
                <input
                  aria-label={`Rename ${track.name}`}
                  value={track.name}
                  maxLength={200}
                  onChange={(e) =>
                    props.onTrack(track.id, { name: e.target.value })
                  }
                />
                <div className="track-actions">
                  <button
                    title={track.muted ? "Enable track" : "Mute track"}
                    aria-label={`${track.muted ? "Enable" : "Mute"} ${track.name}`}
                    aria-pressed={track.muted}
                    onClick={() =>
                      props.onTrack(track.id, { muted: !track.muted })
                    }
                  >
                    {track.kind === "audio" ? (
                      track.muted ? (
                        <VolumeX size={12} />
                      ) : (
                        <Volume2 size={12} />
                      )
                    ) : track.muted ? (
                      <EyeOff size={12} />
                    ) : (
                      <Eye size={12} />
                    )}
                  </button>
                  <button
                    title={track.locked ? "Unlock track" : "Lock track"}
                    aria-label={`${track.locked ? "Unlock" : "Lock"} ${track.name}`}
                    aria-pressed={track.locked}
                    onClick={() =>
                      props.onTrack(track.id, { locked: !track.locked })
                    }
                  >
                    {track.locked ? (
                      <LockKeyhole size={11} />
                    ) : (
                      <UnlockKeyhole size={11} />
                    )}
                  </button>
                  <button
                    aria-label={`Move ${track.name} up`}
                    disabled={
                      index === 0 ||
                      (tracks[index - 1].kind === "audio") !==
                        (track.kind === "audio")
                    }
                    onClick={() => props.onReorder(track.id, -1)}
                  >
                    <ArrowUp size={10} />
                  </button>
                  <button
                    aria-label={`Move ${track.name} down`}
                    disabled={
                      index === tracks.length - 1 ||
                      (tracks[index + 1].kind === "audio") !==
                        (track.kind === "audio")
                    }
                    onClick={() => props.onReorder(track.id, 1)}
                  >
                    <ArrowDown size={10} />
                  </button>
                  <button
                    aria-label={`Delete ${track.name}`}
                    disabled={track.locked}
                    onClick={() => props.onRemoveTrack(track.id)}
                  >
                    <Trash2 size={10} />
                  </button>
                </div>
              </div>
              <div
                className="track-lane"
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => drop(e, track)}
                onDoubleClick={(e) => {
                  if (e.target === e.currentTarget) props.onSeek(at(e.clientX));
                }}
                style={{
                  width: content,
                  backgroundSize: `${pixels * tickStep}px 100%`,
                }}
              >
                {track.clips
                  .filter(
                    (c) =>
                      c.id === selected ||
                      (c.start + c.duration >= visibleStart &&
                        c.start <= visibleEnd),
                  )
                  .map((c) => {
                    const item = media[c.media_id || ""],
                      offline = track.kind !== "title" && !item;
                    return (
                      <div
                        role="button"
                        tabIndex={0}
                        aria-label={`Clip ${track.kind === "title" ? c.text : item?.name || "Offline media"}`}
                        key={c.id}
                        data-clip-id={c.id}
                        className={`timeline-clip ${track.kind} ${selected === c.id ? "selected" : ""} ${offline ? "offline" : ""}`}
                        style={
                          {
                            left: c.start * pixels,
                            width: Math.max(7, c.duration * pixels),
                            "--clip-color":
                              track.kind === "audio"
                                ? "#57b8a0"
                                : track.kind === "title"
                                  ? "#d6af70"
                                  : "#5e9baf",
                          } as CSSProperties
                        }
                        onClick={(e) => {
                          e.stopPropagation();
                          props.onSelect(c.id);
                        }}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") props.onSelect(c.id);
                        }}
                        onPointerDown={(e) => begin(e, c, track, "move")}
                        onPointerMove={drag}
                        onPointerUp={end}
                        onPointerCancel={cancel}
                      >
                        {item?.poster_url && (
                          <img
                            className="clip-poster"
                            src={assetUrl(item.poster_url)}
                            width="64"
                            height="42"
                            loading="lazy"
                            decoding="async"
                            alt=""
                            draggable={false}
                          />
                        )}
                        <div className="clip-label">
                          <span>
                            {track.kind === "title" ? (
                              <Type size={10} />
                            ) : track.kind === "audio" ? (
                              <AudioLines size={10} />
                            ) : (
                              <Film size={10} />
                            )}
                          </span>
                          <strong>
                            {track.kind === "title"
                              ? c.text || "Empty title"
                              : item?.name || "Offline media"}
                          </strong>
                          {c.speed !== 1 && (
                            <small>{c.speed.toFixed(2)}×</small>
                          )}
                          {c.reverse && <small>REV</small>}
                          {Object.keys(c.lens).length > 0 && (
                            <small>LENS</small>
                          )}
                        </div>
                        <Waveform clip={c} media={item} />
                        <div
                          className="clip-edge left"
                          role="separator"
                          aria-label="Trim clip start"
                          onPointerDown={(e) => begin(e, c, track, "left")}
                        />
                        <div
                          className="clip-edge right"
                          role="separator"
                          aria-label="Trim clip end"
                          onPointerDown={(e) => begin(e, c, track, "right")}
                        />
                        {c.fade_in > 0 && (
                          <i
                            className="clip-fade in"
                            style={{
                              width: `${Math.min(100, (c.fade_in / c.duration) * 100)}%`,
                            }}
                          />
                        )}
                        {c.fade_out > 0 && (
                          <i
                            className="clip-fade out"
                            style={{
                              width: `${Math.min(100, (c.fade_out / c.duration) * 100)}%`,
                            }}
                          />
                        )}
                        {selected === c.id &&
                          Object.entries(c.keyframes).flatMap(([key, points]) =>
                            points!.map((point) => (
                              <button
                                className="timeline-key"
                                key={`${key}-${point.time}`}
                                title={`${key} · ${point.value.toFixed(2)}`}
                                style={{
                                  left: (point.time / c.duration) * 100 + "%",
                                }}
                                onPointerDown={(e) => e.stopPropagation()}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  props.onSeek(c.start + point.time);
                                }}
                              />
                            )),
                          )}
                      </div>
                    );
                  })}
                {ghost?.track === track.id && (
                  <div
                    className="drag-ghost"
                    style={{
                      left: ghost.start * pixels,
                      width: ghost.length * pixels,
                    }}
                  />
                )}
              </div>
            </div>
          ))}
          <div
            className="timeline-playhead"
            style={{
              left: HEADER + playhead * pixels,
              height: 36 + tracks.length * ROW,
            }}
          >
            <i />
          </div>
          {!project.tracks.some((t) => t.clips.length > 0) && (
            <div className="timeline-empty" style={{ left: HEADER + 24 }}>
              Drop media onto a track, or insert from the media bin.
              <span>Your originals stay untouched.</span>
            </div>
          )}
        </div>
      </div>
      <div className="timeline-bottom">
        <span>
          {tracks.length} tracks <i />{" "}
          {project.tracks.reduce((n, t) => n + t.clips.length, 0)} clips
        </span>
        <span>
          {snap ? "Snapping on" : "Snapping off"} <i /> {Math.round(pixels)}{" "}
          px/s
        </span>
        <span>
          Sequence duration <b>{timecode(total, project.fps)}</b>
        </span>
      </div>
    </section>
  );
}
