import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Circle,
  Compass,
  ExternalLink,
  Play,
  Sparkles,
} from "lucide-react";
import { api } from "../api/client";
import { useToast } from "../components/Toast";
import { useEventStream } from "../hooks/useEventStream";
import { ReadinessRing, StageIllustration } from "../components/Visuals";
import type { JobSnapshot, ProgressAction, ProgressResponse, RunSummary } from "../types";

export function Wizard() {
  const navigate = useNavigate();
  const { show } = useToast();
  const [progress, setProgress] = useState<ProgressResponse | null>(null);
  const [idx, setIdx] = useState(0);
  const [userPickedStep, setUserPickedStep] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobSnap, setJobSnap] = useState<JobSnapshot | null>(null);
  const [running, setRunning] = useState(false);

  // Inline quick helpers for Project & Promote stages
  const [quickProjectName, setQuickProjectName] = useState("v0.3-optical-hd");
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const timer = useRef<any>(null);

  const load = useCallback(async () => {
    try {
      const p = await api.progress();
      setProgress(p);
      if (!userPickedStep) {
        const stages = p?.stages || [];
        const firstOpen = stages.findIndex((s) => !s.done);
        if (firstOpen >= 0) setIdx(firstOpen);
        else if (stages.length) setIdx(stages.length - 1);
      }
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Failed to load guided progress", true);
    }
  }, [show, userPickedStep]);

  useEffect(() => {
    load();
    api.listRuns().then(setRuns).catch(() => {});
    timer.current = setInterval(load, 6000);
    return () => clearInterval(timer.current);
  }, [load]);

  useEventStream(true, (ev) => {
    if (
      ev?.kind === "job.finished" ||
      ev?.kind === "dataset.upserted" ||
      ev?.kind === "dataset.ready" ||
      ev?.kind === "project.created" ||
      ev?.kind === "model.promoted" ||
      ev?.kind === "s2c.complete"
    ) {
      setUserPickedStep(false);
      load();
      api.listRuns().then(setRuns).catch(() => {});
    }
  });

  // Keyboard left/right step navigation
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement
      ) {
        return;
      }
      const total = progress?.stages?.length || 0;
      if (!total) return;
      if (e.key === "ArrowLeft") {
        setUserPickedStep(true);
        setIdx((v) => Math.max(0, v - 1));
      } else if (e.key === "ArrowRight") {
        setUserPickedStep(true);
        setIdx((v) => Math.min(total - 1, v + 1));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [progress]);

  useEffect(() => {
    if (!jobId) {
      setJobSnap(null);
      return;
    }
    const poll = async () => {
      try {
        const snap = await api.getJob(jobId, 80);
        setJobSnap(snap);
        if (!snap.live) {
          setRunning(false);
          setUserPickedStep(false);
          load();
        }
      } catch {
        // ignore transient poll error
      }
    };
    poll();
    const t = setInterval(poll, 1500);
    return () => clearInterval(t);
  }, [jobId, load]);

  const normalizeHref = (href?: string) => {
    if (!href) return "/";
    if (href === "/dataset") return "/datasets";
    if (href.startsWith("/guide")) return "/guide";
    return href;
  };

  const runAction = async (action?: ProgressAction | null) => {
    if (!action) return;
    if (action.kind === "link") {
      navigate(normalizeHref(action.href));
      return;
    }
    if (action.kind === "post" && action.endpoint) {
      setRunning(true);
      try {
        const res = await api.post(action.endpoint, action.payload || {});
        const jid = res?.job?.id || res?.id;
        if (jid) {
          setJobId(jid);
          show(`Job started: ${res?.job?.title || jid}`);
        } else {
          setRunning(false);
          setUserPickedStep(false);
          show("Stage completed");
          load();
        }
      } catch (e: any) {
        setRunning(false);
        show(e instanceof Error ? e.message : "Action failed", true);
      }
    }
  };

  const handleInlineCreateProject = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!quickProjectName.trim()) return;
    setRunning(true);
    try {
      await api.createProject({
        slug: quickProjectName.trim(),
        title: quickProjectName.trim(),
        description: "Created from Guided Setup",
        status: "development",
        tags: ["optical", "s2c"],
      });
      show(`Project "${quickProjectName.trim()}" created`);
      setUserPickedStep(false);
      await load();
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Could not create project", true);
    } finally {
      setRunning(false);
    }
  };

  const handleQuickPromote = async (runName: string) => {
    setRunning(true);
    try {
      await api.promoteModel(runName);
      show(`Promoted ${runName} to active detector`);
      setUserPickedStep(false);
      await load();
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Promotion failed", true);
    } finally {
      setRunning(false);
    }
  };

  const stages = progress?.stages || [];
  const current = stages[idx];
  const pct = progress?.percent ?? 0;
  const promotableRuns = runs.filter((r) => r.has_weights);

  return (
    <div className="wrap">
      <div className="page-header">
        <div>
          <div className="page-eyebrow">
            <Compass size={13} /> Onboarding &amp; Pipeline Verification
          </div>
          <h1>Guided Setup</h1>
          <p>
            Six deterministic stages from hardware verification to a SHA-1 verified optical screen-to-camera transfer.
            Use <kbd>←</kbd> <kbd>→</kbd> to browse stages.
          </p>
        </div>
        {progress && (
          <div className="row" style={{ gap: 14 }}>
            <ReadinessRing
              percent={pct}
              sub={`${progress.done}/${progress.total} done`}
              size={68}
            />
          </div>
        )}
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(250px, 300px) minmax(0, 1fr)",
          gap: 18,
          alignItems: "start",
        }}
        className="wizard-layout"
      >
        {/* Left Stage Rail */}
        <div className="card" style={{ padding: 12, marginBottom: 0 }}>
          <div
            style={{
              padding: "4px 8px 10px",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <h4>6 Pipeline Stages</h4>
            <span className="mono small">{pct}%</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {stages.map((s, i) => {
              const selected = i === idx;
              return (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => {
                    setUserPickedStep(true);
                    setIdx(i);
                  }}
                  style={{
                    background: selected
                      ? "rgba(34, 211, 238, 0.12)"
                      : "var(--inset)",
                    border: selected
                      ? "1px solid var(--accent)"
                      : s.done
                      ? "1px solid rgba(16, 185, 129, 0.3)"
                      : "1px solid var(--border)",
                    borderRadius: 10,
                    padding: "10px 12px",
                    textAlign: "left",
                    display: "flex",
                    alignItems: "flex-start",
                    gap: 10,
                    whiteSpace: "normal",
                    color: "var(--fg)",
                  }}
                >
                  <span
                    className={`pill ${s.done ? "ok" : s.current ? "run" : ""}`}
                    style={{ minWidth: 26, justifyContent: "center", padding: "2px 6px" }}
                  >
                    {s.done ? "✓" : i + 1}
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 650, fontSize: "0.87rem" }}>{s.title}</div>
                    <div
                      className="small"
                      style={{
                        fontSize: "0.75rem",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {s.detail}
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* Right Active Step Card */}
        <div>
          {current ? (
            <div className="card" style={{ marginBottom: 0 }}>
              <div className="row spread" style={{ alignItems: "flex-start", marginBottom: 14 }}>
                <div>
                  <span className="small mono">
                    STEP {idx + 1} OF {stages.length}
                  </span>
                  <h2 style={{ fontSize: "1.3rem", marginTop: 2 }}>{current.title}</h2>
                </div>
                <span
                  className={`pill ${
                    current.done ? "ok" : current.current ? "run" : "warn"
                  }`}
                >
                  {current.done
                    ? "Verified ✓"
                    : current.current
                    ? "Current Focus"
                    : "Pending Earlier Stage"}
                </span>
              </div>

              <div className="card-subtle" style={{ marginBottom: 16, padding: "12px 16px" }}>
                <StageIllustration stageId={current.id} />
              </div>

              <div className="callout" style={{ marginBottom: 16 }}>
                <strong style={{ color: "#fff" }}>Why this stage matters: </strong>
                {current.why}
              </div>

              <div className="row spread" style={{ marginBottom: 8 }}>
                <h4>Verification Checklist</h4>
                <span className="small mono">{current.detail}</span>
              </div>

              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 8,
                  marginBottom: 18,
                }}
              >
                {(current.checklist || []).map((c, i) => (
                  <div
                    key={i}
                    className="card-subtle"
                    style={{
                      padding: "9px 12px",
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                      borderColor: c.done ? "rgba(16, 185, 129, 0.35)" : "var(--border)",
                    }}
                  >
                    {c.done ? (
                      <CheckCircle2 size={16} color="#10b981" />
                    ) : (
                      <Circle size={16} color="#64748b" />
                    )}
                    <span style={{ color: c.done ? "var(--fg)" : "var(--muted)" }}>
                      {c.label}
                    </span>
                  </div>
                ))}
              </div>

              {/* Inline Fast-Track Form for Stage 2 (Project) */}
              {current.id === "project" && !current.done && (
                <form
                  onSubmit={handleInlineCreateProject}
                  className="card-subtle"
                  style={{ marginBottom: 16 }}
                >
                  <h4 style={{ marginBottom: 8 }}>Quick-Create Your First Project</h4>
                  <div className="row">
                    <div className="grow">
                      <input
                        className="input"
                        value={quickProjectName}
                        onChange={(e) => setQuickProjectName(e.target.value)}
                        placeholder="e.g. v0.3-optical-hd"
                      />
                    </div>
                    <button type="submit" disabled={running || !quickProjectName.trim()}>
                      <Sparkles size={14} /> Create Project Now
                    </button>
                  </div>
                </form>
              )}

              {/* Inline Fast-Track Promote for Stage 5 (Verify) */}
              {current.id === "verify" && !current.done && promotableRuns.length > 0 && (
                <div className="card-subtle" style={{ marginBottom: 16 }}>
                  <h4 style={{ marginBottom: 8 }}>Ready Runs Available to Promote</h4>
                  <div className="row">
                    {promotableRuns.slice(0, 3).map((r) => (
                      <button
                        key={r.name}
                        type="button"
                        className="success sm"
                        disabled={running}
                        onClick={() => handleQuickPromote(r.name)}
                      >
                        Promote {r.name} → Active
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {current.current &&
                progress?.next?.blocked_by &&
                progress.next.blocked_by.length > 0 && (
                  <div className="callout warn" style={{ marginBottom: 16 }}>
                    <div className="row" style={{ gap: 6, marginBottom: 4 }}>
                      <AlertTriangle size={15} color="#fbbf24" />
                      <strong>Downstream stages waiting on this step:</strong>
                    </div>
                    <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>
                      {progress.next.blocked_by.map((b, i) => (
                        <li key={i} className="small" style={{ color: "var(--fg-soft)" }}>
                          {b}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

              <div className="row spread" style={{ paddingTop: 6 }}>
                <div className="row" style={{ gap: 8 }}>
                  {current.action && (
                    <button
                      type="button"
                      disabled={running}
                      onClick={() => runAction(current.action)}
                    >
                      <Play size={14} />
                      {running
                        ? "Running…"
                        : current.action.label || current.cta}
                    </button>
                  )}
                  <Link
                    to={normalizeHref(current.href)}
                    className="btn secondary"
                  >
                    Open Workspace <ExternalLink size={14} />
                  </Link>
                </div>

                <div className="row" style={{ gap: 6 }}>
                  <button
                    type="button"
                    className="ghost sm"
                    disabled={idx === 0}
                    onClick={() => {
                      setUserPickedStep(true);
                      setIdx((v) => Math.max(0, v - 1));
                    }}
                  >
                    <ArrowLeft size={14} /> Prev
                  </button>
                  <button
                    type="button"
                    className="ghost sm"
                    disabled={idx >= stages.length - 1}
                    onClick={() => {
                      setUserPickedStep(true);
                      setIdx((v) => Math.min(stages.length - 1, v + 1));
                    }}
                  >
                    Next <ArrowRight size={14} />
                  </button>
                </div>
              </div>

              {jobId && (
                <div className="card-subtle mt" style={{ marginTop: 18 }}>
                  <div className="row spread" style={{ marginBottom: 8 }}>
                    <div className="row" style={{ gap: 8 }}>
                      <span className={`pill ${jobSnap?.live ? "run" : jobSnap?.status === "ok" ? "ok" : "warn"}`}>
                        {jobSnap?.status || "starting"}
                      </span>
                      <strong className="mono" style={{ fontSize: 12 }}>
                        {jobSnap?.title || jobId}
                      </strong>
                    </div>
                    <span className="mono small">
                      {Math.round((jobSnap?.progress || 0) * 100)}%
                    </span>
                  </div>
                  <div className="progress-track">
                    <div
                      className="progress-fill"
                      style={{ width: `${Math.round((jobSnap?.progress || 0) * 100)}%` }}
                    />
                  </div>
                  {jobSnap?.message && (
                    <p className="small mt" style={{ marginBottom: 6 }}>
                      {jobSnap.message}
                    </p>
                  )}
                  {jobSnap?.tail && (
                    <pre className="terminal" style={{ maxHeight: 190, marginTop: 8 }}>
                      {jobSnap.tail}
                    </pre>
                  )}
                  {jobSnap && !jobSnap.live && (
                    <div className="row mt">
                      <button
                        type="button"
                        className="secondary sm"
                        onClick={() => {
                          setJobId(null);
                          load();
                        }}
                      >
                        Dismiss Log
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          ) : (
            <div className="card">
              <p className="small">Loading guided progress…</p>
            </div>
          )}
        </div>
      </div>

      <style>{`
        @media (max-width: 820px) {
          .wizard-layout {
            grid-template-columns: 1fr !important;
          }
        }
      `}</style>
    </div>
  );
}
