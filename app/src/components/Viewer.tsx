import { useCallback, useEffect, useRef, useState } from "react";
import {
  ChevronFirst,
  ChevronLast,
  ChevronLeft,
  ChevronRight,
  Clapperboard,
  Film,
  LoaderCircle,
  Maximize,
  Pause,
  Play,
  RefreshCw,
} from "lucide-react";
import type { Job, Media, Preview, Project } from "../types";
import { assetUrl } from "../api";
import { finiteSeekTarget } from "../media_seek";
import {
  clamp,
  duration,
  frameTime,
  parseTimecode,
  timecode,
} from "../editor_state";

type Props = {
  project: Project;
  playhead: number;
  source?: Media;
  tab: "program" | "source";
  onTab: (tab: "program" | "source") => void;
  preview: Preview | null;
  previewBusy: boolean;
  previewError: string;
  previewFresh: boolean;
  programUrl: string;
  programKey: string;
  currentKey: string;
  playing: boolean;
  renderJob?: Job;
  previewWidth: number;
  onWidth: (width: number) => void;
  onSeek: (time: number) => void;
  onPlay: () => void;
  onPause: () => void;
  onRetry: () => void;
  onError: (e: unknown) => void;
  videoRef: React.RefObject<HTMLVideoElement | null>;
};
export function Viewer(props: Props) {
  const {
    project,
    playhead,
    source,
    tab,
    onTab,
    preview,
    previewBusy,
    previewError,
    previewFresh,
    programUrl,
    programKey,
    currentKey,
    playing,
    renderJob,
    previewWidth,
    onWidth,
    onSeek,
    onPlay,
    onPause,
    onRetry,
    onError,
    videoRef,
  } = props;
  const [draft, setDraft] = useState<string | null>(null),
    [sourceTime, setSourceTime] = useState(0);
  const panel = useRef<HTMLDivElement>(null);
  const total = duration(project),
    hasProgram = !!programUrl && programKey === currentKey;
  const syncProgramSeek = useCallback(() => {
    if (tab !== "program" || !hasProgram || playing) return;
    const video = videoRef.current;
    if (!video) return;
    const target = finiteSeekTarget(
      playhead,
      video.duration,
      video.readyState,
      video.currentTime,
    );
    if (target === null) return;
    try {
      video.currentTime = target;
    } catch (error) {
      onError(error);
    }
  }, [playhead, hasProgram, tab, playing, videoRef, onError]);
  useEffect(syncProgramSeek, [syncProgramSeek, programUrl]);
  useEffect(() => {
    const video = videoRef.current;
    if (!video || tab !== "program" || !hasProgram) return;
    if (playing) video.play().catch(onError);
    else video.pause();
  }, [playing, hasProgram, tab, videoRef, onError]);
  const seek = (n: number) => {
    onPause();
    onSeek(clamp(n, 0, total));
  };
  const enter = () => {
    if (draft !== null) {
      try {
        seek(parseTimecode(draft, project.fps));
      } catch (e) {
        onError(e);
      }
      setDraft(null);
    }
  };
  return (
    <section className="viewer panel" ref={panel}>
      <div className="viewer-heading">
        <div className="viewer-tabs">
          <button
            className={tab === "program" ? "active" : ""}
            onClick={() => onTab("program")}
          >
            Program
          </button>
          <button
            className={tab === "source" ? "active" : ""}
            onClick={() => onTab("source")}
          >
            Source
          </button>
        </div>
        <span>
          {tab === "program"
            ? `${project.width} × ${project.height} · ${project.color_space}`
            : source?.name || "Select source media"}
        </span>
        <button
          className="icon-button"
          aria-label="Fullscreen viewer"
          title="Fullscreen viewer"
          onClick={() => {
            if (document.fullscreenElement)
              document.exitFullscreen().catch(onError);
            else panel.current?.requestFullscreen().catch(onError);
          }}
        >
          <Maximize size={14} />
        </button>
      </div>
      <div className="viewer-screen">
        <div
          className="safe-frame"
          style={{
            aspectRatio:
              tab === "source" && source?.width
                ? `${source.width}/${source.height}`
                : `${project.width}/${project.height}`,
          }}
        >
          {tab === "program" ? (
            hasProgram ? (
              <video
                ref={videoRef}
                src={programUrl}
                playsInline
                preload="auto"
                onLoadedMetadata={syncProgramSeek}
                onDurationChange={syncProgramSeek}
                onCanPlay={syncProgramSeek}
                onTimeUpdate={(e) => {
                  if (playing && Number.isFinite(e.currentTarget.currentTime))
                    onSeek(Math.min(total, e.currentTarget.currentTime));
                }}
                onEnded={onPause}
                onError={() =>
                  onError(
                    new Error(
                      "The rendered program preview could not play. Download the verified render or try rendering a new preview.",
                    ),
                  )
                }
              />
            ) : preview ? (
              <img
                src={assetUrl(preview.url)}
                width={preview.width}
                height={preview.height}
                alt={`Rendered program at ${timecode(preview.time, project.fps)}`}
                decoding="async"
              />
            ) : (
              <div className="viewer-empty">
                <div className="empty-aperture">
                  <Film size={37} />
                </div>
                <strong>A clean canvas. A clear vision.</strong>
                <p>
                  Build your sequence below.
                  <br />
                  Your first frame appears here.
                </p>
                <span>UP TO 8K · LOCAL RENDERING</span>
              </div>
            )
          ) : source?.proxy_url ? (
            source.kind === "audio" ? (
              <div className="source-audio">
                <Film size={34} />
                <strong>{source.name}</strong>
                <audio
                  controls
                  src={assetUrl(source.proxy_url)}
                  onTimeUpdate={(e) => {
                    if (Number.isFinite(e.currentTarget.currentTime))
                      setSourceTime(e.currentTarget.currentTime);
                  }}
                />
              </div>
            ) : (
              <video
                key={source.id}
                src={assetUrl(source.proxy_url)}
                controls
                playsInline
                preload="metadata"
                poster={assetUrl(source.poster_url)}
                onTimeUpdate={(e) => {
                  if (Number.isFinite(e.currentTarget.currentTime))
                    setSourceTime(e.currentTarget.currentTime);
                }}
              />
            )
          ) : source?.poster_url ? (
            <img
              src={assetUrl(source.poster_url)}
              alt={source.name}
              width={source.width}
              height={source.height}
            />
          ) : (
            <div className="viewer-empty">
              <Clapperboard size={38} />
              <strong>Source viewer</strong>
              <p>Select an imported source in the media bin.</p>
            </div>
          )}
        </div>
        {tab === "program" && (
          <>
            <div className="viewer-badge">
              {hasProgram
                ? "RENDERED PROGRAM"
                : previewFresh
                  ? "RENDERED FRAME"
                  : preview
                    ? "PREVIOUS FRAME"
                    : "PROGRAM"}
            </div>
            {previewBusy && !hasProgram && (
              <div className="viewer-loading">
                <LoaderCircle size={14} className="spin" /> Rendering frame
              </div>
            )}
            {renderJob &&
              !["completed", "failed", "cancelled"].includes(
                renderJob.status,
              ) && (
                <div className="program-render">
                  <LoaderCircle size={18} className="spin" />
                  <strong>Rendering full timeline preview</strong>
                  <p>{renderJob.message}</p>
                  <div className="job-progress">
                    <i style={{ width: `${renderJob.progress * 100}%` }} />
                  </div>
                  <small>
                    {Math.round(renderJob.progress * 100)}% · cancellable in
                    Export & queue
                  </small>
                </div>
              )}
          </>
        )}
        {tab === "source" && source && (
          <div className="viewer-badge">
            SOURCE PROXY · UNGRADED · {sourceTime.toFixed(2)}s
          </div>
        )}
      </div>
      <div className="viewer-status">
        <span>
          {tab === "source"
            ? "Proxy playback is separate from the edited program."
            : previewError ||
              (!previewFresh && preview && !hasProgram
                ? "Frame is updating to the current edit."
                : hasProgram
                  ? "Full timeline preview · downscaled for playback"
                  : preview
                    ? `${preview.width} × ${preview.height} frame preview · ${Math.round(preview.render_ms)} ms`
                    : "Insert media or add a title to start.")}
        </span>
        {tab === "program" && (
          <button
            className="icon-button"
            title="Refresh rendered frame"
            aria-label="Refresh rendered frame"
            onClick={onRetry}
          >
            <RefreshCw size={12} />
          </button>
        )}
      </div>
      <div className="transport">
        <div className="transport-time">
          <input
            aria-label="Program timecode"
            title="Non-drop timecode HH:MM:SS:FF; seconds also accepted"
            value={draft ?? timecode(playhead, project.fps)}
            onFocus={() => setDraft(timecode(playhead, project.fps))}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={enter}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.currentTarget.blur();
              if (e.key === "Escape") {
                setDraft(null);
                e.currentTarget.blur();
              }
            }}
          />
          <small>NDF</small>
        </div>
        <div className="transport-buttons">
          <button
            className="icon-button"
            aria-label="Go to sequence start"
            title="Start (Home)"
            onClick={() => seek(0)}
          >
            <ChevronFirst size={18} />
          </button>
          <button
            className="icon-button"
            aria-label="Previous frame"
            title="Previous frame (←)"
            onClick={() => seek(playhead - frameTime(project))}
          >
            <ChevronLeft size={18} />
          </button>
          <button
            className="play-button"
            aria-label={playing ? "Pause program" : "Play rendered program"}
            title={
              playing
                ? "Pause (Space)"
                : "Render full timeline preview and play (Space)"
            }
            onClick={playing ? onPause : onPlay}
            disabled={
              !total ||
              (!!renderJob &&
                !["completed", "failed", "cancelled"].includes(
                  renderJob.status,
                ))
            }
          >
            {playing ? (
              <Pause size={19} fill="currentColor" />
            ) : (
              <Play size={19} fill="currentColor" />
            )}
          </button>
          <button
            className="icon-button"
            aria-label="Next frame"
            title="Next frame (→)"
            onClick={() => seek(playhead + frameTime(project))}
          >
            <ChevronRight size={18} />
          </button>
          <button
            className="icon-button"
            aria-label="Go to sequence end"
            title="End"
            onClick={() => seek(total)}
          >
            <ChevronLast size={18} />
          </button>
        </div>
        <div className="viewer-quality">
          <select
            value={previewWidth}
            aria-label="Frame preview resolution"
            onChange={(e) => onWidth(Number(e.target.value))}
          >
            {[640, 960, 1280, 1920].map((n) => (
              <option key={n} value={n}>
                {n}px preview
              </option>
            ))}
          </select>
          <span>{timecode(total, project.fps)}</span>
        </div>
      </div>
    </section>
  );
}
