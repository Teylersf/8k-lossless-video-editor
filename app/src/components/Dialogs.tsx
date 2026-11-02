import { useEffect, useRef, useState } from "react";
import {
  Check,
  Download,
  FolderOpen,
  Info,
  LoaderCircle,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";
import type {
  ExportMode,
  FormatCatalog,
  Job,
  Marker,
  Media,
  Project,
  ProjectEnvelope,
} from "../types";
import { api, assetUrl, bytesLabel, isTerminal, post } from "../api";
import { duration, uid, validateProject } from "../editor_state";
import { NumberField } from "./Inspector";

export function Modal({
  title,
  subtitle,
  wide = false,
  onClose,
  children,
}: {
  title: string;
  subtitle?: string;
  wide?: boolean;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const close = useRef(onClose),
    dialog = useRef<HTMLElement>(null);
  close.current = onClose;
  useEffect(() => {
    const last = document.activeElement as HTMLElement | null;
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close.current();
      }
      if (e.key === "Tab") {
        const items = [
          ...dialog.current!.querySelectorAll<HTMLElement>(
            'button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]',
          ),
        ].filter((el) => el.getClientRects().length);
        const first = items[0],
          lastItem = items.at(-1);
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          lastItem?.focus();
        } else if (!e.shiftKey && document.activeElement === lastItem) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      last?.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <section
        ref={dialog}
        className={`modal ${wide ? "wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header>
          <div>
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <button
            autoFocus
            className="icon-button"
            aria-label="Close dialog"
            onClick={onClose}
          >
            <X size={19} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
export function ProjectSettings({
  project,
  onApply,
  onClose,
  onError,
}: {
  project: Project;
  onApply: (p: Project) => void;
  onClose: () => void;
  onError: (e: unknown) => void;
}) {
  const [draft, setDraft] = useState(project),
    [scale, setScale] = useState(false);
  const set = (patch: Partial<Project>) =>
    setDraft((p) => ({ ...p, ...patch }));
  const apply = () => {
    try {
      let next = validateProject(draft);
      if (scale) {
        const ratio = draft.width / project.width;
        next = {
          ...next,
          tracks: next.tracks.map((t) => ({
            ...t,
            clips: t.clips.map((c) => ({
              ...c,
              x: c.x * ratio,
              y: c.y * (draft.height / project.height),
              font_size: Math.min(1024, Math.max(8, c.font_size * ratio)),
              keyframes: {
                ...c.keyframes,
                ...(c.keyframes.x
                  ? {
                      x: c.keyframes.x.map((k) => ({
                        ...k,
                        value: k.value * ratio,
                      })),
                    }
                  : {}),
                ...(c.keyframes.y
                  ? {
                      y: c.keyframes.y.map((k) => ({
                        ...k,
                        value: (k.value * draft.height) / project.height,
                      })),
                    }
                  : {}),
              },
            })),
          })),
        };
        next = validateProject(next);
      }
      onApply(next);
      onClose();
    } catch (e) {
      onError(e);
    }
  };
  return (
    <Modal
      title="Sequence settings"
      subtitle="A native-size canvas. Original sources are preserved."
      onClose={onClose}
    >
      <div className="modal-body">
        <label className="stack-field">
          Sequence name
          <input
            value={draft.name}
            maxLength={200}
            onChange={(e) => set({ name: e.target.value })}
          />
        </label>
        <label className="stack-field">
          Canvas preset
          <select
            value={`${draft.width}x${draft.height}`}
            onChange={(e) => {
              const [width, height] = e.target.value.split("x").map(Number);
              set({ width, height });
            }}
          >
            {![
              "1920x1080",
              "3840x2160",
              "4096x2160",
              "7680x4320",
              "8192x4320",
              "1080x1920",
              "2160x3840",
            ].includes(`${draft.width}x${draft.height}`) && (
              <option value={`${draft.width}x${draft.height}`}>Custom</option>
            )}
            {[
              ["1920x1080", "HD · 1920 × 1080"],
              ["3840x2160", "UHD 4K · 3840 × 2160"],
              ["4096x2160", "DCI 4K · 4096 × 2160"],
              ["7680x4320", "UHD 8K · 7680 × 4320"],
              ["8192x4320", "DCI 8K · 8192 × 4320"],
              ["1080x1920", "Portrait HD · 1080 × 1920"],
              ["2160x3840", "Portrait 4K · 2160 × 3840"],
            ].map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <div className="two-fields">
          <NumberField
            label="Width"
            value={draft.width}
            min={16}
            max={8192}
            step={1}
            onChange={(width) => set({ width })}
          />
          <NumberField
            label="Height"
            value={draft.height}
            min={16}
            max={8192}
            step={1}
            onChange={(height) => set({ height })}
          />
        </div>
        <label className="stack-field">
          Frame rate (rational)
          <input
            list="frame-rates"
            value={draft.fps}
            onChange={(e) => set({ fps: e.target.value })}
          />
          <datalist id="frame-rates">
            {[
              "24000/1001",
              "24/1",
              "25/1",
              "30000/1001",
              "30/1",
              "50/1",
              "60000/1001",
              "60/1",
              "120/1",
            ].map((v) => (
              <option key={v}>{v}</option>
            ))}
          </datalist>
        </label>
        <label className="stack-field">
          Output color space
          <select
            value={draft.color_space}
            onChange={(e) =>
              set({ color_space: e.target.value as Project["color_space"] })
            }
          >
            <option value="bt709">Rec.709 SDR</option>
            <option value="bt2020-pq">Rec.2020 PQ HDR</option>
            <option value="bt2020-hlg">Rec.2020 HLG HDR</option>
          </select>
        </label>
        <div className="two-fields">
          <label className="stack-field">
            Audio layout
            <select
              value={draft.audio_layout}
              onChange={(e) =>
                set({ audio_layout: e.target.value as Project["audio_layout"] })
              }
            >
              {["mono", "stereo", "5.1", "7.1"].map((v) => (
                <option key={v}>{v}</option>
              ))}
            </select>
          </label>
          <label className="stack-field">
            Audio sample rate
            <select
              value={draft.audio_sample_rate}
              onChange={(e) =>
                set({
                  audio_sample_rate: Number(
                    e.target.value,
                  ) as Project["audio_sample_rate"],
                })
              }
            >
              {[44100, 48000, 96000].map((v) => (
                <option key={v} value={v}>
                  {v} Hz
                </option>
              ))}
            </select>
          </label>
        </div>
        <label className="color-field">
          Gap background
          <input
            type="color"
            value={draft.background}
            onChange={(e) => set({ background: e.target.value })}
          />
        </label>
        <label className="check-field">
          <input
            type="checkbox"
            checked={scale}
            onChange={(e) => setScale(e.target.checked)}
          />{" "}
          Scale existing positions and title sizes with canvas
        </label>
        <p className="field-note">
          Timeline output is constant frame rate. Mixed sources are resampled to
          this sequence rate. Timecode displays non-drop frame counts.
        </p>
      </div>
      <footer>
        <button onClick={onClose}>Cancel</button>
        <button className="primary" onClick={apply}>
          Apply settings
        </button>
      </footer>
    </Modal>
  );
}
export function OpenProject({
  onOpen,
  onClose,
  onError,
}: {
  onOpen: (p: Project, id?: string) => void;
  onClose: () => void;
  onError: (e: unknown) => void;
}) {
  const [items, setItems] = useState<ProjectEnvelope[] | null>(null);
  useEffect(() => {
    let active = true;
    api<ProjectEnvelope[]>("/api/projects")
      .then((v) => {
        if (active) setItems(v);
      })
      .catch(onError);
    return () => {
      active = false;
    };
  }, [onError]);
  return (
    <Modal
      title="Open project"
      subtitle="Saved locally on this computer"
      onClose={onClose}
    >
      <div className="modal-body project-list">
        {items === null ? (
          <p>
            <LoaderCircle className="spin" size={16} /> Loading projects…
          </p>
        ) : !items.length ? (
          <div className="empty-note">
            <FolderOpen size={30} />
            <p>No saved projects yet.</p>
          </div>
        ) : (
          items.map((item) => (
            <button
              key={item.id}
              onClick={() => {
                try {
                  onOpen(validateProject(item.project), item.id);
                  onClose();
                } catch (e) {
                  onError(e);
                }
              }}
            >
              <FolderOpen size={22} />
              <span>
                <strong>{item.name || item.project.name}</strong>
                <small>
                  {item.project.width} × {item.project.height} ·{" "}
                  {duration(item.project).toFixed(2)}s ·{" "}
                  {new Date(item.updated_at * 1000).toLocaleString()}
                </small>
              </span>
            </button>
          ))
        )}
      </div>
    </Modal>
  );
}
export function MarkerDialog({
  marker,
  onApply,
  onDelete,
  onClose,
}: {
  marker?: Marker;
  onApply: (m: Marker) => void;
  onDelete?: (id: string) => void;
  onClose: () => void;
}) {
  const [draft, set] = useState<Marker>(
    marker || { id: uid("marker"), time: 0, label: "Marker", color: "#e2b86b" },
  );
  return (
    <Modal title="Timeline marker" onClose={onClose}>
      <div className="modal-body">
        <label className="stack-field">
          Label
          <input
            value={draft.label}
            maxLength={200}
            onChange={(e) => set({ ...draft, label: e.target.value })}
          />
        </label>
        <NumberField
          label="Timeline time"
          value={draft.time}
          min={0}
          max={86400}
          unit="s"
          onChange={(time) => set({ ...draft, time })}
        />
        <label className="color-field">
          Marker color
          <input
            type="color"
            value={draft.color}
            onChange={(e) => set({ ...draft, color: e.target.value })}
          />
        </label>
      </div>
      <footer>
        {marker && onDelete && (
          <button
            className="danger"
            onClick={() => {
              onDelete(marker.id);
              onClose();
            }}
          >
            Delete
          </button>
        )}
        <button
          className="primary"
          onClick={() => {
            onApply(draft);
            onClose();
          }}
        >
          Save marker
        </button>
      </footer>
    </Modal>
  );
}
export function SourceDialog({
  media,
  onApply,
  onClose,
  onError,
}: {
  media: Media;
  onApply: (m: Media) => void;
  onClose: () => void;
  onError: (e: unknown) => void;
}) {
  const [transfer, setTransfer] = useState(
      ["linear", "srgb", "bt709", "pq", "hlg"].includes(media.transfer)
        ? media.transfer
        : "linear",
    ),
    [primaries, setPrimaries] = useState(
      ["bt709", "bt2020", "smpte432", "acescg"].includes(media.color_primaries)
        ? media.color_primaries
        : "bt709",
    ),
    [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      onApply(
        await post<Media>(`/api/media/${media.id}/interpret`, {
          transfer,
          color_primaries: primaries,
        }),
      );
      onClose();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      title="Source interpretation"
      subtitle={media.name}
      onClose={onClose}
    >
      <div className="modal-body">
        <dl className="metadata-grid">
          {[
            ["Type", media.kind],
            ["Raster", `${media.width} × ${media.height}`],
            ["Codec", media.codec],
            ["Pixel format", media.pixel_format],
            ["Rate", media.fps_exact],
            ["Bit depth", String(media.bit_depth)],
            ["Size", bytesLabel(media.bytes)],
          ].map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v || "Unknown"}</dd>
            </div>
          ))}
        </dl>
        <p className="field-note">
          Original media bytes remain unchanged. Interpretation is the fallback
          for clips using source metadata; per-clip overrides live in the Color
          inspector.
        </p>
        <label className="stack-field">
          Transfer function
          <select
            value={transfer}
            onChange={(e) => setTransfer(e.target.value)}
          >
            {[
              ["linear", "Linear"],
              ["srgb", "sRGB"],
              ["bt709", "Rec.709"],
              ["pq", "PQ / ST2084"],
              ["hlg", "HLG"],
            ].map(([id, l]) => (
              <option key={id} value={id}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label className="stack-field">
          Color primaries
          <select
            value={primaries}
            onChange={(e) => setPrimaries(e.target.value)}
          >
            {[
              ["bt709", "Rec.709"],
              ["bt2020", "Rec.2020"],
              ["smpte432", "Display P3 D65"],
              ["acescg", "ACEScg / AP1"],
            ].map(([id, l]) => (
              <option key={id} value={id}>
                {l}
              </option>
            ))}
          </select>
        </label>
        {media.warnings?.map((w, i) => (
          <p className="warning-note" key={i}>
            {w}
          </p>
        ))}
        <div className="hash-line">
          Source SHA-256 <code>{media.sha256}</code>
        </div>
      </div>
      <footer>
        <button onClick={onClose}>Close</button>
        <button className="primary" disabled={busy} onClick={save}>
          {busy ? "Saving…" : "Save interpretation"}
        </button>
      </footer>
    </Modal>
  );
}
export function FormatsDialog({
  catalog,
  onClose,
}: {
  catalog: FormatCatalog | null;
  onClose: () => void;
}) {
  const [search, setSearch] = useState("");
  return (
    <Modal
      title="Format support"
      subtitle="Availability comes from the installed decoder and encoder build."
      wide
      onClose={onClose}
    >
      <div className="modal-body format-body">
        <div className="search">
          <Search size={15} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search formats, codecs, or extensions"
            aria-label="Search format support"
          />
        </div>
        <h3>Import & preview</h3>
        <div className="format-table">
          {catalog?.formats
            .filter((f) =>
              JSON.stringify(f).toLowerCase().includes(search.toLowerCase()),
            )
            .map((f) => (
              <div key={f.id}>
                <span
                  className={f.import_available ? "support yes" : "support no"}
                >
                  {f.import_available ? (
                    <Check size={14} />
                  ) : (
                    <Info size={14} />
                  )}
                </span>
                <span>
                  <strong>{f.label}</strong>
                  <small>
                    {f.category} · {f.extensions?.join(", ")}
                  </small>
                  <p>
                    {f.scope}
                    {f.reason ? ` · ${f.reason}` : ""}
                  </p>
                  {f.decoders && (
                    <div className="decoder-badges">
                      {Object.entries(f.decoders).map(([name, available]) => (
                        <span
                          key={name}
                          className={available ? "available" : "unavailable"}
                          title={
                            available
                              ? "Decoder installed; individual file variants still require validation"
                              : "Decoder not installed"
                          }
                        >
                          {available ? "✓" : "×"} {name}
                        </span>
                      ))}
                    </div>
                  )}
                </span>
                <b>
                  {f.import_available
                    ? `${Object.values(f.decoders || {}).filter(Boolean).length}/${Object.keys(f.decoders || {}).length} decoders`
                    : "Not available"}
                </b>
              </div>
            ))}
        </div>
        <h3>Export & preservation</h3>
        <div className="format-table">
          {catalog?.export_profiles
            .filter((f) =>
              JSON.stringify(f).toLowerCase().includes(search.toLowerCase()),
            )
            .map((f) => (
              <div key={f.id}>
                <span className={`support ${f.available ? "yes" : "no"}`}>
                  {f.available ? <Check size={14} /> : <Info size={14} />}
                </span>
                <span>
                  <strong>{f.label || f.name}</strong>
                  <small>
                    {f.codec} · {f.pixel_format} · {f.extension}
                  </small>
                  <p>
                    {Array.isArray(f.notes) ? f.notes.join(" ") : f.notes}
                    {f.reason ? ` · ${f.reason}` : ""}
                  </p>
                </span>
                <b>
                  {f.rgb_lossless
                    ? "RGB lossless"
                    : f.lossless
                      ? "Codec lossless"
                      : "Delivery / mezzanine"}
                </b>
              </div>
            ))}
        </div>
        {!catalog && (
          <p>
            Format information is unavailable. Connect to the local editor
            service and retry.
          </p>
        )}
        {catalog?.notes?.map((n, i) => (
          <p className="field-note" key={i}>
            {n}
          </p>
        ))}
        <div className="info-banner">
          <ShieldCheck size={17} />
          <p>
            Source files are preserved. A lossless codec preserves its encoded
            representation; color conversion, grading, scaling, and YUV
            subsampling can change source samples. Proprietary camera RAW
            formats require their own supported SDK workflow.
          </p>
        </div>
      </div>
    </Modal>
  );
}
export type JobEntry = {
  job: Job;
  key: string;
  preview: boolean;
  name: string;
  mode: string;
  created: number;
};
export function ExportDialog({
  project,
  modes,
  entries,
  onExport,
  onCancel,
  onClose,
}: {
  project: Project;
  modes: ExportMode[];
  entries: JobEntry[];
  onExport: (mode: string) => void;
  onCancel: (id: string) => void;
  onClose: () => void;
}) {
  const [mode, setMode] = useState(
    modes.find((m) => m.id === "lossless")?.id ||
      modes.find((m) => m.available)?.id ||
      "lossless",
  );
  const selected = modes.find((m) => m.id === mode);
  return (
    <Modal
      title="Export & render queue"
      subtitle={`${project.name} · ${project.width} × ${project.height} · ${project.fps} fps · ${duration(project).toFixed(3)}s`}
      wide
      onClose={onClose}
    >
      <div className="export-layout">
        <div className="export-settings">
          <h3>Output profile</h3>
          <label className="stack-field">
            Format
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              {modes.map((m) => (
                <option key={m.id} value={m.id} disabled={!m.available}>
                  {m.name}
                  {!m.available ? " · unavailable" : ""}
                </option>
              ))}
            </select>
          </label>
          <p className="field-note">
            {selected?.description ||
              selected?.reason ||
              "The local renderer validates the complete output before enabling download."}
          </p>
          {mode === "lossless" && (
            <div className="info-banner">
              <ShieldCheck size={19} />
              <p>
                RGB16 FFV1 master with PCM audio. Rendered RGB samples are
                independently decoded and hash-verified.
              </p>
            </div>
          )}
          {mode === "copy" && (
            <p className="warning-note">
              Packet copy requires eligible homogeneous sources and safe cut
              points. Effects, titles, mixing, and incompatible cuts are
              rejected.
            </p>
          )}
          <dl className="metadata-grid">
            <div>
              <dt>Canvas</dt>
              <dd>
                {project.width} × {project.height}
              </dd>
            </div>
            <div>
              <dt>Color</dt>
              <dd>{project.color_space}</dd>
            </div>
            <div>
              <dt>Audio</dt>
              <dd>
                {project.audio_layout} · {project.audio_sample_rate} Hz
              </dd>
            </div>
            <div>
              <dt>Timing</dt>
              <dd>Project CFR</dd>
            </div>
          </dl>
          <button
            className="primary export-start"
            disabled={!duration(project) || !selected?.available}
            onClick={() => onExport(mode)}
          >
            <Download size={16} /> Add export to queue
          </button>
          <p className="field-note">
            Exports use an immutable project snapshot. You can keep editing
            while a render runs.
          </p>
        </div>
        <div className="render-list">
          <h3>Local render jobs</h3>
          {!entries.length && (
            <div className="empty-note">
              Your verified exports and timeline previews will appear here.
            </div>
          )}
          {entries
            .slice()
            .reverse()
            .map(({ job, name, preview, mode, created }) => (
              <article className={`job-card ${job.status}`} key={job.id}>
                <header>
                  <span>
                    {preview ? "TIMELINE PREVIEW" : mode.toUpperCase()}
                  </span>
                  <small>{new Date(created).toLocaleTimeString()}</small>
                </header>
                <strong>{name}</strong>
                <div className="job-progress">
                  <i
                    style={{
                      width: `${Math.min(100, Math.max(0, job.progress * 100))}%`,
                    }}
                  />
                </div>
                <p>{job.error || job.message || job.status}</p>
                {job.result && (
                  <small>
                    {job.result.width} × {job.result.height} ·{" "}
                    {job.result.frames} frames · {job.result.codec} ·{" "}
                    {job.result.pixel_format}
                  </small>
                )}
                {job.result?.warnings?.map((w, i) => (
                  <p className="warning-note" key={i}>
                    {w}
                  </p>
                ))}
                <div className="job-actions">
                  <span className="job-status">
                    {job.status} {Math.round(job.progress * 100)}%
                  </span>
                  {!isTerminal(job.status) && (
                    <button
                      onClick={() => onCancel(job.id)}
                      disabled={job.status === "cancelling"}
                    >
                      {job.status === "cancelling" ? "Stopping…" : "Cancel"}
                    </button>
                  )}
                  {job.status === "completed" && (
                    <a
                      className="button"
                      href={assetUrl(
                        job.output_url || `/api/jobs/${job.id}/download`,
                      )}
                      download={job.output_name || undefined}
                    >
                      <Download size={13} /> Download
                    </a>
                  )}
                </div>
              </article>
            ))}
        </div>
      </div>
    </Modal>
  );
}
