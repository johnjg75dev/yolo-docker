import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import {
  Activity,
  Award,
  BarChart3,
  BookOpen,
  Camera,
  Compass,
  Cpu,
  Database,
  FlaskConical,
  FolderKanban,
  LayoutDashboard,
  LogOut,
  Menu,
  Radio,
  Settings as SettingsIcon,
  Sliders,
  X,
} from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../hooks/useAuth";
import { useEventStream } from "../hooks/useEventStream";
import type { ProgressResponse } from "../types";

export function Layout({ children }: { children: ReactNode }) {
  const { authed, logout } = useAuth();
  const loc = useLocation();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [progress, setProgress] = useState<ProgressResponse | null>(null);
  const [activeModel, setActiveModel] = useState<string>("yolo26n_optical");

  const refreshSummary = useCallback(() => {
    if (!authed) return;
    api
      .progress()
      .then((p) => setProgress(p))
      .catch(() => {});
    api
      .status()
      .then((s) => {
        if (s?.active?.name) setActiveModel(s.active.name);
      })
      .catch(() => {});
  }, [authed]);

  useEffect(() => {
    refreshSummary();
    if (!authed) return;
    const t = setInterval(refreshSummary, 8000);
    return () => clearInterval(t);
  }, [authed, refreshSummary]);

  const { connected } = useEventStream(authed, (ev) => {
    if (
      ev?.kind?.startsWith("job.") ||
      ev?.kind?.startsWith("dataset.") ||
      ev?.kind?.startsWith("model.") ||
      ev?.kind?.startsWith("project.") ||
      ev?.kind?.startsWith("s2c.")
    ) {
      refreshSummary();
    }
  });

  useEffect(() => {
    setDrawerOpen(false);
  }, [loc.pathname]);

  if (loc.pathname === "/login") {
    return <>{children}</>;
  }

  const stageDone = (id: string) =>
    Boolean(progress?.stages?.find((s) => s.id === id)?.done);
  const runningJobs = progress?.counts?.jobs_running || 0;

  const isActive = (paths: string[]) =>
    paths.some((p) =>
      p === "/" ? loc.pathname === "/" : loc.pathname === p || loc.pathname.startsWith(p + "/")
    );

  return (
    <div className="app-shell">
      <div
        className={`sidebar-backdrop ${drawerOpen ? "open" : ""}`}
        onClick={() => setDrawerOpen(false)}
      />

      <aside className={`sidebar ${drawerOpen ? "open" : ""}`}>
        <div className="row spread" style={{ paddingRight: 8 }}>
          <Link to="/" className="sidebar-brand" style={{ borderBottom: "none", flex: 1 }}>
            <div className="brand-mark">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                <rect x="3" y="3" width="7" height="7" rx="1.5" fill="#22d3ee" />
                <rect x="14" y="3" width="7" height="7" rx="1.5" fill="#10b981" />
                <rect x="3" y="14" width="7" height="7" rx="1.5" fill="#3b82f6" />
                <rect x="14" y="14" width="7" height="7" rx="1.5" stroke="#22d3ee" strokeWidth="2" />
              </svg>
            </div>
            <div>
              <div className="brand-title">Optical Modem</div>
              <div className="brand-sub">Screen2Camera Studio</div>
            </div>
          </Link>
          <button
            type="button"
            className="mobile-menu-btn"
            style={{ border: "none" }}
            onClick={() => setDrawerOpen(false)}
            aria-label="Close navigation"
          >
            <X size={18} />
          </button>
        </div>
        <div style={{ height: 1, background: "var(--border)" }} />

        <div className="sidebar-section">
          <div className="sidebar-section-label">Overview</div>
          <Link to="/" className={`nav-item ${isActive(["/"]) ? "active" : ""}`}>
            <LayoutDashboard size={16} />
            <span>Mission Control</span>
          </Link>
          <Link to="/wizard" className={`nav-item ${isActive(["/wizard"]) ? "active" : ""}`}>
            <Compass size={16} />
            <span>Guided Setup</span>
            {progress && (
              <span className="nav-badge">
                {progress.done}/{progress.total}
              </span>
            )}
          </Link>
          <Link to="/guide" className={`nav-item ${isActive(["/guide"]) ? "active" : ""}`}>
            <BookOpen size={16} />
            <span>Protocol &amp; Guide</span>
          </Link>
        </div>

        <div className="sidebar-section">
          <div className="sidebar-section-label">Optical Modem</div>
          <Link to="/s2c" className={`nav-item ${isActive(["/s2c"]) ? "active" : ""}`}>
            <Radio size={16} />
            <span>S2C Transceiver</span>
            {stageDone("send") && <span className="nav-check">✓</span>}
          </Link>
          <Link to="/lab" className={`nav-item ${isActive(["/lab"]) ? "active" : ""}`}>
            <FlaskConical size={16} />
            <span>Channel Lab</span>
          </Link>
          <Link to="/capture" className={`nav-item ${isActive(["/capture"]) ? "active" : ""}`}>
            <Camera size={16} />
            <span>Capture Station</span>
          </Link>
        </div>

        <div className="sidebar-section">
          <div className="sidebar-section-label">ML Vision Pipeline</div>
          <Link to="/projects" className={`nav-item ${isActive(["/projects"]) ? "active" : ""}`}>
            <FolderKanban size={16} />
            <span>1. Projects</span>
            {stageDone("project") ? (
              <span className="nav-check">✓</span>
            ) : (
              progress?.counts?.projects ? (
                <span className="nav-badge">{progress.counts.projects}</span>
              ) : null
            )}
          </Link>
          <Link
            to="/datasets"
            className={`nav-item ${isActive(["/datasets", "/dataset"]) ? "active" : ""}`}
          >
            <Database size={16} />
            <span>2. Datasets</span>
            {stageDone("dataset") && <span className="nav-check">✓</span>}
          </Link>
          <Link to="/train" className={`nav-item ${isActive(["/train"]) ? "active" : ""}`}>
            <Cpu size={16} />
            <span>3. Train Detector</span>
            {stageDone("train") && <span className="nav-check">✓</span>}
          </Link>
          <Link to="/evaluate" className={`nav-item ${isActive(["/evaluate"]) ? "active" : ""}`}>
            <BarChart3 size={16} />
            <span>4. Evaluate</span>
          </Link>
          <Link to="/models" className={`nav-item ${isActive(["/models"]) ? "active" : ""}`}>
            <Award size={16} />
            <span>5. Model Registry</span>
            {stageDone("verify") && <span className="nav-check">✓</span>}
          </Link>
        </div>

        <div className="sidebar-section">
          <div className="sidebar-section-label">Operations</div>
          <Link to="/jobs" className={`nav-item ${isActive(["/jobs"]) ? "active" : ""}`}>
            <Activity size={16} />
            <span>Jobs &amp; Queue</span>
            {runningJobs > 0 && <span className="nav-live">{runningJobs} live</span>}
          </Link>
          <Link to="/settings" className={`nav-item ${isActive(["/settings"]) ? "active" : ""}`}>
            <SettingsIcon size={16} />
            <span>Diagnostics &amp; Prefs</span>
          </Link>
        </div>

        <div className="sidebar-footer">
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              fontSize: 11,
              color: "var(--muted)",
            }}
          >
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <span
                className="status-dot"
                style={{ color: connected ? "var(--ok)" : "#64748b" }}
              />
              {connected ? "SSE Live Stream" : "Polling Active"}
            </span>
            <Link to="/models" className="mono" style={{ fontSize: 11, color: "var(--accent)" }}>
              {activeModel}
            </Link>
          </div>
          {authed ? (
            <button
              type="button"
              className="ghost sm"
              style={{ width: "100%", justifyContent: "center" }}
              onClick={logout}
            >
              <LogOut size={14} />
              Sign out
            </button>
          ) : (
            <Link to="/login" className="btn sm" style={{ width: "100%" }}>
              Sign in
            </Link>
          )}
        </div>
      </aside>

      <div className="main-area">
        <header className="topbar">
          <button
            type="button"
            className="mobile-menu-btn"
            onClick={() => setDrawerOpen(true)}
            aria-label="Open navigation"
          >
            <Menu size={18} />
          </button>

          {progress && (
            <Link
              to="/wizard"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 10,
                padding: "5px 11px",
                borderRadius: 999,
                background: "var(--card)",
                border: "1px solid var(--border)",
                textDecoration: "none",
              }}
            >
              <span
                className={`pill ${progress.complete ? "ok" : "run"}`}
                style={{ padding: "1px 7px", fontSize: 11 }}
              >
                {progress.percent}%
              </span>
              <span className="small" style={{ color: "var(--fg-soft)" }}>
                {progress.next
                  ? `Next: ${progress.next.title}`
                  : "All 6 pipeline stages verified"}
              </span>
            </Link>
          )}

          <span style={{ flex: 1 }} />

          <div className="row" style={{ gap: 8 }}>
            <Link to="/lab" className="btn ghost sm">
              <Sliders size={14} />
              Channel Lab
            </Link>
            <Link to="/s2c" className="btn sm">
              <Radio size={14} />
              Transmit S2C
            </Link>
          </div>
        </header>

        <main className="page-content">{children}</main>
      </div>
    </div>
  );
}
