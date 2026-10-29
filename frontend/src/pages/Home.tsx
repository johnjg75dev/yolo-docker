import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Activity,
  ArrowRight,
  Award,
  Camera,
  CheckCircle2,
  Compass,
  Cpu,
  Database,
  FlaskConical,
  Play,
  Radio,
  Server,
} from "lucide-react";
import { api } from "../api/client";
import { useToast } from "../components/Toast";
import { useEventStream } from "../hooks/useEventStream";
import { OpticalPipelineDiagram, ReadinessRing } from "../components/Visuals";
import type { ProgressResponse, RunSummary } from "../types";

function formatBytes(b?: number) {
  if (!b || b <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let val = b;
  let i = 0;
  while (val >= 1024 && i < units.length - 1) {
    val /= 1024;
    i++;
  }
  return `${val.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function Home() {
  const navigate = useNavigate();
  const { show } = useToast();
  const [progress, setProgress] = useState<ProgressResponse | null>(null);
  const [status, setStatus] = useState<any>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [runningAction, setRunningAction] = useState(false);

  const loadAll = useCallback(() => {
    api.progress().then(setProgress).catch(() => {});
    api.status().then(setStatus).catch(() => {});
    api
      .recentEvents(12)
      .then((r) => setEvents(r.events || []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    loadAll();
    const t = setInterval(loadAll, 6000);
    return () => clearInterval(t);
  }, [loadAll]);

  useEventStream(true, () => {
    loadAll();
  });

  const handleQuickNext = async () => {
    const nxt = progress?.next;
    if (!nxt?.action) {
      navigate("/wizard");
      return;
    }
    if (nxt.action.kind === "link" && nxt.action.href) {
      const target = nxt.action.href === "/dataset" ? "/datasets" : nxt.action.href;
      navigate(target);
      return;
    }
    if (nxt.action.kind === "post" && nxt.action.endpoint) {
      setRunningAction(true);
      try {
        const res = await api.post(nxt.action.endpoint, nxt.action.payload || {});
        show(`Started: ${res?.job?.title || nxt.title}`);
        navigate("/jobs");
      } catch (e: any) {
        show(e instanceof Error ? e.message : "Failed to start action", true);
      } finally {
        setRunningAction(false);
      }
    }
  };

  const counts = progress?.counts || {
    datasets: 0,
    images: 0,
    runs: 0,
    models: 0,
    projects: 0,
    transfers: 0,
    shots: 0,
    complete_transfers: 0,
    jobs_running: 0,
  };
  const runs: RunSummary[] = status?.runs || [];
  const activeModel = status?.active;
  const env = status?.env || {};
  const dbInfo = status?.state?.database || {};
  const busInfo = status?.state?.bus || {};

  return (
    <div className="wrap">
      <div className="page-header">
        <div>
          <div className="page-eyebrow">
            <Radio size={13} /> Optical Modem Mission Control
          </div>
          <h1>Screen-to-Camera Studio</h1>
          <p>
            End-to-end optical data modem: packetize &amp; Reed-Solomon/LDPC encode bytes into calibrated colour grids,
            localise frames with YOLO26-pose, and demodulate camera captures back to verified payloads.
          </p>
        </div>
        <div className="row">
          <Link to="/wizard" className="btn secondary">
            <Compass size={15} />
            Guided Setup
          </Link>
          <Link to="/s2c" className="btn">
            <Radio size={15} />
            Open S2C Transceiver
          </Link>
        </div>
      </div>

      {/* Pipeline & Readiness Hero */}
      <div className="card" style={{ padding: "20px 22px" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 24,
            flexWrap: "wrap",
          }}
        >
          <ReadinessRing
            percent={progress?.percent ?? 0}
            sub={progress ? `${progress.done}/${progress.total} stages` : "loading"}
            size={110}
          />
          <div style={{ flex: 1, minWidth: 300 }}>
            <OpticalPipelineDiagram
              stages={progress?.stages}
              onSelectNode={(href) => navigate(href)}
            />
          </div>
        </div>
      </div>

      {/* Recommended Next Action + Quick Launch */}
      <div className="grid-2" style={{ marginBottom: 16 }}>
        <div
          className="card"
          style={{
            marginBottom: 0,
            borderLeft: "3px solid var(--accent)",
            display: "flex",
            flexDirection: "column",
            justifyContent: "space-between",
          }}
        >
          <div>
            <div className="row spread" style={{ marginBottom: 6 }}>
              <span className="pill run">
                {progress?.complete ? "Pipeline Ready" : "Recommended Next Step"}
              </span>
              <Link to="/wizard" className="small">
                Full 6-step wizard →
              </Link>
            </div>
            <h2 style={{ marginBottom: 6 }}>
              {progress?.next ? progress.next.title : "Ready to Transmit Over Photons"}
            </h2>
            <p className="small">
              {progress?.next
                ? progress.next.why
                : "Every pipeline stage has been verified on this box. Launch the S2C Transceiver or experiment with channel impairments in the Lab."}
            </p>
          </div>
          <div className="row mt" style={{ gap: 10 }}>
            {progress?.next ? (
              <button onClick={handleQuickNext} disabled={runningAction}>
                <Play size={14} />
                {runningAction
                  ? "Starting…"
                  : progress.next.action?.label || progress.next.cta}
              </button>
            ) : (
              <Link to="/s2c" className="btn">
                <Radio size={14} />
                Transmit a Payload
              </Link>
            )}
            <Link to="/lab" className="btn secondary">
              <FlaskConical size={14} />
              Test in Channel Lab
            </Link>
          </div>
        </div>

        <div className="card" style={{ marginBottom: 0 }}>
          <div className="row spread" style={{ marginBottom: 10 }}>
            <h3>Active Detector &amp; Runtime</h3>
            <span className="pill ok">
              <CheckCircle2 size={12} /> {activeModel?.name || "yolo26n_optical"}
            </span>
          </div>
          <div className="grid-2" style={{ gap: 10 }}>
            <div className="card-subtle" style={{ padding: "10px 12px" }}>
              <div className="small">Pose Detector</div>
              <div className="mono" style={{ fontWeight: 600, color: "var(--fg)", marginTop: 2 }}>
                {activeModel?.name || "Bundled Baseline"}
              </div>
              <div className="small" style={{ marginTop: 2 }}>
                {activeModel?.metrics?.pose_map50_95
                  ? `Pose mAP ${(activeModel.metrics.pose_map50_95 * 100).toFixed(1)}%`
                  : activeModel?.shipped
                  ? "Shipped CPU checkpoint"
                  : "Promoted detector"}
              </div>
            </div>
            <div className="card-subtle" style={{ padding: "10px 12px" }}>
              <div className="small">Compute &amp; Storage</div>
              <div className="mono" style={{ fontWeight: 600, color: "var(--fg)", marginTop: 2 }}>
                {env.cpu_count || "—"} vCPU · {env.cuda ? "CUDA GPU" : "CPU"}
              </div>
              <div className="small" style={{ marginTop: 2 }}>
                DB: <code>{dbInfo.kind || "sqlite"}</code> · Bus: <code>{busInfo.kind || "memory"}</code>
              </div>
            </div>
          </div>
          <div className="row spread mt" style={{ fontSize: 12, color: "var(--muted)" }}>
            <span>
              Disk free: <strong className="mono">{formatBytes(status?.disk?.free_bytes)}</strong>
            </span>
            <span>
              OpenCV <code>{env.opencv || "—"}</code> · PyTorch <code>{env.torch || "—"}</code>
            </span>
          </div>
        </div>
      </div>

      {/* KPI Metrics Row */}
      <div className="grid-4" style={{ marginBottom: 16 }}>
        <Link to="/datasets" style={{ textDecoration: "none" }}>
          <div className="kpi-card card-interactive">
            <div className="kpi-label">
              <span>Labelled Frames</span>
              <Database size={14} color="#22d3ee" />
            </div>
            <div className="kpi-value">{counts.images}</div>
            <div className="kpi-sub">
              {counts.datasets} dataset(s) · {counts.shots} camera shots
            </div>
          </div>
        </Link>

        <Link to="/train" style={{ textDecoration: "none" }}>
          <div className="kpi-card card-interactive">
            <div className="kpi-label">
              <span>Training Runs</span>
              <Cpu size={14} color="#3b82f6" />
            </div>
            <div className="kpi-value">{counts.runs}</div>
            <div className="kpi-sub">
              {status?.best_box_map50_95 != null
                ? `Best box mAP ${status.best_box_map50_95.toFixed(3)}`
                : "Fine-tune YOLO26-pose"}
            </div>
          </div>
        </Link>

        <Link to="/s2c" style={{ textDecoration: "none" }}>
          <div className="kpi-card card-interactive">
            <div className="kpi-label">
              <span>S2C Transfers</span>
              <Radio size={14} color="#10b981" />
            </div>
            <div className="kpi-value">{counts.transfers}</div>
            <div className="kpi-sub">
              {counts.complete_transfers} SHA-1 verified receipts
            </div>
          </div>
        </Link>

        <Link to="/projects" style={{ textDecoration: "none" }}>
          <div className="kpi-card card-interactive">
            <div className="kpi-label">
              <span>Projects &amp; Jobs</span>
              <Server size={14} color="#8b5cf6" />
            </div>
            <div className="kpi-value">{counts.projects}</div>
            <div className="kpi-sub">
              {counts.jobs_running > 0
                ? `${counts.jobs_running} job(s) running now`
                : "Queue idle & ready"}
            </div>
          </div>
        </Link>
      </div>

      {/* 6-Stage Workflow Cards */}
      {progress?.stages && progress.stages.length > 0 && (
        <div className="card">
          <div className="card-header">
            <div>
              <h2>Pipeline Walkthrough</h2>
              <p className="small" style={{ margin: 0 }}>
                Derived directly from artefacts on disk &amp; database — click any stage to inspect or act.
              </p>
            </div>
            <Link to="/wizard" className="btn ghost sm">
              Interactive Wizard <ArrowRight size={14} />
            </Link>
          </div>
          <div className="grid-3">
            {progress.stages.map((s, idx) => {
              const targetHref =
                s.href === "/dataset"
                  ? "/datasets"
                  : s.href.startsWith("/guide")
                  ? "/guide"
                  : s.href;
              return (
                <Link key={s.id} to={targetHref} style={{ textDecoration: "none" }}>
                  <div
                    className="card-subtle card-interactive"
                    style={{
                      height: "100%",
                      borderColor: s.current
                        ? "var(--accent)"
                        : s.done
                        ? "rgba(16, 185, 129, 0.35)"
                        : "var(--border)",
                    }}
                  >
                    <div className="row spread" style={{ marginBottom: 6 }}>
                      <span
                        className={`pill ${s.done ? "ok" : s.current ? "run" : ""}`}
                      >
                        {s.done ? "✓ Complete" : s.current ? `Step ${idx + 1} · Current` : `Step ${idx + 1}`}
                      </span>
                      <ArrowRight size={14} color="var(--muted)" />
                    </div>
                    <h3 style={{ marginBottom: 4 }}>{s.title}</h3>
                    <p className="small" style={{ margin: 0 }}>
                      {s.detail}
                    </p>
                  </div>
                </Link>
              );
            })}
          </div>
        </div>
      )}

      {/* Quick Studio Workbenches */}
      <div className="grid-3" style={{ marginBottom: 16 }}>
        <Link to="/lab" style={{ textDecoration: "none" }}>
          <div className="card card-interactive" style={{ marginBottom: 0, height: "100%" }}>
            <div className="row" style={{ gap: 8, marginBottom: 6 }}>
              <FlaskConical size={18} color="#22d3ee" />
              <h3>Optical Channel Lab</h3>
            </div>
            <p className="small" style={{ margin: 0 }}>
              Simulate handheld tilt, blur &amp; glare on any modulation/finder pair and inspect pose corners + rectified lattice.
            </p>
          </div>
        </Link>
        <Link to="/capture" style={{ textDecoration: "none" }}>
          <div className="card card-interactive" style={{ marginBottom: 0, height: "100%" }}>
            <div className="row" style={{ gap: 8, marginBottom: 6 }}>
              <Camera size={18} color="#f59e0b" />
              <h3>Paired Capture Station</h3>
            </div>
            <p className="small" style={{ margin: 0 }}>
              Pair laptop display + phone camera via QR code to shoot real screen optics and auto-label 4-corner poses.
            </p>
          </div>
        </Link>
        <Link to="/evaluate" style={{ textDecoration: "none" }}>
          <div className="card card-interactive" style={{ marginBottom: 0, height: "100%" }}>
            <div className="row" style={{ gap: 8, marginBottom: 6 }}>
              <Award size={18} color="#10b981" />
              <h3>Benchmark &amp; Registry</h3>
            </div>
            <p className="small" style={{ margin: 0 }}>
              Compare Classical vs YOLO26 vs Hybrid Auto decode rates across noise levels and promote winning checkpoints.
            </p>
          </div>
        </Link>
      </div>

      {/* Bottom Row: Recent Runs & Live Event Stream */}
      <div className="grid-2">
        <div className="card" style={{ marginBottom: 0 }}>
          <div className="card-header">
            <h2>Recent Training Runs</h2>
            <Link to="/train" className="small">
              New run →
            </Link>
          </div>
          {runs.length === 0 ? (
            <div className="empty-state" style={{ padding: "24px 14px" }}>
              <p className="small" style={{ marginBottom: 10 }}>
                No custom runs yet — bundled <code>yolo26n_optical</code> is active.
              </p>
              <Link to="/models" className="btn secondary sm">
                Inspect Shipped Baseline
              </Link>
            </div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Run</th>
                    <th>Status</th>
                    <th className="num">Epochs</th>
                    <th className="num">mAP50-95</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.slice(0, 6).map((r) => (
                    <tr key={r.name}>
                      <td>
                        <Link to="/models" className="mono">
                          {r.name}
                        </Link>
                      </td>
                      <td>
                        <span
                          className={`pill ${
                            r.has_weights ? "ok" : r.status === "running" ? "run" : "warn"
                          }`}
                        >
                          {r.has_weights ? "weights ✓" : r.status || "pending"}
                        </span>
                      </td>
                      <td className="num">
                        {r.epoch ?? "—"}/{r.epochs ?? "—"}
                      </td>
                      <td className="num">
                        {typeof r.mAP === "number" ? r.mAP.toFixed(3) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="card" style={{ marginBottom: 0 }}>
          <div className="card-header">
            <div className="row" style={{ gap: 8 }}>
              <Activity size={16} color="#22d3ee" />
              <h2>Recent Suite Events</h2>
            </div>
            <Link to="/jobs" className="small">
              Job console →
            </Link>
          </div>
          {events.length === 0 ? (
            <div className="empty-state" style={{ padding: "24px 14px" }}>
              <p className="small" style={{ margin: 0 }}>
                No events recorded in this session yet. Actions across datasets, training, models, and S2C appear here live.
              </p>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 240, overflowY: "auto" }}>
              {events.slice(0, 10).map((ev, i) => (
                <div
                  key={i}
                  className="card-subtle"
                  style={{
                    padding: "8px 12px",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 10,
                  }}
                >
                  <div className="row" style={{ gap: 8 }}>
                    <span className="pill run" style={{ fontSize: 11 }}>
                      {ev.kind || "event"}
                    </span>
                    <span className="small" style={{ color: "var(--fg-soft)" }}>
                      {ev.job?.title ||
                        ev.project?.slug ||
                        ev.transfer?.id ||
                        ev.model?.name ||
                        ev.subject ||
                        ""}
                    </span>
                  </div>
                  {ev.ts && (
                    <span className="small mono" style={{ fontSize: 11 }}>
                      {new Date(ev.ts * 1000).toLocaleTimeString()}
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
