export interface HealthResponse {
  status: string;
  version?: string;
  uptime?: number;
}

export interface LoginResponse {
  ok: boolean;
  token?: string;
}

export interface S2CTransfer {
  id: string;
  bytes: number;
  frames: number;
  modulation: string;
  ecc: string;
  resolution: string;
  symbol_px: number;
  repeat: number;
  fps: number;
  finder_shape: string;
  finder_px: number;
  finder_margin: number;
  created_at: string;
}

export interface S2CResult {
  ok: boolean;
  complete: boolean;
  bytes_received: number;
  bytes_total: number;
  crc_ok: boolean;
  sha256?: string;
  detail?: string;
}

export interface S2CState {
  transfer: S2CTransfer;
  result: S2CResult;
  decode: S2CResult;
}

export interface Dataset {
  id: string;
  name: string;
  path: string;
  images: number;
  labels: number;
  created_at: string;
}

export interface Run {
  id: string;
  name: string;
  dataset: string;
  status: string;
  epoch: number;
  epochs: number;
  mAP: number;
  started_at: string;
  finished_at?: string;
  artifacts?: { weights: string; onnx: string };
}

export interface Job {
  id: string;
  type: string;
  status: string;
  progress: number;
  message?: string;
  created_at: string;
}

export interface TrainDefaults {
  epochs: number;
  imgsz: number;
  batch: number;
  device: string;
  workers: number;
  model: string;
  seed: number;
  project: string;
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
  project?: string;
}

export interface Project {
  id: string;
  name: string;
  path: string;
  created_at: string;
}

export interface CaptureStatus {
  running: boolean;
  pid?: number;
  port?: number;
  url?: string;
}

export interface DecodeResult {
  ok: boolean;
  bytes: number;
  sha256?: string;
  detail?: string;
}

export interface WizardState {
  step: number;
  total: number;
  title: string;
  description: string;
}