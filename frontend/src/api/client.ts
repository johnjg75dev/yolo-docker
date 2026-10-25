import type {
  LoginResponse,
  S2CTransfer,
  S2CResult,
  Run,
  StartTrainPayload,
  Project,
  CaptureStatus,
} from "../types";

const BASE = (import.meta as any).env?.VITE_API_BASE || (window as any).__API_BASE__ || "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => fetch("/healthz", { credentials: "include" }).then(async (r) => {
    if (!r.ok) throw new Error((await r.text()) || `HTTP ${r.status}`);
    return r.json();
  }),

  login: async (password: string): Promise<LoginResponse> => {
    return request<LoginResponse>("/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
  },

  logout: () => request<{ ok: boolean }>("/logout", { method: "POST" }),

  authStatus: async () => {
    try {
      await request<{ authenticated: boolean }>("/me");
      return { authed: true };
    } catch {
      return { authed: false };
    }
  },

  s2cOptions: () =>
    request<{
      modulations: string[];
      eccs: string[];
      resolutions: string[];
      finder_shapes: { name: string; px: number; margin: number }[];
    }>("/suite/s2c/options"),

  s2cTransmit: (payload: {
    text: string;
    modulation: string;
    ecc: string;
    resolution: string;
    symbol_px: string;
    repeat: string;
    fps: string;
    finder: string;
  }) => {
    const form = new FormData();
    Object.entries(payload).forEach(([k, v]) => form.append(k, v));
    return request<{ transfer: S2CTransfer; result: S2CResult }>(
      "/suite/s2c/transmit",
      { method: "POST", body: form }
    );
  },

  s2cReceive: (tid: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{
      transfer: S2CTransfer;
      result: S2CResult;
      decode: S2CResult;
    }>(`/suite/s2c/${encodeURIComponent(tid)}/receive`, {
      method: "POST",
      body: form,
    });
  },

  s2cDownload: (tid: string) => `${BASE}/suite/s2c/${encodeURIComponent(tid)}/payload`,

  listDatasets: () => request<{ datasets: any[] }>("/suite/datasets").then((r) => r.datasets || []),
  deleteDataset: (name: string) =>
    request<{ ok: boolean }>(`/suite/datasets/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),

  listRuns: () => request<{ runs: any[]; active: any }>("/suite/runs").then((r) => r.runs),
  getRun: (id: string) => request<Run>(`/suite/runs/${encodeURIComponent(id)}`),
  startTrain: (payload: StartTrainPayload) =>
    request<{ job: any; run: string }>("/suite/train", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  trainDefaults: () => request<any>("/suite/train/defaults"),

  listProjects: () => request<{ projects: any[] }>("/suite/projects").then((r) => r.projects || []),
  createProject: (name: string) =>
    request<Project>("/suite/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),

  captureStatus: () => request<CaptureStatus>("/capture/status"),
  listJobs: (kind?: string, limit = 50) => {
    const params = new URLSearchParams();
    if (kind) params.set("kind", kind);
    params.set("limit", String(limit));
    return request<{ jobs: any[] }>(`/jobs?${params.toString()}`).then((r) => r.jobs || []);
  },
  getJob: (jid: string) => request<any>(`/jobs/${encodeURIComponent(jid)}`),
  cancelJob: (jid: string) => request<any>(`/jobs/${encodeURIComponent(jid)}/cancel`, { method: "POST" }),
  captureStart: (port?: number) =>
    request<CaptureStatus>("/capture/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ port }),
    }),
  captureStop: () =>
    request<CaptureStatus>("/capture/stop", { method: "POST" }),
};