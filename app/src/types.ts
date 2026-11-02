export type TrackKind = "video" | "audio" | "title";
export type MediaKind = "video" | "audio" | "image" | "sequence";
export type KeyframeKey = "opacity" | "x" | "y" | "scale" | "volume";
export type Keyframe = { time: number; value: number };
export type Clip = {
  id: string;
  media_id?: string;
  start: number;
  in: number;
  duration: number;
  speed: number;
  reverse: boolean;
  opacity: number;
  volume: number;
  fade_in: number;
  fade_out: number;
  x: number;
  y: number;
  scale: number;
  rotation: number;
  flip_h: boolean;
  flip_v: boolean;
  crop: { left: number; right: number; top: number; bottom: number };
  exposure: number;
  contrast: number;
  saturation: number;
  temperature: number;
  blur: number;
  sharpen: number;
  text: string;
  font_size: number;
  color: string;
  box_color: string;
  box_opacity: number;
  input_transfer: "auto" | "linear" | "srgb" | "bt709" | "pq" | "hlg";
  input_primaries: "auto" | "bt709" | "bt2020" | "smpte432" | "acescg";
  reference_white_nits: number;
  keyframes: Partial<Record<KeyframeKey, Keyframe[]>>;
  lens: Record<string, number>;
};
export type Track = {
  id: string;
  name: string;
  kind: TrackKind;
  muted: boolean;
  locked: boolean;
  clips: Clip[];
};
export type Marker = { id: string; time: number; label: string; color: string };
export type Project = {
  schema_version: 1;
  id?: string;
  name: string;
  width: number;
  height: number;
  fps: string;
  background: string;
  color_space: "bt709" | "bt2020-pq" | "bt2020-hlg";
  audio_sample_rate: 44100 | 48000 | 96000;
  audio_layout: "mono" | "stereo" | "5.1" | "7.1";
  tracks: Track[];
  markers: Marker[];
};
export type Media = {
  id: string;
  name: string;
  kind: MediaKind;
  width: number;
  height: number;
  display_width?: number;
  display_height?: number;
  fps: number;
  fps_exact: string;
  duration: number;
  codec: string;
  container: string;
  pixel_format: string;
  bit_depth: number;
  audio_streams: number;
  audio_channels?: number;
  audio_layout?: string;
  audio_sample_rate?: number;
  transfer: string;
  color_primaries: string;
  color_space: string;
  color_range: string;
  bytes: number;
  sha256: string;
  proxy_url?: string;
  poster_url?: string;
  waveform?: number[];
  frame_count?: number;
  warnings: string[];
};
export type MediaMap = Record<string, Media>;
export type ExportMode = {
  id: string;
  name: string;
  extension?: string;
  available: boolean;
  reason?: string;
  description?: string;
  lossless?: boolean;
};
export type Capabilities = {
  modes: ExportMode[];
  decoders?: string[];
  encoders?: string[];
  acceleration?: {
    device: string;
    gpu: string;
    accelerated: boolean;
    reason?: string;
  };
  compositing?: string;
  [key: string]: unknown;
};
export type Status = {
  app: string;
  version: string;
  ready: boolean;
  device?: string | null;
  gpu?: string;
  accelerated?: boolean;
  reason?: string;
  ffmpeg?: boolean;
  capabilities?: Capabilities;
};
export type Preview = {
  url: string;
  width: number;
  height: number;
  time: number;
  render_ms: number;
  warnings?: string[];
};
export type RenderResult = {
  width?: number;
  height?: number;
  frames?: number;
  fps_exact?: string;
  codec?: string;
  encoder?: string;
  pixel_format?: string;
  bit_depth?: number;
  container?: string;
  audio?: string;
  encode_quality?: string;
  warnings?: string[];
  bytes?: number;
  [key: string]: unknown;
};
export type Job = {
  id: string;
  status:
    | "queued"
    | "running"
    | "completed"
    | "failed"
    | "cancelled"
    | "cancelling";
  progress: number;
  message: string;
  error?: string | null;
  output_url?: string | null;
  output_name?: string | null;
  result?: RenderResult;
};
export type ProjectEnvelope = {
  id: string;
  name?: string;
  project: Project;
  updated_at: number;
};
export type FormatEntry = {
  id: string;
  label: string;
  category: string;
  extensions: string[];
  kind?: string;
  decoders?: Record<string, boolean>;
  import_available: boolean;
  scope?: string;
  reason?: string;
  [key: string]: unknown;
};
export type FormatExport = ExportMode & {
  label: string;
  category: string;
  codec?: string;
  pixel_format?: string;
  rgb_lossless?: boolean;
  hardware?: string;
  notes?: string | string[];
};
export type FormatCatalog = {
  formats: FormatEntry[];
  export_profiles: FormatExport[];
  notes?: string[];
  [key: string]: unknown;
};
