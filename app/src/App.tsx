import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";
import {
  Check,
  ChevronDown,
  Copy,
  Download,
  FileJson,
  FolderOpen,
  HelpCircle,
  Layers3,
  LoaderCircle,
  Plus,
  Redo2,
  Save,
  Scissors,
  Settings2,
  ShieldCheck,
  Trash2,
  Type,
  Undo2,
  X,
} from "lucide-react";
import type {
  Capabilities,
  FormatCatalog,
  Job,
  Marker,
  Media,
  MediaMap,
  Preview,
  Project,
  ProjectEnvelope,
  Status,
  TrackKind,
} from "./types";
import {
  API_BASE,
  api,
  assetUrl,
  downloadJson,
  errorMessage,
  isTerminal,
  post,
} from "./api";
import {
  accepts,
  adoptImportedSettings,
  clamp,
  clipCount,
  duplicateClip,
  duration,
  findClip,
  frameTime,
  historyReducer,
  insertClip,
  makeClip,
  makeTrack,
  newProject,
  newImportSettings,
  relinkMedia,
  removeClip,
  renderKey,
  roundFrame,
  splitClip,
  uid,
  updateTrack,
  validateProject,
} from "./editor_state";
import { Timeline } from "./components/Timeline";
import { MediaBin } from "./components/MediaBin";
import { Inspector } from "./components/Inspector";
import {
  ExportDialog,
  FormatsDialog,
  MarkerDialog,
  Modal,
  OpenProject,
  ProjectSettings,
  SourceDialog,
} from "./components/Dialogs";
import type { JobEntry } from "./components/Dialogs";
import { Viewer } from "./components/Viewer";
import { MediaErrorBoundary } from "./components/MediaErrorBoundary";

const RECOVERY = "8k-editor-recovery-v1";
function initial() {
  try {
    const saved = JSON.parse(localStorage.getItem(RECOVERY) || "null");
    if (saved?.project)
      return {
        project: validateProject(saved.project),
        id: typeof saved.id === "string" ? saved.id : undefined,
        settings: {
          video: !!saved.touched,
          audioLayout: saved.settings?.audioLayout !== false,
          audioSampleRate: saved.settings?.audioSampleRate !== false,
        },
      };
  } catch {
    /* An invalid recovery never replaces a valid blank project. */
  }
  return { project: newProject(), id: undefined, settings: newImportSettings() };
}
function requestUpload(
  files: File[],
  sequence: boolean,
  fps: string,
  onProgress: (n: number) => void,
) {
  return new Promise<Media>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(
      "POST",
      `${API_BASE}${sequence ? "/api/media/sequence" : "/api/media"}`,
    );
    xhr.setRequestHeader("X-Editor-Client", "8k-lossless-editor");
    const data = new FormData();
    files.forEach((file) => data.append(sequence ? "files" : "file", file));
    if (sequence) data.append("fps", fps);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress((e.loaded / e.total) * 90);
    };
    xhr.onerror = () =>
      reject(new Error("The upload could not reach the local editor."));
    xhr.onload = () => {
      try {
        const json = JSON.parse(xhr.responseText);
        if (xhr.status < 200 || xhr.status >= 300)
          throw new Error(
            typeof json.detail === "string"
              ? json.detail
              : `Upload failed (${xhr.status}).`,
          );
        resolve(json);
      } catch (e) {
        reject(e);
      }
    };
    xhr.send(data);
  });
}

export default function App() {
  const [startup] = useState(initial),
    [history, dispatch] = useReducer(historyReducer, {
      past: [],
      present: startup.project,
      future: [],
    }),
    project = history.present;
  const projectRef = useRef(project);
  projectRef.current = project;
  const [status, setStatus] = useState<Status | null>(null),
    [connection, setConnection] = useState("Connecting to local renderer…"),
    [capabilities, setCapabilities] = useState<Capabilities | null>(null),
    [catalog, setCatalog] = useState<FormatCatalog | null>(null);
  const [media, setMedia] = useState<Media[]>([]),
    [mediaRevision, setMediaRevision] = useState(0),
    [sourceId, setSourceId] = useState<string | null>(null),
    [selected, setSelected] = useState<string | null>(null),
    [playhead, setPlayhead] = useState(0),
    [pixels, setPixels] = useState(56),
    [snap, setSnap] = useState(true),
    [tool, setTool] = useState<"select" | "blade">("select"),
    [dragging, setDragging] = useState(false),
    [timelineHeight, setTimelineHeight] = useState(310);
  const [notice, setNotice] = useState(""),
    [error, setError] = useState(""),
    [modal, setModal] = useState<
      "settings" | "open" | "export" | "formats" | "help" | "new" | null
    >(null),
    [marker, setMarker] = useState<Marker | null>(null),
    [sourceDialog, setSourceDialog] = useState<Media | null>(null);
  const [uploadBusy, setUploadBusy] = useState(""),
    [uploadProgress, setUploadProgress] = useState(0),
    [savedLabel, setSavedLabel] = useState(
      startup.id ? "Recovered local project" : "Local recovery ready",
    ),
    [saving, setSaving] = useState(false),
    [menu, setMenu] = useState(false);
  const savedId = useRef<string | undefined>(startup.id),
    savedSnapshot = useRef(""),
    importSettings = useRef(startup.settings),
    generation = useRef(0),
    saveInFlight = useRef<Promise<void> | null>(null);
  const [entries, setEntries] = useState<JobEntry[]>([]),
    entriesRef = useRef(entries);
  entriesRef.current = entries;
  const [preview, setPreview] = useState<Preview | null>(null),
    [previewStamp, setPreviewStamp] = useState(""),
    [previewBusy, setPreviewBusy] = useState(false),
    [previewError, setPreviewError] = useState(""),
    [previewWidth, setPreviewWidth] = useState(960),
    [previewNonce, setPreviewNonce] = useState(0);
  const [viewerTab, setViewerTab] = useState<"program" | "source">("program"),
    [program, setProgram] = useState({ url: "", key: "" }),
    [playing, setPlaying] = useState(false),
    [pendingPlay, setPendingPlay] = useState<string | null>(null);
  const resize = useRef<{ y: number; height: number } | null>(null),
    videoRef = useRef<HTMLVideoElement>(null),
    jsonInput = useRef<HTMLInputElement>(null),
    noticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mediaMap = useMemo<MediaMap>(
      () => Object.fromEntries(media.map((m) => [m.id, m])),
      [media],
    ),
    key = useMemo(
      () => `${renderKey(project)}:${mediaRevision}`,
      [project, mediaRevision],
    ),
    keyRef = useRef(key);
  keyRef.current = key;
  const onError = useCallback((e: unknown) => setError(errorMessage(e)), []);
  const notify = useCallback((message: string) => {
    setNotice(message);
    if (noticeTimer.current) clearTimeout(noticeTimer.current);
    noticeTimer.current = setTimeout(() => setNotice(""), 4500);
  }, []);
  const pause = useCallback(() => {
    setPlaying(false);
    videoRef.current?.pause();
  }, []);
  const commit = useCallback(
    (next: Project) => {
      try {
        const valid = validateProject(next);
        dispatch({ type: "commit", project: valid });
        setError("");
      } catch (e) {
        onError(e);
      }
    },
    [onError],
  );
  const seek = useCallback(
    (time: number) => setPlayhead(clamp(time, 0, duration(projectRef.current))),
    [],
  );
  const safe = useCallback(
    (fn: () => void) => {
      try {
        fn();
      } catch (e) {
        onError(e);
      }
    },
    [onError],
  );
  const initialize = useCallback(async () => {
    const results = await Promise.allSettled([
      api<Status>("/api/status"),
      api<Media[]>("/api/media"),
      api<Capabilities>("/api/capabilities"),
      api<FormatCatalog>("/api/formats"),
    ]);
    results.forEach((r, i) => {
      if (r.status === "fulfilled") {
        if (i === 0) {
          const value = r.value as Status;
          if (value.app !== "8K Lossless Video Editor") {
            setConnection("A different service is using the editor port.");
            return;
          }
          setStatus(value);
          setConnection(
            value.ready
              ? ""
              : value.reason || "Renderer prerequisites are not ready.",
          );
        } else if (i === 1) setMedia(r.value as Media[]);
        else if (i === 2) setCapabilities(r.value as Capabilities);
        else setCatalog(r.value as FormatCatalog);
      } else if (i === 0)
        setConnection(
          "Local renderer is offline. Start the editor service, then reconnect.",
        );
    });
  }, []);
  useEffect(() => {
    void initialize();
    return () => {
      if (noticeTimer.current) clearTimeout(noticeTimer.current);
    };
  }, [initialize]);
  useEffect(() => {
    pause();
    setPendingPlay(null);
  }, [key, pause]);
  useEffect(() => {
    if (!findClip(project, selected)) setSelected(null);
    setPlayhead((n) => clamp(n, 0, duration(project)));
  }, [project, selected]);
  const modes = useMemo(() => {
    if (catalog?.export_profiles?.length)
      return catalog.export_profiles.map((p) => ({
        ...p,
        name: p.label || p.name,
        description: Array.isArray(p.notes)
          ? p.notes.join(" ")
          : p.notes || p.reason,
      }));
    return capabilities?.modes || status?.capabilities?.modes || [];
  }, [catalog, capabilities, status]);
  const persist = useCallback(
    async (manual = false) => {
      if (saveInFlight.current) {
        await saveInFlight.current;
        if (
          !manual &&
          savedSnapshot.current === JSON.stringify(projectRef.current)
        )
          return;
      }
      const snapshot = projectRef.current,
        serialized = JSON.stringify(snapshot),
        epoch = generation.current;
      if (!manual && savedSnapshot.current === serialized) return;
      setSaving(true);
      setSavedLabel("Saving…");
      const operation = (async () => {
        try {
          const envelope = await post<ProjectEnvelope>("/api/projects", {
            project: { ...snapshot, id: savedId.current },
          });
          if (generation.current !== epoch) return;
          savedId.current = envelope.id;
          savedSnapshot.current = serialized;
          setSavedLabel("Saved locally");
          if (manual) notify("Project saved on this computer.");
          try {
            localStorage.setItem(
              RECOVERY,
              JSON.stringify({
                project: projectRef.current,
                id: savedId.current,
                touched: importSettings.current.video,
                settings: importSettings.current,
              }),
            );
          } catch {
            /* Server save remains valid when browser storage is full. */
          }
        } catch (e) {
          if (generation.current === epoch) {
            setSavedLabel("Browser recovery only");
            if (manual) onError(e);
          }
        } finally {
          if (generation.current === epoch) setSaving(false);
        }
      })();
      saveInFlight.current = operation;
      await operation;
      if (saveInFlight.current === operation) saveInFlight.current = null;
    },
    [notify, onError],
  );
  useEffect(() => {
    try {
      localStorage.setItem(
        RECOVERY,
        JSON.stringify({
          project,
          id: savedId.current,
          touched: importSettings.current.video,
          settings: importSettings.current,
        }),
      );
    } catch {
      /* Saving a project remains available. */
    }
    if (dragging) return;
    const timer = setTimeout(() => {
      void persist();
    }, 1400);
    return () => clearTimeout(timer);
  }, [project, dragging, persist]);
  const loadProject = useCallback((next: Project, id?: string) => {
    generation.current++;
    savedId.current = id;
    savedSnapshot.current = id ? JSON.stringify(next) : "";
    importSettings.current = newImportSettings(true);
    setSaving(false);
    dispatch({ type: "replace", project: { ...next, id: undefined } });
    setPlayhead(0);
    setSelected(null);
    setProgram({ url: "", key: "" });
    setPreview(null);
    setSavedLabel(id ? "Opened saved project" : "Loaded project JSON");
    setMenu(false);
  }, []);
  const applyFormat = useCallback(
    (p: Project) => {
      importSettings.current = newImportSettings(true);
      commit(p);
    },
    [commit],
  );
  const upload = useCallback(
    async (files: File[], sequence: boolean, fps: string) => {
      if (uploadBusy) return;
      if (
        sequence &&
        (!/^\d+(\/\d+)?$/.test(fps) ||
          !Number.isFinite(
            Number(fps.split("/")[0]) / (Number(fps.split("/")[1]) || 1),
          ))
      ) {
        onError(new Error("Use a rational sequence rate such as 24/1."));
        return;
      }
      setUploadProgress(0);
      try {
        const batches = sequence ? [files] : files.map((f) => [f]);
        for (let i = 0; i < batches.length; i++) {
          setUploadBusy(
            sequence
              ? `Preparing ${files.length}-frame sequence…`
              : `Importing ${i + 1}/${files.length}: ${batches[i][0].name}`,
          );
          const imported = await requestUpload(
            batches[i],
            sequence,
            fps,
            setUploadProgress,
          );
          setMedia((items) => [
            ...items.filter((m) => m.id !== imported.id),
            imported,
          ]);
          setSourceId(imported.id);
          const adopted = adoptImportedSettings(
            projectRef.current,
            imported,
            importSettings.current,
          );
          importSettings.current = adopted.settings;
          if (adopted.project !== projectRef.current) commit(adopted.project);
          setUploadProgress(100);
        }
        notify(
          sequence
            ? "Image sequence imported in natural filename order."
            : `${files.length} source${files.length === 1 ? "" : "s"} imported. Original bytes preserved.`,
        );
      } catch (e) {
        onError(e);
      } finally {
        setUploadBusy("");
        setUploadProgress(0);
      }
    },
    [uploadBusy, onError, commit, notify],
  );
  const addMedia = useCallback(
    (id: string, atEnd = false, at?: number, targetId?: string) =>
      safe(() => {
        const item = mediaMap[id];
        if (!item) throw new Error("Source media is unavailable.");
        let p = projectRef.current;
        const selectedTrack = findClip(p, selected)?.track;
        let track = targetId
          ? p.tracks.find((t) => t.id === targetId)
          : selectedTrack &&
              !selectedTrack.locked &&
              accepts(selectedTrack, item)
            ? selectedTrack
            : p.tracks.find((t) => !t.locked && accepts(t, item));
        if (!track) {
          if (targetId)
            throw new Error("That track cannot accept this source.");
          if (p.tracks.length >= 64)
            throw new Error("A project supports 64 tracks.");
          track = makeTrack(
            item.kind === "audio" ? "audio" : "video",
            item.kind === "audio" ? "Audio" : "Video",
          );
          p = { ...p, tracks: [...p.tracks, track] };
        }
        const start = clamp(
            roundFrame(at ?? (atEnd ? duration(p) : playhead), p),
            0,
            86399.999,
          ),
          clip = makeClip(item, start);
        commit(insertClip(p, clip, track.id, item));
        setSelected(clip.id);
        setViewerTab("program");
        seek(start);
      }),
    [mediaMap, selected, playhead, commit, seek, safe],
  );
  const addTitle = () =>
    safe(() => {
      let p = projectRef.current,
        track = p.tracks.find((t) => t.kind === "title" && !t.locked);
      if (!track) {
        if (p.tracks.length >= 64)
          throw new Error("A project supports 64 tracks.");
        track = makeTrack("title", "Titles");
        p = { ...p, tracks: [...p.tracks, track] };
      }
      const c = makeClip(undefined, Math.min(playhead, 86399.999));
      commit(insertClip(p, c, track.id));
      setSelected(c.id);
      setViewerTab("program");
    });
  const split = (id = selected, time = playhead) =>
    safe(() => {
      if (!id) throw new Error("Select a clip to split.");
      const result = splitClip(projectRef.current, id, time);
      commit(result.project);
      setSelected(result.id);
    });
  const duplicate = () =>
    safe(() => {
      if (!selected) return;
      const result = duplicateClip(projectRef.current, selected);
      commit(result.project);
      setSelected(result.id);
    });
  const remove = () =>
    safe(() => {
      if (selected) {
        commit(removeClip(projectRef.current, selected));
        setSelected(null);
      }
    });
  const addTrack = (kind: TrackKind) => {
    if (project.tracks.length >= 64) {
      onError(new Error("A project supports 64 tracks."));
      return;
    }
    commit({
      ...project,
      tracks: [
        ...project.tracks,
        makeTrack(
          kind,
          `${kind === "video" ? "Video" : kind === "audio" ? "Audio" : "Titles"} ${project.tracks.filter((t) => t.kind === kind).length + 1}`,
        ),
      ],
    });
  };
  const reorder = (id: string, direction: -1 | 1) => {
    const visual = [
        ...project.tracks.filter((t) => t.kind !== "audio").reverse(),
        ...project.tracks.filter((t) => t.kind === "audio"),
      ],
      index = visual.findIndex((t) => t.id === id),
      other = visual[index + direction];
    if (!other) return;
    const raw = [...project.tracks],
      a = raw.findIndex((t) => t.id === id),
      b = raw.findIndex((t) => t.id === other.id);
    [raw[a], raw[b]] = [raw[b], raw[a]];
    commit({ ...project, tracks: raw });
  };
  const newMarker = () => {
    if (project.markers.length >= 2000) {
      onError(new Error("A project supports 2,000 markers."));
      return;
    }
    setMarker({
      id: uid("marker"),
      time: playhead,
      label: `Marker ${project.markers.length + 1}`,
      color: "#e2b86b",
    });
  };
  const createJob = useCallback(
    async (mode: string, previewJob = false) => {
      const snapshot = projectRef.current,
        snapshotKey = keyRef.current;
      if (!duration(snapshot)) {
        onError(new Error("Add a clip or title before rendering."));
        return;
      }
      try {
        const job = await post<Job>("/api/jobs", {
          project: { ...snapshot, id: savedId.current },
          mode,
          preview: previewJob,
        });
        setEntries((items) => [
          ...items,
          {
            job,
            key: snapshotKey,
            preview: previewJob,
            name: snapshot.name,
            mode,
            created: Date.now(),
          },
        ]);
        if (previewJob) setPendingPlay(job.id);
        else notify("Export queued. You can continue editing.");
      } catch (e) {
        onError(e);
      }
    },
    [notify, onError],
  );
  const play = useCallback(() => {
    setViewerTab("program");
    if (program.url && program.key === keyRef.current) {
      if (
        playhead >=
        duration(projectRef.current) - frameTime(projectRef.current)
      ) {
        seek(0);
        if (videoRef.current) videoRef.current.currentTime = 0;
      }
      setPlaying(true);
      return;
    }
    const active = entriesRef.current.find(
      (e) => e.preview && e.key === keyRef.current && !isTerminal(e.job.status),
    );
    if (active) {
      setPendingPlay(active.job.id);
      return;
    }
    void createJob("hevc", true);
  }, [program, playhead, seek, createJob]);
  useEffect(() => {
    if (!entries.some((e) => !isTerminal(e.job.status))) return;
    let active = true;
    const poll = async () => {
      const live = entriesRef.current.filter((e) => !isTerminal(e.job.status));
      const settled = await Promise.allSettled(
        live.map(async (e) => ({
          id: e.job.id,
          job: await api<Job>(`/api/jobs/${e.job.id}`),
        })),
      );
      if (!active) return;
      const updates = new Map<string, Job>();
      settled.forEach((r) => {
        if (r.status === "fulfilled") updates.set(r.value.id, r.value.job);
      });
      if (updates.size)
        setEntries((items) =>
          items.map((e) =>
            updates.has(e.job.id) ? { ...e, job: updates.get(e.job.id)! } : e,
          ),
        );
    };
    const timer = setInterval(() => {
      void poll();
    }, 800);
    void poll();
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [entries.some((e) => !isTerminal(e.job.status))]);
  useEffect(() => {
    if (!pendingPlay) return;
    const entry = entries.find((e) => e.job.id === pendingPlay);
    if (!entry || !isTerminal(entry.job.status)) return;
    setPendingPlay(null);
    if (entry.job.status === "completed" && entry.key === keyRef.current) {
      setProgram({
        url: assetUrl(
          entry.job.output_url || `/api/jobs/${entry.job.id}/download`,
        ),
        key: entry.key,
      });
      setPlayhead(0);
      setPlaying(true);
    } else if (entry.job.status === "failed")
      onError(
        new Error(
          entry.job.error || entry.job.message || "Timeline preview failed.",
        ),
      );
  }, [entries, pendingPlay, onError]);
  const activeRender = entries.find(
    (e) => e.preview && e.key === key && !isTerminal(e.job.status),
  )?.job;
  useEffect(() => {
    if (
      !status?.ready ||
      !clipCount(project) ||
      dragging ||
      playing ||
      viewerTab !== "program" ||
      activeRender
    )
      return;
    let active = true;
    const controller = new AbortController();
    const stamp = `${key}:${playhead}:${previewWidth}`;
    const timer = setTimeout(() => {
      setPreviewBusy(true);
      post<Preview>(
        "/api/preview",
        { project, time: playhead, width: previewWidth },
        controller.signal,
      )
        .then((value) => {
          if (active) {
            setPreview(value);
            setPreviewStamp(stamp);
            setPreviewError("");
          }
        })
        .catch((e) => {
          if (active && !(e instanceof DOMException && e.name === "AbortError"))
            setPreviewError(errorMessage(e));
        })
        .finally(() => {
          if (active) setPreviewBusy(false);
        });
    }, 350);
    return () => {
      active = false;
      controller.abort();
      clearTimeout(timer);
      setPreviewBusy(false);
    };
  }, [
    key,
    playhead,
    previewWidth,
    previewNonce,
    status?.ready,
    dragging,
    playing,
    viewerTab,
    !!activeRender,
  ]);
  const cancel = async (id: string) => {
    try {
      const job = await post<Job>(`/api/jobs/${id}/cancel`, {});
      setEntries((items) =>
        items.map((e) => (e.job.id === id ? { ...e, job } : e)),
      );
    } catch (e) {
      onError(e);
    }
  };
  const actions = useRef({
    play,
    pause,
    split,
    duplicate,
    remove,
    persist,
    newMarker,
  });
  actions.current = {
    play,
    pause,
    split,
    duplicate,
    remove,
    persist,
    newMarker,
  };
  useEffect(() => {
    const listener = (e: KeyboardEvent) => {
      const input =
          e.target instanceof HTMLElement &&
          !!e.target.closest('input,textarea,select,[contenteditable="true"]'),
        command = e.metaKey || e.ctrlKey;
      if (command && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void actions.current.persist(true);
        return;
      }
      if (input || modal || marker || sourceDialog) return;
      if (command && e.key.toLowerCase() === "z") {
        e.preventDefault();
        dispatch({ type: e.shiftKey ? "redo" : "undo" });
        return;
      }
      if (command && e.key.toLowerCase() === "d") {
        e.preventDefault();
        actions.current.duplicate();
        return;
      }
      if (command && e.key.toLowerCase() === "y") {
        e.preventDefault();
        dispatch({ type: "redo" });
        return;
      }
      if (e.code === "Space") {
        e.preventDefault();
        if (playing) actions.current.pause();
        else actions.current.play();
      } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        actions.current.pause();
        setPlayhead((t) =>
          clamp(
            t +
              (e.key === "ArrowRight" ? 1 : -1) *
                frameTime(projectRef.current) *
                (e.shiftKey ? 10 : 1),
            0,
            duration(projectRef.current),
          ),
        );
      } else if (e.key === "Home") {
        e.preventDefault();
        actions.current.pause();
        setPlayhead(0);
      } else if (e.key === "End") {
        e.preventDefault();
        actions.current.pause();
        setPlayhead(duration(projectRef.current));
      } else if (e.key === "Delete" || e.key === "Backspace") {
        e.preventDefault();
        actions.current.remove();
      } else if (e.key.toLowerCase() === "s") actions.current.split();
      else if (e.key.toLowerCase() === "m") actions.current.newMarker();
      else if (e.key === "=" || e.key === "+")
        setPixels((n) => Math.min(400, n * 1.25));
      else if (e.key === "-") setPixels((n) => Math.max(2, n / 1.25));
      else if (e.key === "Escape") {
        actions.current.pause();
        setMenu(false);
      }
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, [playing, modal, marker, sourceDialog]);
  const loadJson = async (file?: File) => {
    if (!file) return;
    try {
      if (file.size > 8 * 1024 * 1024)
        throw new Error("Project JSON must be 8 MB or smaller.");
      const parsed = JSON.parse(await file.text());
      loadProject(validateProject(parsed.project || parsed));
      notify("Project loaded. Relink any offline sources in Project Media.");
    } catch (e) {
      onError(e);
    }
  };
  const busyCount = entries.filter((e) => !isTerminal(e.job.status)).length,
    selectedClip = findClip(project, selected),
    frameFresh = previewStamp === `${key}:${playhead}:${previewWidth}`;
  return (
    <div
      className="editor-shell"
      style={
        { "--timeline-height": `${timelineHeight}px` } as React.CSSProperties
      }
    >
      <header className="app-header">
        <a
          className="brand"
          href="#"
          onClick={(e) => e.preventDefault()}
          aria-label="8K Lossless Video Editor"
        >
          <div className="brand-mark">
            <Layers3 size={22} />
          </div>
          <div>
            <strong>
              8K<span>LOSSLESS</span>
            </strong>
            <small>VIDEO EDITOR</small>
          </div>
        </a>
        <div className="project-menu">
          <button className="project-title" onClick={() => setMenu(!menu)}>
            <span>{project.name}</span>
            <ChevronDown size={13} />
          </button>
          <div className="save-state">
            {saving ? (
              <LoaderCircle size={10} className="spin" />
            ) : (
              <Check size={10} />
            )}{" "}
            {savedLabel}
          </div>
          {menu && (
            <>
              <div className="menu-dismiss" onClick={() => setMenu(false)} />
              <div className="project-dropdown">
                <button
                  onClick={() => {
                    setMenu(false);
                    setModal("new");
                  }}
                >
                  <Plus size={14} /> New project
                </button>
                <button
                  onClick={() => {
                    setMenu(false);
                    setModal("open");
                  }}
                >
                  <FolderOpen size={14} /> Open saved project
                </button>
                <button
                  onClick={() => {
                    setMenu(false);
                    jsonInput.current?.click();
                  }}
                >
                  <FileJson size={14} /> Import project JSON
                </button>
                <button
                  onClick={() => {
                    downloadJson(
                      { ...project, id: savedId.current },
                      `${project.name.replace(/[^a-z0-9_-]/gi, "_")}.8kedit.json`,
                    );
                    setMenu(false);
                  }}
                >
                  <Download size={14} /> Download project JSON
                </button>
                <button
                  onClick={() => {
                    setMenu(false);
                    setModal("settings");
                  }}
                >
                  <Settings2 size={14} /> Sequence settings
                </button>
              </div>
            </>
          )}
        </div>
        <div className="header-history">
          <button
            className="icon-button"
            title="Undo (Ctrl / ⌘ Z)"
            aria-label="Undo"
            disabled={!history.past.length}
            onClick={() => dispatch({ type: "undo" })}
          >
            <Undo2 size={17} />
          </button>
          <button
            className="icon-button"
            title="Redo (Ctrl / ⌘ Shift Z)"
            aria-label="Redo"
            disabled={!history.future.length}
            onClick={() => dispatch({ type: "redo" })}
          >
            <Redo2 size={17} />
          </button>
          <i />
          <button
            className="icon-button"
            title="Save project"
            aria-label="Save project"
            disabled={saving}
            onClick={() => void persist(true)}
          >
            <Save size={17} />
          </button>
        </div>
        <div className="header-spec">
          <span>
            {project.width} × {project.height}
          </span>
          <b>{project.fps} FPS</b>
        </div>
        <div className="header-actions">
          <button className="quiet" onClick={() => setModal("settings")}>
            <Settings2 size={15} />
            <span>Sequence</span>
          </button>
          <button
            className="icon-button"
            aria-label="Keyboard shortcuts and workflow"
            title="Keyboard shortcuts and workflow"
            onClick={() => setModal("help")}
          >
            <HelpCircle size={17} />
          </button>
          <button
            className="primary export-button"
            onClick={() => setModal("export")}
          >
            <Download size={15} /> Export{busyCount > 0 && <b>{busyCount}</b>}
          </button>
        </div>
      </header>
      {connection && (
        <div className="connection-banner">
          <LoaderCircle size={14} />
          <span>{connection}</span>
          <button onClick={() => void initialize()}>Reconnect</button>
        </div>
      )}
      {error && (
        <div className="error-banner" role="alert">
          <span>{error}</span>
          <button
            className="icon-button"
            aria-label="Dismiss error"
            onClick={() => setError("")}
          >
            <X size={15} />
          </button>
        </div>
      )}
      <main className="workspace">
        <MediaBin
          items={media}
          project={project}
          selected={sourceId}
          busy={uploadBusy}
          uploadProgress={uploadProgress}
          onSelect={(id) => {
            setSourceId(id);
            setViewerTab("source");
          }}
          onAdd={addMedia}
          onUpload={(f, s, r) => void upload(f, s, r)}
          onRelink={(id, replacement) =>
            safe(() => {
              commit(relinkMedia(project, id, mediaMap[replacement]));
              notify("Media relinked. Existing edit points preserved.");
            })
          }
          onInterpret={setSourceDialog}
          onFormats={() => setModal("formats")}
        />
        <div className="program-column">
          <MediaErrorBoundary
            resetKey={`${viewerTab}:${key}:${program.url}`}
            onRetry={() => {
              pause();
              setProgram({ url: "", key: "" });
              setPreviewNonce((n) => n + 1);
            }}
          >
            <Viewer
              project={project}
              playhead={playhead}
              source={mediaMap[sourceId || ""]}
              tab={viewerTab}
              onTab={(tab) => {
                pause();
                setViewerTab(tab);
              }}
              preview={preview}
              previewBusy={previewBusy}
              previewError={previewError}
              previewFresh={frameFresh}
              programUrl={program.url}
              programKey={program.key}
              currentKey={key}
              playing={playing}
              renderJob={activeRender}
              previewWidth={previewWidth}
              onWidth={setPreviewWidth}
              onSeek={seek}
              onPlay={play}
              onPause={pause}
              onRetry={() => {
                setProgram({ url: "", key: "" });
                setPreviewNonce((n) => n + 1);
              }}
              onError={onError}
              videoRef={videoRef}
            />
          </MediaErrorBoundary>
          <div className="edit-toolbar">
            <span className="edit-section">SEQUENCE</span>
            <button
              disabled={!selectedClip || selectedClip.track.locked}
              onClick={() => split()}
              title="Split at playhead (S)"
            >
              <Scissors size={14} /> Split
            </button>
            <button
              disabled={!selectedClip || selectedClip.track.locked}
              onClick={duplicate}
              title="Duplicate (Ctrl / ⌘ D)"
            >
              <Copy size={14} /> Duplicate
            </button>
            <button
              disabled={!selectedClip || selectedClip.track.locked}
              onClick={remove}
              title="Remove clip (Delete)"
            >
              <Trash2 size={14} /> Remove
            </button>
            <i />
            <button onClick={addTitle}>
              <Type size={15} /> Add title
            </button>
            <span className="edit-source-label">
              {selectedClip
                ? `${selectedClip.track.name} / ${selectedClip.track.kind === "title" ? "Title" : mediaMap[selectedClip.clip.media_id || ""]?.name || "Offline source"}`
                : "Non-destructive timeline"}
            </span>
          </div>
        </div>
        <Inspector
          key={selected || "none"}
          project={project}
          selected={selected}
          media={mediaMap}
          playhead={playhead}
          onChange={commit}
          onSeek={seek}
          onError={onError}
        />
      </main>
      <div
        className="workspace-resizer"
        role="separator"
        aria-label="Resize timeline"
        aria-orientation="horizontal"
        aria-valuemin={200}
        aria-valuemax={Math.round(window.innerHeight * 0.6)}
        aria-valuenow={timelineHeight}
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "ArrowUp" || e.key === "ArrowDown") {
            e.preventDefault();
            e.stopPropagation();
            setTimelineHeight((n) =>
              clamp(
                n + (e.key === "ArrowUp" ? 20 : -20),
                200,
                window.innerHeight * 0.6,
              ),
            );
          }
        }}
        onPointerDown={(e) => {
          resize.current = { y: e.clientY, height: timelineHeight };
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (resize.current)
            setTimelineHeight(
              clamp(
                resize.current.height + resize.current.y - e.clientY,
                200,
                window.innerHeight * 0.6,
              ),
            );
        }}
        onPointerUp={() => {
          resize.current = null;
        }}
        onPointerCancel={() => {
          resize.current = null;
        }}
      >
        <i />
      </div>
      <Timeline
        project={project}
        media={mediaMap}
        selected={selected}
        playhead={playhead}
        pixels={pixels}
        snap={snap}
        tool={tool}
        onSelect={(id) => {
          setSelected(id);
          const source = findClip(project, id)?.clip.media_id;
          if (source) setSourceId(source);
          setViewerTab("program");
        }}
        onSeek={(time) => {
          pause();
          seek(time);
        }}
        onChange={commit}
        onTransient={(p) => dispatch({ type: "transient", project: p })}
        onGesture={(before, p) => {
          try {
            dispatch({ type: "gesture", before, project: validateProject(p) });
          } catch (e) {
            dispatch({ type: "transient", project: before });
            onError(e);
          }
        }}
        onDragging={setDragging}
        onError={onError}
        onSplit={split}
        onDropMedia={(id, time, track) => addMedia(id, false, time, track)}
        onPixels={setPixels}
        onSnap={() => setSnap((n) => !n)}
        onTool={setTool}
        onTrack={(id, change) => commit(updateTrack(project, id, change))}
        onAddTrack={addTrack}
        onRemoveTrack={(id) => {
          const track = project.tracks.find((t) => t.id === id);
          if (track?.locked) {
            onError(new Error("Unlock the track before removing it."));
            return;
          }
          commit({
            ...project,
            tracks: project.tracks.filter((t) => t.id !== id),
          });
        }}
        onReorder={reorder}
        onMarker={newMarker}
        onEditMarker={(id) =>
          setMarker(project.markers.find((m) => m.id === id) || null)
        }
      />
      <footer className="status-bar">
        <span>
          <i className={`status-dot ${status?.ready ? "online" : ""}`} />
          {status?.ready
            ? `${status.device?.toUpperCase() || "CPU"} · ${status.gpu || "Local processing"}`
            : "LOCAL SERVICE OFFLINE"}
        </span>
        <span>
          <ShieldCheck size={11} /> Originals preserved · Local files only
        </span>
        <button onClick={() => setModal("formats")}>Format support</button>
        <span className="status-version">
          8K LOSSLESS · {status?.version || "0.1.0-local"}
        </span>
      </footer>
      {notice && (
        <div className="toast" role="status">
          <Check size={16} />
          {notice}
        </div>
      )}
      <input
        hidden
        ref={jsonInput}
        type="file"
        accept="application/json,.json,.8kedit"
        onChange={(e) => {
          void loadJson(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      {modal === "settings" && (
        <ProjectSettings
          project={project}
          onApply={applyFormat}
          onClose={() => setModal(null)}
          onError={onError}
        />
      )}
      {modal === "open" && (
        <OpenProject
          onOpen={loadProject}
          onClose={() => setModal(null)}
          onError={onError}
        />
      )}
      {modal === "formats" && (
        <FormatsDialog catalog={catalog} onClose={() => setModal(null)} />
      )}
      {modal === "export" && (
        <ExportDialog
          project={project}
          modes={modes}
          entries={entries}
          onExport={(mode) => void createJob(mode)}
          onCancel={(id) => void cancel(id)}
          onClose={() => setModal(null)}
        />
      )}
      {modal === "new" && (
        <Modal
          title="Start a new project"
          subtitle="Your current project remains in browser recovery and can be saved locally."
          onClose={() => setModal(null)}
        >
          <div className="modal-body">
            <p>
              Save the current project before starting a new sequence to keep it
              in your project library.
            </p>
          </div>
          <footer>
            <button
              onClick={() => {
                loadProject(newProject());
                importSettings.current = newImportSettings();
                setModal(null);
              }}
            >
              New empty project
            </button>
            <button
              className="primary"
              onClick={() =>
                void persist(true).then(() => {
                  if (
                    savedSnapshot.current !== JSON.stringify(projectRef.current)
                  ) {
                    onError(
                      new Error(
                        "Save did not finish. Download project JSON or retry before starting a new project.",
                      ),
                    );
                    return;
                  }
                  loadProject(newProject());
                  importSettings.current = newImportSettings();
                  setModal(null);
                })
              }
            >
              Save & start new
            </button>
          </footer>
        </Modal>
      )}
      {modal === "help" && (
        <Modal
          title="A focused editing workspace"
          subtitle="Everything runs locally. Sources remain untouched."
          wide
          onClose={() => setModal(null)}
        >
          <div className="modal-body help-grid">
            <section>
              <h3>Build your sequence</h3>
              <p>
                Import media, then drag onto a compatible track. Double-click a
                source to insert at the playhead. Import image sequences in
                natural filename order at an explicit frame rate.
              </p>
              <h3>Edit with precision</h3>
              <p>
                Drag a clip to move it, or drag its edge to trim. Higher video
                tracks composite over lower tracks. Unlock tracks to edit; mute
                changes the rendered program. Diamonds animate clip-local values
                with linear interpolation.
              </p>
              <h3>See the actual result</h3>
              <p>
                Program frames are rendered from your current edits. Play
                renders the full timeline to a playback proxy. Source view plays
                the ungraded source proxy. Edits invalidate program playback.
              </p>
              <h3>Export honestly</h3>
              <p>
                RGB16 FFV1 master verifies rendered RGB samples. Codec-lossless
                YUV modes can still transform RGB samples. Delivery codecs are
                clearly labeled. Check Format support for installed formats and
                SDK limitations.
              </p>
            </section>
            <section>
              <h3>Keyboard shortcuts</h3>
              {[
                ["Space", "Render / play / pause program"],
                ["← / →", "Previous / next frame"],
                ["Shift ← / →", "Move ten frames"],
                ["Home / End", "Sequence start / end"],
                ["S", "Split selected clip"],
                ["⌘ / Ctrl D", "Duplicate selected clip"],
                ["Delete", "Remove selected clip"],
                ["M", "Add marker at playhead"],
                ["⌘ / Ctrl Z", "Undo"],
                ["⌘ / Ctrl Shift Z", "Redo"],
                ["⌘ / Ctrl S", "Save project"],
                ["+ / −", "Zoom timeline"],
              ].map(([shortcut, label]) => (
                <div className="help-shortcut" key={shortcut}>
                  <kbd>{shortcut}</kbd>
                  <span>{label}</span>
                </div>
              ))}
              <p className="field-note">
                Project JSON references local media IDs. When moving a project
                to another computer, import its sources and use the offline
                relink controls.
              </p>
            </section>
          </div>
        </Modal>
      )}
      {marker && (
        <MarkerDialog
          marker={marker}
          onApply={(m) =>
            commit({
              ...project,
              markers: [
                ...project.markers.filter((v) => v.id !== m.id),
                m,
              ].sort((a, b) => a.time - b.time),
            })
          }
          onDelete={(id) =>
            commit({
              ...project,
              markers: project.markers.filter((m) => m.id !== id),
            })
          }
          onClose={() => setMarker(null)}
        />
      )}
      {sourceDialog && (
        <SourceDialog
          media={sourceDialog}
          onApply={(m) => {
            setMedia((items) => items.map((v) => (v.id === m.id ? m : v)));
            setMediaRevision((n) => n + 1);
            setProgram({ url: "", key: "" });
            setPreviewNonce((n) => n + 1);
            notify("Source interpretation saved.");
          }}
          onClose={() => setSourceDialog(null)}
          onError={onError}
        />
      )}
    </div>
  );
}
