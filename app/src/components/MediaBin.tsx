import { useRef, useState } from "react";
import {
  AudioLines,
  Clapperboard,
  FileImage,
  Film,
  FolderInput,
  ImagePlus,
  Plus,
  Search,
  Settings2,
  Upload,
} from "lucide-react";
import type { Media, MediaMap, Project } from "../types";
import { assetUrl, bytesLabel } from "../api";

type Props = {
  items: Media[];
  project: Project;
  selected: string | null;
  busy: string;
  uploadProgress: number;
  onSelect: (id: string) => void;
  onAdd: (id: string, atEnd: boolean) => void;
  onUpload: (files: File[], sequence: boolean, fps: string) => void;
  onRelink: (missing: string, replacement: string) => void;
  onInterpret: (media: Media) => void;
  onFormats: () => void;
};
export function MediaBin({
  items,
  project,
  selected,
  busy,
  uploadProgress,
  onSelect,
  onAdd,
  onUpload,
  onRelink,
  onInterpret,
  onFormats,
}: Props) {
  const [search, setSearch] = useState(""),
    [filter, setFilter] = useState("all"),
    [fps, setFps] = useState("24/1"),
    [replacement, setReplacement] = useState("");
  const upload = useRef<HTMLInputElement>(null),
    sequence = useRef<HTMLInputElement>(null);
  const map: MediaMap = Object.fromEntries(items.map((m) => [m.id, m])),
    missing = [
      ...new Set(
        project.tracks.flatMap((t) =>
          t.clips.flatMap((c) =>
            c.media_id && !map[c.media_id] ? [c.media_id] : [],
          ),
        ),
      ),
    ];
  const visible = items.filter(
    (m) =>
      (filter === "all" || filter === "image"
        ? filter === "all" || m.kind === "image" || m.kind === "sequence"
        : m.kind === filter) &&
      m.name.toLowerCase().includes(search.toLowerCase()),
  );
  const receive = (files: FileList | null, isSequence = false) => {
    if (files?.length)
      onUpload(
        [...files].sort((a, b) =>
          a.name.localeCompare(b.name, undefined, { numeric: true }),
        ),
        isSequence,
        fps,
      );
  };
  return (
    <aside className="media-panel panel">
      <div className="panel-heading">
        <span>PROJECT MEDIA</span>
        <button
          className="icon-button"
          onClick={onFormats}
          title="Format support"
          aria-label="Format support"
        >
          <Settings2 size={15} />
        </button>
      </div>
      <div className="media-import">
        <button
          className="primary subtle"
          disabled={!!busy}
          onClick={() => upload.current?.click()}
        >
          <Upload size={15} /> Import media
        </button>
        <button
          className="icon-button bordered"
          disabled={!!busy}
          onClick={() => sequence.current?.click()}
          title="Import an ordered image sequence"
          aria-label="Import image sequence"
        >
          <ImagePlus size={17} />
        </button>
      </div>
      <input
        hidden
        type="file"
        multiple
        ref={upload}
        onChange={(e) => {
          receive(e.target.files);
          e.target.value = "";
        }}
      />
      <input
        hidden
        type="file"
        multiple
        accept="image/*,.exr,.dpx,.j2k,.jp2,.cin"
        ref={sequence}
        onChange={(e) => {
          receive(e.target.files, true);
          e.target.value = "";
        }}
      />
      <label className="sequence-rate">
        Sequence rate{" "}
        <input
          aria-label="Image sequence frame rate"
          value={fps}
          onChange={(e) => setFps(e.target.value)}
          placeholder="24/1"
          title="Images are ordered naturally by filename"
        />
      </label>
      {busy && (
        <div className="import-progress">
          <div>
            <i style={{ width: `${uploadProgress}%` }} />
          </div>
          <span>{busy}</span>
        </div>
      )}
      <div className="search">
        <Search size={14} />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search media…"
          aria-label="Search media"
        />
      </div>
      <div className="bin-tabs">
        {[
          ["all", "All"],
          ["video", "Video"],
          ["audio", "Audio"],
          ["image", "Stills"],
        ].map(([value, label]) => (
          <button
            key={value}
            className={filter === value ? "active" : ""}
            onClick={() => setFilter(value)}
          >
            {label}
          </button>
        ))}
      </div>
      <div
        className="media-items"
        onDragOver={(e) => {
          if (e.dataTransfer.types.includes("Files")) e.preventDefault();
        }}
        onDrop={(e) => {
          if (e.dataTransfer.files.length) {
            e.preventDefault();
            receive(e.dataTransfer.files);
          }
        }}
      >
        {!items.length && (
          <div className="empty-media">
            <FolderInput size={33} />
            <strong>Your story starts here</strong>
            <p>
              Drop video, audio, or stills.
              <br />
              Original files stay untouched.
            </p>
            <button onClick={() => upload.current?.click()} disabled={!!busy}>
              Choose files
            </button>
          </div>
        )}
        {visible.map((m) => (
          <div
            key={m.id}
            className={`media-card ${selected === m.id ? "selected" : ""}`}
            draggable
            onDragStart={(e) => {
              e.dataTransfer.setData("application/x-8k-editor-media", m.id);
              e.dataTransfer.effectAllowed = "copy";
            }}
            onClick={() => onSelect(m.id)}
            onDoubleClick={() => onAdd(m.id, false)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter") onSelect(m.id);
            }}
            aria-label={`Select ${m.name}`}
          >
            <div className="media-thumb">
              {m.poster_url ? (
                <img
                  src={assetUrl(m.poster_url)}
                  alt=""
                  width={72}
                  height={46}
                  loading="lazy"
                  decoding="async"
                />
              ) : m.kind === "audio" ? (
                <AudioLines size={23} />
              ) : m.kind === "image" || m.kind === "sequence" ? (
                <FileImage size={23} />
              ) : (
                <Film size={23} />
              )}
              <span>
                {m.kind === "sequence" ? "SEQ" : m.kind.toUpperCase()}
              </span>
            </div>
            <div className="media-meta">
              <strong title={m.name}>{m.name}</strong>
              <small>
                {m.kind === "audio"
                  ? `${m.audio_streams} audio stream${m.audio_streams === 1 ? "" : "s"}`
                  : `${m.width} × ${m.height}`}{" "}
                · {m.duration > 0 ? `${m.duration.toFixed(2)}s` : "Still"}
              </small>
              <small>
                {m.codec} · {m.bit_depth} bit · {bytesLabel(m.bytes)}
              </small>
            </div>
            <div className="media-actions">
              <button
                className="icon-button"
                title="Insert at playhead"
                aria-label={`Insert ${m.name} at playhead`}
                onClick={(e) => {
                  e.stopPropagation();
                  onAdd(m.id, false);
                }}
              >
                <Plus size={14} />
              </button>
              <button
                className="icon-button"
                title="Append to timeline"
                aria-label={`Append ${m.name}`}
                onClick={(e) => {
                  e.stopPropagation();
                  onAdd(m.id, true);
                }}
              >
                <Clapperboard size={14} />
              </button>
              <button
                className="icon-button"
                title="Source interpretation and metadata"
                aria-label={`Source interpretation for ${m.name}`}
                onClick={(e) => {
                  e.stopPropagation();
                  onInterpret(m);
                }}
              >
                <Settings2 size={13} />
              </button>
            </div>
          </div>
        ))}
        {!!items.length && !visible.length && (
          <p className="empty-note">No matching media.</p>
        )}
        {missing.map((id) => (
          <div className="offline-card" key={id}>
            <strong>Offline media</strong>
            <code>{id.slice(0, 20)}…</code>
            <p>Relink all matching clips; edit points are preserved.</p>
            <select
              aria-label={`Replacement for ${id}`}
              value={replacement}
              onChange={(e) => setReplacement(e.target.value)}
            >
              <option value="">Choose imported replacement</option>
              {items.map((m) => (
                <option value={m.id} key={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
            <button
              disabled={!replacement}
              onClick={() => onRelink(id, replacement)}
            >
              Relink media
            </button>
          </div>
        ))}
      </div>
      <div className="panel-foot">
        {items.length} source{items.length === 1 ? "" : "s"} · Drag to timeline
      </div>
    </aside>
  );
}
