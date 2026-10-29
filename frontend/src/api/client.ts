import type {
  CurveData,
  DatasetSummary,
  DiagnoseResponse,
  EvalReport,
  FinderShapeOption,
  FinderSpanOption,
  JobSnapshot,
  LabDecodeResponse,
  LabSimulateResponse,
  LoginResponse,
  ModelSummary,
  PaletteEntry,
  ProgressResponse,
  ProjectItem,
  RunSummary,
  S2CLoopbackReport,
  S2CResultInfo,
  S2CTransferInfo,
  StartTrainPayload,
} from "../types";

const BASE =
  (import.meta as any).env?.VITE_API_BASE ||
  (window as any).__API_BASE__ ||
  "/api";

const TOKEN_STORAGE_KEY = "om_auth_token";

export function getAuthToken(): string {
  try {
    return sessionStorage.getItem(TOKEN_STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

export function setAuthToken(tok?: string | null) {
  try {
    if (tok) sessionStorage.setItem(TOKEN_STORAGE_KEY, tok);
    else sessionStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    // ignore storage errors
  }
}

function appendTokenQuery(url: string): string {
  const tok = getAuthToken();
  if (!tok) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}_tok=${encodeURIComponent(tok)}`;
}

function resolveApiUrl(path: string, withQueryToken = false): string {
  let out: string;
  if (/^https?:\/\//i.test(path)) {
    out = path;
  } else if (path.startsWith("/api/") || path === "/api") {
    out = BASE === "/api" ? path : `${BASE}${path.slice(4)}`;
  } else {
    out = `${BASE}${path.startsWith("/") ? path : `/${path}`}`;
  }
  return withQueryToken && new URL(out, window.location.href).origin !== window.location.origin ? appendTokenQuery(out) : out;
}

async function parseError(res: Response): Promise<string> {
  const text = await res.text();
  if (!text) return `HTTP ${res.status}`;
  try {
    const parsed = JSON.parse(text);
    if (typeof parsed?.detail === "string") return parsed.detail;
    if (typeof parsed?.error === "string") return parsed.error;
  } catch {
    // plain text
  }
  return text;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const tok = getAuthToken();
  const res = await fetch(resolveApiUrl(path), {
    ...init,
    credentials: "include",
    headers: {
      ...(tok ? { "X-OM-Token": tok } : {}),
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    throw new Error(await parseError(res));
  }
  return res.json() as Promise<T>;
}

export const api = {
  eventStreamUrl: () => resolveApiUrl("/events", true),
  // ---------------- Auth & Diagnostics ----------------
  health: () =>
    fetch("/healthz", { credentials: "include" }).then(async (r) => {
      if (!r.ok) throw new Error(await parseError(r));
      return r.json();
    }),

  diagnose: () => request<DiagnoseResponse>("/suite/diagnose"),

  login: async (password: string) => {
    const r = await request<LoginResponse>("/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (r.token) setAuthToken(r.token);
    return r;
  },

  logout: async () => {
    // Send the bearer token before clearing it so cross-origin sessions are
    // revoked even when the browser does not attach the session cookie.
    try {
      return await request<{ ok: boolean }>("/logout", { method: "POST" });
    } finally {
      setAuthToken(null);
    }
  },

  authStatus: async () => {
    try {
      await request<{ authenticated: boolean }>("/me");
      return { authed: true };
    } catch {
      return { authed: false };
    }
  },

  // ---------------- Global Suite State & Guided Progress ----------------
  status: () => request<any>("/suite/status"),
  env: () => request<any>("/suite/env"),
  progress: () => request<ProgressResponse>("/suite/progress"),
  stateOverview: () => request<any>("/suite/state"),
  recentEvents: (limit = 40, kind?: string) =>
    request<{ events: any[]; bus: any }>(
      `/suite/events?limit=${limit}${kind ? `&kind=${encodeURIComponent(kind)}` : ""}`
    ),
  exportManifestUrl: () => resolveApiUrl("/suite/export/manifest", true),
  qrSvgUrl: (data: string, scale = 6) =>
    resolveApiUrl(`/qr.svg?data=${encodeURIComponent(data)}&scale=${scale}`, true),

  post: (path: string, body?: any) =>
    request<any>(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),

  // ---------------- Projects ----------------
  listProjects: (status?: string) =>
    request<{ projects: ProjectItem[]; enabled: boolean; statuses?: string[] }>(
      `/suite/projects${status ? `?status=${encodeURIComponent(status)}` : ""}`
    ).then((r) => r.projects || []),

  getProject: (slugOrId: string | number) =>
    request<{
      project: ProjectItem;
      datasets?: any[];
      runs?: any[];
      transfers?: any[];
      jobs?: any[];
    }>(`/suite/projects/${encodeURIComponent(String(slugOrId))}`),

  createProject: (payload: {
    slug?: string;
    title?: string;
    description?: string;
    status?: string;
    tags?: string[];
  }) =>
    request<{ project: ProjectItem }>("/suite/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),

  updateProject: (
    slugOrId: string | number,
    payload: Partial<{
      title: string;
      description: string;
      status: string;
      tags: string[];
      slug: string;
    }>
  ) =>
    request<{ project: ProjectItem }>(
      `/suite/projects/${encodeURIComponent(String(slugOrId))}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }
    ),

  deleteProject: (
    slugOrId: string | number,
    opts?: { deleteDatasets?: boolean; reassignTo?: string }
  ) => {
    const q = new URLSearchParams();
    if (opts?.deleteDatasets) q.set("delete_datasets", "true");
    if (opts?.reassignTo) q.set("reassign_to", opts.reassignTo);
    const qs = q.toString();
    return request<{ ok: boolean }>(
      `/suite/projects/${encodeURIComponent(String(slugOrId))}${qs ? `?${qs}` : ""}`,
      { method: "DELETE" }
    );
  },

  // ---------------- Datasets & Keypoint Editor ----------------
  listDatasets: () =>
    request<{ datasets: DatasetSummary[] }>("/suite/datasets").then(
      (r) => r.datasets || []
    ),

  getDataset: (name: string) =>
    request<DatasetSummary>(`/suite/datasets/${encodeURIComponent(name)}`),

  synthDataset: (payload: {
    name?: string;
    train: number;
    val: number;
    seed?: number;
    level?: number;
    project?: string;
  }) =>
    request<{ job: JobSnapshot; dataset: string }>("/suite/datasets/synth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),

  capturesDataset: (payload: {
    name?: string;
    val_frac?: number;
    verified_only?: boolean;
    project?: string;
  }) =>
    request<{ job: JobSnapshot; dataset: string }>("/suite/datasets/captures", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),

  mergeDatasets: (payload: {
    name?: string;
    sources: string[];
    project?: string;
  }) =>
    request<{ job: JobSnapshot; dataset: string }>("/suite/datasets/merge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),

  datasetPreviewUrl: (name: string, split = "train", index = 0, bust = 0) =>
    resolveApiUrl(
      `/suite/datasets/${encodeURIComponent(name)}/preview?split=${encodeURIComponent(
        split
      )}&index=${index}${bust ? `&t=${bust}` : ""}`,
      true
    ),

  datasetExportZipUrl: (name: string) =>
    resolveApiUrl(`/suite/datasets/${encodeURIComponent(name)}/export.zip`, true),

  datasetImages: (name: string, split = "train", limit = 200, offset = 0) =>
    request<{ split: string; total: number; images: string[] }>(
      `/suite/datasets/${encodeURIComponent(name)}/images?split=${encodeURIComponent(
        split
      )}&limit=${limit}&offset=${offset}`
    ),

  datasetImageUrl: (name: string, split: string, filename: string, size?: number) =>
    resolveApiUrl(
      `/suite/datasets/${encodeURIComponent(name)}/image/${encodeURIComponent(
        split
      )}/${encodeURIComponent(filename)}${size ? `?size=${size}` : ""}`,
      true
    ),

  assignDataset: (name: string, project: string, allowMove = false) =>
    request(`/suite/datasets/${encodeURIComponent(name)}/assign`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project, allow_move: allowMove }) }),
  curateDataset: (name: string, destination: string, project: string, selected: Record<string, string[]>) =>
    request<{ dataset: string; images: number }>(`/suite/datasets/${encodeURIComponent(name)}/curate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: destination, project: project || undefined, selected }) }),

  datasetQuality: (name: string, offset = 0, limit = 200) => request<{ counts: Record<string, number>; issue_count: number; issues: {kind: string; file: string; detail: string; split?: string; image?: string; index?: number}[]; truncated: boolean }>(`/suite/datasets/${encodeURIComponent(name)}/quality?offset=${offset}&limit=${limit}`),
  cropStatus: (name: string, split: string, filename: string) =>
    request<{ width: number; height: number; has_backup: boolean; revision: string }>(`/suite/datasets/${encodeURIComponent(name)}/crop/${encodeURIComponent(split)}/${encodeURIComponent(filename)}`),
  cropImage: (name: string, split: string, filename: string, crop: { x: number; y: number; width: number; height: number; revision: string }) =>
    request<{ ok: boolean }>(`/suite/datasets/${encodeURIComponent(name)}/crop/${encodeURIComponent(split)}/${encodeURIComponent(filename)}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(crop) }),
  revertImage: (name: string, split: string, filename: string) =>
    request<{ ok: boolean }>(`/suite/datasets/${encodeURIComponent(name)}/revert/${encodeURIComponent(split)}/${encodeURIComponent(filename)}`, { method: "POST" }),

  datasetLabel: (name: string, split: string, filename: string) =>
    request<{
      filename: string;
      text: string;
      keypoints: { x: number; y: number; v: number }[];
      path: string;
    }>(
      `/suite/datasets/${encodeURIComponent(name)}/label/${encodeURIComponent(
        split
      )}/${encodeURIComponent(filename)}`
    ),

  saveDatasetLabel: (name: string, split: string, filename: string, payload: any) =>
    request<{ ok: boolean; path: string; text: string }>(
      `/suite/datasets/${encodeURIComponent(name)}/label/${encodeURIComponent(
        split
      )}/${encodeURIComponent(filename)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }
    ),

  deleteDataset: (name: string) =>
    request<{ ok: boolean }>(`/suite/datasets/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),

  // ---------------- Capture Station ----------------
  captureCapabilities: () => request<any>("/capabilities"),
  listCaptureSessions: () =>
    request<{ sessions: any[] }>("/sessions").then((r) => r.sessions || []),
  getCaptureSession: (sid: string) =>
    request<any>(`/sessions/${encodeURIComponent(sid)}`),
  createCaptureSession: (payload: {
    label?: string;
    count?: number;
    resolution?: string;
    seed?: number;
    modulations?: string[];
    eccs?: string[];
  }) =>
    request<any>("/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  sessionFrameUrl: (sid: string, index?: number) =>
    resolveApiUrl(
      `/sessions/${encodeURIComponent(sid)}/frame.png${
        index !== undefined ? `?index=${index}` : ""
      }`
    ),
  sessionUndoShot: (sid: string) =>
    request<any>(`/sessions/${encodeURIComponent(sid)}/reject`, { method: "POST" }),
  sessionSkipFrame: (sid: string) =>
    request<any>(`/sessions/${encodeURIComponent(sid)}/skip`, { method: "POST" }),
  sessionSeek: (sid: string, index: number) =>
    request<any>(`/sessions/${encodeURIComponent(sid)}/seek`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index }),
    }),
  sessionExportUrl: (sid: string) =>
    resolveApiUrl(`/sessions/${encodeURIComponent(sid)}/export`),

  // ---------------- Training, Runs & Evaluation ----------------
  trainDefaults: () => request<any>("/suite/train/defaults"),

  startTrain: (payload: StartTrainPayload) =>
    request<{ job: JobSnapshot; run: string }>("/suite/train", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),

  listRuns: () =>
    request<{ runs: RunSummary[]; active: any }>("/suite/runs").then(
      (r) => r.runs || []
    ),

  resumeRun: (name: string, payload?: any) =>
    request<{ job: JobSnapshot; run: string }>(
      `/suite/runs/${encodeURIComponent(name)}/resume`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
      }
    ),

  extendRun: (name: string, payload: { epochs: number; batch?: number; imgsz?: number }) =>
    request<{ job: JobSnapshot; run: string; base: string }>(
      `/suite/runs/${encodeURIComponent(name)}/extend`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }
    ),

  runCurve: (name: string) =>
    request<CurveData>(`/suite/runs/${encodeURIComponent(name)}/curve`),

  runImages: (name: string) =>
    request<{ run: string; images: string[] }>(
      `/suite/runs/${encodeURIComponent(name)}/images`
    ),

  runImageUrl: (name: string, filename: string) =>
    resolveApiUrl(
      `/suite/runs/${encodeURIComponent(name)}/image/${encodeURIComponent(filename)}`,
      true
    ),

  evaluateRun: (
    name: string,
    payload: { n?: number; imgsz?: number; levels?: string; dataset?: string }
  ) =>
    request<{ job: JobSnapshot; report: string }>(
      `/suite/runs/${encodeURIComponent(name)}/evaluate`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }
    ),

  getEvalReport: (name: string) =>
    request<EvalReport>(`/suite/runs/${encodeURIComponent(name)}/eval`),

  // ---------------- Model Registry ----------------
  listModels: () =>
    request<{
      models: ModelSummary[];
      shipped?: ModelSummary[];
      active: any;
      runs: RunSummary[];
    }>("/suite/models"),

  promoteModel: (run: string, name?: string) =>
    request<{ active: any; models: ModelSummary[] }>("/suite/models/promote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run, ...(name ? { name } : {}) }),
    }),

  activateModel: (name: string) =>
    request<{ active: any }>(`/suite/models/${encodeURIComponent(name)}/activate`, {
      method: "POST",
    }),

  deleteModel: (name: string) =>
    request<{ ok: boolean; active: any }>(
      `/suite/models/${encodeURIComponent(name)}`,
      { method: "DELETE" }
    ),

  // ---------------- Background Jobs ----------------
  listJobs: (kind?: string, limit = 50) => {
    const params = new URLSearchParams();
    if (kind) params.set("kind", kind);
    params.set("limit", String(limit));
    return request<{ jobs: JobSnapshot[] }>(`/jobs?${params.toString()}`).then(
      (r) => r.jobs || []
    );
  },

  getJob: (jid: string, lines = 160) =>
    request<JobSnapshot>(`/jobs/${encodeURIComponent(jid)}?lines=${lines}`),

  cancelJob: (jid: string) =>
    request<JobSnapshot>(`/jobs/${encodeURIComponent(jid)}/cancel`, {
      method: "POST",
    }),

  pauseJob: (jid: string) =>
    request<JobSnapshot>(`/jobs/${encodeURIComponent(jid)}/pause`, {
      method: "POST",
    }),

  resumeJob: (jid: string) =>
    request<JobSnapshot>(`/jobs/${encodeURIComponent(jid)}/resume`, {
      method: "POST",
    }),

  deleteJob: (jid: string) =>
    request<{ ok: boolean; id: string }>(`/jobs/${encodeURIComponent(jid)}`, {
      method: "DELETE",
    }),

  // ---------------- S2C Transceiver ----------------
  s2cOptions: () =>
    request<{
      active: any;
      modulations: string[];
      eccs: string[];
      resolutions: string[];
      finder_shapes: FinderShapeOption[];
      finder_spans: FinderSpanOption[];
      transfers: S2CTransferInfo[];
      env: any;
    }>("/suite/s2c/options"),

  s2cPalette: (cells = 32, ecc = "rs32") =>
    request<{ modulations: PaletteEntry[]; cells: number; ecc: string; eccs: string[] }>(
      `/suite/palette?cells=${cells}&ecc=${encodeURIComponent(ecc)}`
    ),

  finderSvgUrl: (shape: string, size = 64) =>
    resolveApiUrl(`/suite/finder/${encodeURIComponent(shape)}.svg?size=${size}`, true),

  s2cTransmit: (payload: {
    text?: string;
    data_b64?: string;
    name?: string;
    modulation: string;
    ecc: string;
    resolution: string;
    cells?: number | null;
    symbol_px: number;
    repeat: number;
    fps: number;
    finder: string;
    finder_span?: string;
  }) =>
    request<{ transfer: S2CTransferInfo; result: S2CResultInfo }>(
      "/suite/s2c/transmit",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }
    ),

  s2cTransfers: () =>
    request<{ transfers: S2CTransferInfo[] }>("/suite/s2c/transfers").then(
      (r) => r.transfers || []
    ),

  s2cGet: (tid: string) =>
    request<{ transfer: S2CTransferInfo; result: S2CResultInfo; log: any[] }>(
      `/suite/s2c/${encodeURIComponent(tid)}`
    ),

  s2cFrameUrl: (tid: string, index = 0) =>
    resolveApiUrl(`/suite/s2c/${encodeURIComponent(tid)}/frame.png?index=${index}`, true),

  s2cPreviewUrl: (tid: string, index = 0) =>
    resolveApiUrl(`/suite/s2c/${encodeURIComponent(tid)}/preview.jpg?index=${index}`, true),

  s2cPayloadUrl: (tid: string) =>
    resolveApiUrl(`/suite/s2c/${encodeURIComponent(tid)}/payload`, true),

  s2cReceive: (tid: string, file: File | Blob, filename = "capture.jpg") => {
    const form = new FormData();
    form.append("file", file, filename);
    return request<{
      transfer: S2CTransferInfo;
      result: S2CResultInfo;
      decode: Record<string, any>;
    }>(`/suite/s2c/${encodeURIComponent(tid)}/receive`, {
      method: "POST",
      body: form,
    });
  },

  s2cReset: (tid: string) =>
    request<{ result: S2CResultInfo }>(
      `/suite/s2c/${encodeURIComponent(tid)}/reset`,
      { method: "POST" }
    ),

  s2cLoopback: (
    tid: string,
    payload: { level?: number; frames?: number; seed?: number }
  ) =>
    request<S2CLoopbackReport>(
      `/suite/s2c/${encodeURIComponent(tid)}/loopback`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }
    ),

  // ---------------- Optical Channel Lab ----------------
  labSimulate: (payload: {
    text: string;
    modulation: string;
    ecc: string;
    resolution: string;
    cells?: number | null;
    symbol_px: number;
    finder: string;
    finder_span?: string;
    level: number;
    seed: number;
    mode: string;
  }) =>
    request<LabSimulateResponse>("/suite/lab/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),

  labDecode: (
    file: File | Blob,
    params: {
      modulation: string;
      ecc: string;
      resolution: string;
      cells?: string;
      symbol_px: number;
      finder: string;
      mode: string;
    }
  ) => {
    const form = new FormData();
    form.append("file", file);
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null) form.append(k, String(v));
    });
    return request<LabDecodeResponse>("/suite/lab/decode", {
      method: "POST",
      body: form,
    });
  },
};
