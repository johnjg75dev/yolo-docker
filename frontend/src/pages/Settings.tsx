import { useEffect, useState } from "react";
import {
  CheckCircle2,
  Download,
  HardDrive,
  RefreshCw,
  Settings as SettingsIcon,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import { applyTheme, readTheme, THEMES, type Theme } from "../theme";
import { api } from "../api/client";
import { useToast } from "../components/Toast";
import type { DiagnoseResponse } from "../types";

const PREFS_KEY = "om_studio_prefs_v1";

interface OperatorPrefs {
  defaultFps: number;
  defaultDetector: "auto" | "classical" | "yolo";
  defaultModulation: string;
  cameraFacing: "environment" | "user";
}

function formatBytes(b?: number) {
  if (!b || b <= 0) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let v = b;
  let i = 0;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(1)} ${u[i]}`;
}

export function Settings() {
  const { show } = useToast();
  const [theme, setTheme] = useState<Theme>(readTheme);
  const [diag, setDiag] = useState<DiagnoseResponse | null>(null);
  const [status, setStatus] = useState<any>(null);
  const [prefs, setPrefs] = useState<OperatorPrefs>(() => {
    try {
      const raw = localStorage.getItem(PREFS_KEY);
      if (raw) return JSON.parse(raw);
    } catch {
      // ignore
    }
    return {
      defaultFps: 3,
      defaultDetector: "auto",
      defaultModulation: "4color",
      cameraFacing: "environment",
    };
  });

  const refresh = () => {
    api.diagnose().then(setDiag).catch(() => {});
    api.status().then(setStatus).catch(() => {});
  };

  useEffect(() => {
    refresh();
  }, []);

  const savePrefs = (e: React.FormEvent) => {
    e.preventDefault();
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
    show("Saved operator preferences to browser");
  };

  const exportDiagnosticBundle = () => {
    const bundle = {
      exported_at: new Date().toISOString(),
      diagnose: diag,
      status,
      prefs,
    };
    const blob = new Blob([JSON.stringify(bundle, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `optical-modem-diagnostics-${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const disk = status?.disk || {};
  const diskPct =
    disk.total_bytes > 0
      ? Math.round(((disk.total_bytes - disk.free_bytes) / disk.total_bytes) * 100)
      : 0;

  return (
    <div className="wrap">
      <div className="page-header">
        <div>
          <div className="page-eyebrow">
            <SettingsIcon size={13} /> Deployment Health &amp; Preferences
          </div>
          <h1>System Diagnostics &amp; Settings</h1>
          <p>
            Inspect reverse-proxy HTTPS headers, session cookie policy, SQLite/Postgres persistence, disk headroom, and browser preferences.
          </p>
        </div>
        <div className="row">
          <button type="button" className="secondary sm" onClick={refresh}>
            <RefreshCw size={14} /> Re-Run Checks
          </button>
          <button type="button" className="sm" onClick={exportDiagnosticBundle}>
            <Download size={14} /> Export JSON Report
          </button>
        </div>
      </div>

      <section className="card" aria-labelledby="appearance-title">
        <div className="card-header"><div><h2 id="appearance-title">Appearance</h2><p className="small">Colors and fonts apply immediately and are saved in this browser.</p></div></div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(165px, 1fr))", gap: 8 }}>
          {THEMES.map(t => <button type="button" key={t.id} className={theme === t.id ? "" : "secondary"} aria-pressed={theme === t.id} onClick={() => { setTheme(t.id); applyTheme(t.id); }} style={{ display: "block", textAlign: "left" }}><strong>{t.label}</strong><br /><span style={{ fontSize: 11 }}>{t.description}</span></button>)}
        </div>
      </section>

      <div className="grid-2" style={{ alignItems: "start" }}>
        {/* Proxy, Cookie & Runtime Health */}
        <div className="card" style={{ marginBottom: 0 }}>
          <div className="card-header">
            <div className="row" style={{ gap: 8 }}>
              {diag?.ok ? (
                <ShieldCheck size={18} color="#34d399" />
              ) : (
                <ShieldAlert size={18} color="#fbbf24" />
              )}
              <h2>Proxy, TLS &amp; Cookie Checks</h2>
            </div>
            <span className={`pill ${diag?.ok ? "ok" : "warn"}`}>
              {diag?.verdict || "Checking…"}
            </span>
          </div>

          <div className="grid-3" style={{ marginBottom: 14 }}>
            <div className="card-subtle" style={{ padding: 10 }}>
              <div className="small">Scheme</div>
              <div className="mono">{diag?.scheme || "—"}</div>
            </div>
            <div className="card-subtle" style={{ padding: 10 }}>
              <div className="small">Database</div>
              <div className="mono">{diag?.database?.kind || "sqlite"}</div>
            </div>
            <div className="card-subtle" style={{ padding: 10 }}>
              <div className="small">Event Bus</div>
              <div className="mono">{diag?.bus?.kind || "memory"}</div>
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {(diag?.checks || []).map((c) => (
              <div
                key={c.id}
                className="card-subtle"
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 10,
                  padding: "9px 12px",
                }}
              >
                <CheckCircle2
                  size={15}
                  color={c.ok ? "#10b981" : "#f59e0b"}
                  style={{ marginTop: 2, flexShrink: 0 }}
                />
                <div style={{ fontSize: 12.5 }}>
                  <strong className="mono" style={{ color: "var(--fg)" }}>
                    {c.id}
                  </strong>
                  <div className="small" style={{ marginTop: 2 }}>
                    {c.detail}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Storage, Runtime & Operator Preferences */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="card" style={{ marginBottom: 0 }}>
            <div className="card-header">
              <div className="row" style={{ gap: 8 }}>
                <HardDrive size={17} color="#22d3ee" />
                <h2>Storage &amp; ML Runtime</h2>
              </div>
              <span className="mono small">{diskPct}% used</span>
            </div>

            <div className="progress-track" style={{ marginBottom: 10 }}>
              <div className="progress-fill" style={{ width: `${diskPct}%` }} />
            </div>

            <div className="row spread small">
              <span>
                DATA_DIR: <code>{status?.data_dir || "/data"}</code>
              </span>
              <span>
                Free: <code>{formatBytes(disk.free_bytes)}</code> /{" "}
                <code>{formatBytes(disk.total_bytes)}</code>
              </span>
            </div>

            <div className="grid-2 mt" style={{ gap: 10 }}>
              <div className="card-subtle" style={{ padding: 10 }}>
                <div className="small">Python &amp; OpenCV</div>
                <div className="mono" style={{ fontSize: 12 }}>
                  Py {status?.env?.python?.split(" ")[0] || "3.11"} · cv2{" "}
                  {status?.env?.opencv || "—"}
                </div>
              </div>
              <div className="card-subtle" style={{ padding: 10 }}>
                <div className="small">PyTorch &amp; Ultralytics</div>
                <div className="mono" style={{ fontSize: 12 }}>
                  torch {status?.env?.torch || "—"} · ultra{" "}
                  {status?.env?.ultralytics || "optional"}
                </div>
              </div>
            </div>
          </div>

          <form onSubmit={savePrefs} className="card" style={{ marginBottom: 0 }}>
            <div className="card-header">
              <h2>Local Operator Preferences</h2>
            </div>

            <div className="grid-2">
              <label className="field">
                <span>Default S2C Carousel FPS</span>
                <input
                  type="number"
                  min={1}
                  max={15}
                  value={prefs.defaultFps}
                  onChange={(e) =>
                    setPrefs({ ...prefs, defaultFps: Number(e.target.value) || 3 })
                  }
                />
              </label>

              <label className="field">
                <span>Preferred Pose Detector</span>
                <select
                  value={prefs.defaultDetector}
                  onChange={(e) =>
                    setPrefs({
                      ...prefs,
                      defaultDetector: e.target.value as any,
                    })
                  }
                >
                  <option value="auto">Hybrid Auto</option>
                  <option value="classical">Classical Contour</option>
                  <option value="yolo">YOLO26-Pose</option>
                </select>
              </label>
            </div>

            <div className="grid-2 mt">
              <label className="field">
                <span>Preferred Camera Lens</span>
                <select
                  value={prefs.cameraFacing}
                  onChange={(e) =>
                    setPrefs({
                      ...prefs,
                      cameraFacing: e.target.value as any,
                    })
                  }
                >
                  <option value="environment">Rear / Environment Lens</option>
                  <option value="user">Front / Webcam Lens</option>
                </select>
              </label>

              <label className="field">
                <span>Default Constellation</span>
                <input
                  value={prefs.defaultModulation}
                  onChange={(e) =>
                    setPrefs({ ...prefs, defaultModulation: e.target.value })
                  }
                />
              </label>
            </div>

            <div className="row mt" style={{ justifyContent: "flex-end" }}>
              <button type="submit">Save Preferences</button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
