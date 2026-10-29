export interface LoginResponse {
  ok: boolean;
  password_set?: boolean;
  token?: string;
}

export interface ProgressChecklistItem {
  label: string;
  done: boolean;
}

export interface ProgressAction {
  kind: "link" | "post";
  href?: string;
  endpoint?: string;
  label?: string;
  payload?: Record<string, any>;
  then?: string;
}

export interface ProgressStage {
  id: string;
  title: string;
  short: string;
  icon: string;
  href: string;
  why: string;
  cta: string;
  detail: string;
  done: boolean;
  current?: boolean;
  blocked?: boolean;
  blockers?: string[];
  blocked_by?: string[];
  checklist: ProgressChecklistItem[];
  action?: ProgressAction | null;
}

export interface ProgressResponse {
  stages: ProgressStage[];
  next: ProgressStage | null;
  percent: number;
  done: number;
  total: number;
  complete: boolean;
  counts: {
    datasets: number;
    images: number;
    runs: number;
    models: number;
    projects: number;
    transfers: number;
    shots: number;
    complete_transfers: number;
    jobs_running: number;
  };
  last_job?: {
    id: string;
    kind: string;
    title: string;
    status: string;
    progress: number;
    live?: boolean;
  } | null;
  generated?: number;
}

export interface DatasetSummary {
  name: string;
  path?: string;
  images: number;
  labels?: number;
  bytes?: number;
  yaml?: string | null;
  splits?: Record<string, { images: number; labels: number; bytes: number }>;
  source?: string;
  status?: string;
  project?: string | null;
  project_id?: number | null;
  missing?: boolean;
  skipped?: number;
  labels_ok?: number;
  labels_bad?: number;
  modified?: number;
  summary?: Record<string, any>;
}

export interface RunSummary {
  name: string;
  path?: string;
  has_weights: boolean;
  has_last?: boolean;
  has_eval?: boolean;
  weights_bytes?: number;
  modified?: number;
  epoch?: number | null;
  epochs?: number | null;
  status?: string;
  metrics?: Record<string, number>;
  mAP?: number | null;
  dataset?: string | null;
  train_args?: Record<string, any>;
  promoted?: boolean;
}

export interface ModelSummary {
  name: string;
  weights: string;
  has_weights: boolean;
  shipped?: boolean;
  bytes: number;
  card?: Record<string, any>;
  metrics?: Record<string, number>;
  active: boolean;
  modified?: number;
}

export interface JobSnapshot {
  id: string;
  kind: string;
  title: string;
  argv?: string[];
  status: string;
  live?: boolean;
  created?: number;
  started?: number | null;
  finished?: number | null;
  elapsed?: number;
  returncode?: number | null;
  meta?: Record<string, any>;
  progress: number;
  message?: string;
  metrics?: Record<string, number>;
  error?: string;
  tail?: string;
}

export interface ProjectItem {
  id: number;
  slug: string;
  title: string;
  description: string;
  status: "development" | "finished" | "archived" | string;
  tags: string[];
  settings?: Record<string, any>;
  created?: number;
  updated?: number;
  n_datasets?: number;
  n_runs?: number;
}

export interface FinderShapeOption {
  name: string;
  size: number;
  symmetry?: string;
  description?: string;
}

export interface FinderSpanOption {
  name: string;
  description: string;
}

export interface PaletteEntry {
  name: string;
  bits: number;
  pair: boolean;
  colours: number;
  hex: string[];
  max_user_payload: number;
  cells: number;
  ecc: string;
}

export interface S2CTransferInfo {
  id: string;
  name: string;
  bytes: number;
  sha1: string;
  frames: number;
  repeat: number;
  modulation: string;
  ecc: string;
  resolution: string;
  cell_px: number;
  cells: number | null;
  payload_per_frame: number;
  frame_px: [number, number];
  finder: string | null;
  capacity_lost_pct: number | null;
  rate_fps: number;
  created: number;
  received: number;
  duplicates: number;
  failed: number;
  complete: boolean;
  last_error: string;
}

export interface S2CResultInfo {
  complete: boolean;
  received: number;
  frames: number;
  duplicates: number;
  failed: number;
  last_error?: string;
  bytes?: number;
  sha1?: string;
  sha1_ok?: boolean;
  text?: string | null;
  name?: string;
}

export interface S2CLoopbackRow {
  frame: number;
  ok: boolean;
  payload_matches: boolean;
  corner_err: number | null;
  detections: number;
  ring_score: number | null;
  finder_score: number | null;
  ms: number;
  mode: string;
  reason: string;
}

export interface S2CLoopbackReport {
  frames: S2CLoopbackRow[];
  decoded: number;
  corner_err_median: number | null;
  complete: boolean;
  sha1_ok: boolean;
  received: number;
  total_frames: number;
  weights: string | null;
  result?: S2CResultInfo;
}

export interface StartTrainPayload {
  dataset: string;
  name?: string;
  epochs?: number;
  imgsz?: number;
  batch?: number;
  device?: string;
  workers?: number;
  model?: string;
  seed?: number;
  mosaic?: number;
  project?: string;
  patience?: number;
  early_stop_start?: number;
  eval_every?: number;
}

export interface CurveData {
  name: string;
  epochs: number[];
  series: Record<string, (number | null)[]>;
  all_columns?: string[];
  source?: string | null;
}

export interface EvalModeMetrics {
  decode_rate: number | null;
  detection_rate?: number | null;
  corner_err_pct_median?: number | null;
  corner_err_pct_p90?: number | null;
  corner_err_median?: number | null;
  corner_err_p90?: number | null;
  ms_mean?: number | null;
  ms_per_frame?: number | null;
}

export interface EvalReport {
  weights?: string;
  imgsz?: number;
  levels: Record<string, {
    n?: number;
    classical?: EvalModeMetrics;
    yolo?: EvalModeMetrics;
    auto?: EvalModeMetrics;
    [k: string]: any;
  }>;
}

export interface DiagnoseCheck {
  id: string;
  ok: boolean;
  detail: string;
  fix?: string;
}

export interface DiagnoseResponse {
  ok: boolean;
  service?: string;
  build?: string;
  scheme?: string;
  secure_context?: boolean;
  host?: string;
  behind_proxy?: boolean;
  trust_proxy?: boolean;
  force_https?: boolean;
  cookie_mode?: string;
  cookie_secure?: boolean;
  verdict?: string;
  checks?: DiagnoseCheck[];
  problems?: string[];
  database?: Record<string, any>;
  bus?: Record<string, any>;
}

export interface LabSimulateResponse {
  ok: boolean;
  payload_matches: boolean;
  decoded_text: string;
  total_frames_for_text: number;
  frame_body_bytes: number;
  config: Record<string, any>;
  telemetry: {
    mode_requested: string;
    yolo_active: boolean;
    yolo_note?: string | null;
    detector: string;
    detections: number;
    ring_score: number | null;
    finder_score: number | null;
    quality: number | null;
    n_corrected: number;
    corner_err_pct: number | null;
    elapsed_ms: number;
    timings_ms?: Record<string, number>;
    reason: string;
  };
  tx_preview: string;
  cam_overlay: string;
  rectified: string;
}

export interface LabDecodeResponse {
  ok: boolean;
  decoded_text: string;
  decoded_bytes: number;
  image_size: [number, number];
  telemetry: {
    mode_requested: string;
    yolo_active: boolean;
    detector: string;
    detections: number;
    ring_score: number | null;
    finder_score: number | null;
    quality: number | null;
    n_corrected: number;
    seq: number | null;
    elapsed_ms: number;
    reason: string;
  };
  cam_overlay: string;
  rectified: string;
}
