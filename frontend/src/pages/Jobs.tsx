import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  Pause,
  Play,
  RefreshCw,
  Square,
  Terminal,
  Trash2,
} from "lucide-react";
import { api } from "../api/client";
import { useToast } from "../components/Toast";
import { useEventStream } from "../hooks/useEventStream";
import type { JobSnapshot } from "../types";

export function Jobs() {
  const { show } = useToast();
  const [jobs, setJobs] = useState<JobSnapshot[]>([]);
  const [filter, setFilter] = useState<"all" | "live" | "ok" | "error">("all");
  const [selectedId, setSelectedId] = useState<string>("");
  const [selectedSnap, setSelectedSnap] = useState<JobSnapshot | null>(null);

  const load = useCallback(async () => {
    try {
      const list = await api.listJobs();
      setJobs(list);
      if (!selectedId && list.length > 0) {
        setSelectedId(list[0].id);
      }
    } catch {
      // ignore
    }
  }, [selectedId]);

  useEffect(() => {
    load();
    const t = setInterval(load, 2500);
    return () => clearInterval(t);
  }, [load]);

  useEventStream(true, (ev) => {
    if (ev?.kind?.startsWith("job.")) load();
  });

  useEffect(() => {
    if (!selectedId) {
      setSelectedSnap(null);
      return;
    }
    const fetchTail = async () => {
      try {
        const snap = await api.getJob(selectedId, 160);
        setSelectedSnap(snap);
      } catch {
        setSelectedSnap(null);
      }
    };
    fetchTail();
    const t = setInterval(fetchTail, 1500);
    return () => clearInterval(t);
  }, [selectedId]);

  const handlePause = async (id: string) => {
    try {
      await api.pauseJob(id);
      show("Paused job (SIGSTOP)");
      load();
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Pause failed", true);
    }
  };

  const handleResume = async (id: string) => {
    try {
      await api.resumeJob(id);
      show("Resumed job (SIGCONT)");
      load();
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Resume failed", true);
    }
  };

  const handleCancel = async (id: string) => {
    try {
      await api.cancelJob(id);
      show("Cancellation signal sent");
      load();
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Cancel failed", true);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.deleteJob(id);
      if (selectedId === id) setSelectedId("");
      show("Removed finished job record");
      load();
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Delete failed", true);
    }
  };

  const shown = jobs.filter((j) => {
    if (filter === "live") return j.live;
    if (filter === "ok") return j.status === "ok";
    if (filter === "error") return j.status === "error" || j.status === "cancelled";
    return true;
  });

  const statusPill = (j: JobSnapshot) => {
    if (j.status === "ok") return "ok";
    if (j.live) return "run";
    if (j.status === "paused") return "warn";
    return "bad";
  };

  return (
    <div className="wrap">
      <div className="page-header">
        <div>
          <div className="page-eyebrow">
            <Activity size={13} /> Subprocess Orchestration &amp; Logs
          </div>
          <h1>Jobs &amp; Queue</h1>
          <p>
            Monitor dataset synthesis, capture imports, YOLO26-pose training, and multi-noise benchmark subprocesses in real time.
          </p>
        </div>
        <div className="row">
          <div className="tabs">
            {(["all", "live", "ok", "error"] as const).map((f) => (
              <button
                key={f}
                type="button"
                className={`tab-btn ${filter === f ? "active" : ""}`}
                onClick={() => setFilter(f)}
                style={{ textTransform: "capitalize" }}
              >
                {f}
              </button>
            ))}
          </div>
          <button type="button" className="secondary sm" onClick={load}>
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(320px, 420px) minmax(0, 1fr)",
          gap: 16,
          alignItems: "start",
        }}
        className="jobs-grid"
      >
        {/* Left Job List */}
        <div className="card" style={{ marginBottom: 0 }}>
          <div className="card-header">
            <h3>Queue ({shown.length})</h3>
          </div>

          {shown.length === 0 ? (
            <div className="empty-state" style={{ padding: "24px 12px" }}>
              <p className="small" style={{ margin: 0 }}>
                No jobs in this filter view.
              </p>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {shown.map((j) => {
                const sel = j.id === selectedId;
                return (
                  <div
                    key={j.id}
                    onClick={() => setSelectedId(j.id)}
                    className="card-subtle"
                    style={{
                      cursor: "pointer",
                      borderColor: sel ? "var(--accent)" : "var(--border)",
                      background: sel
                        ? "rgba(34, 211, 238, 0.08)"
                        : "var(--inset)",
                    }}
                  >
                    <div className="row spread" style={{ marginBottom: 6 }}>
                      <strong style={{ fontSize: 13 }}>{j.title}</strong>
                      <span className={`pill ${statusPill(j)}`}>{j.status}</span>
                    </div>
                    <div className="progress-track" style={{ height: 6 }}>
                      <div
                        className="progress-fill"
                        style={{
                          width: `${Math.round((j.progress || 0) * 100)}%`,
                        }}
                      />
                    </div>
                    <div
                      className="row spread small"
                      style={{ marginTop: 6, fontSize: 11 }}
                    >
                      <span className="mono">
                        {j.kind} · #{j.id.slice(0, 8)}
                      </span>
                      <span>
                        {j.elapsed != null ? `${j.elapsed.toFixed(1)}s` : ""}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Right Live Log Terminal */}
        <div className="card" style={{ marginBottom: 0 }}>
          {selectedSnap ? (
            <>
              <div className="card-header">
                <div>
                  <div className="row" style={{ gap: 8 }}>
                    <Terminal size={16} color="#22d3ee" />
                    <h2>{selectedSnap.title}</h2>
                    <span className={`pill ${statusPill(selectedSnap)}`}>
                      {selectedSnap.status}
                    </span>
                  </div>
                  <div className="small mono" style={{ marginTop: 2 }}>
                    ID: {selectedSnap.id} · Kind: {selectedSnap.kind} · Progress:{" "}
                    {Math.round((selectedSnap.progress || 0) * 100)}%
                  </div>
                </div>

                <div className="row" style={{ gap: 6 }}>
                  {selectedSnap.status === "running" && (
                    <button
                      type="button"
                      className="secondary sm"
                      onClick={() => handlePause(selectedSnap.id)}
                    >
                      <Pause size={14} /> Pause
                    </button>
                  )}
                  {selectedSnap.status === "paused" && (
                    <button
                      type="button"
                      className="sm"
                      onClick={() => handleResume(selectedSnap.id)}
                    >
                      <Play size={14} /> Resume
                    </button>
                  )}
                  {selectedSnap.live && (
                    <button
                      type="button"
                      className="danger sm"
                      onClick={() => handleCancel(selectedSnap.id)}
                    >
                      <Square size={14} /> Cancel
                    </button>
                  )}
                  {!selectedSnap.live && (
                    <button
                      type="button"
                      className="ghost sm"
                      onClick={() => handleDelete(selectedSnap.id)}
                    >
                      <Trash2 size={14} color="#f87171" /> Remove
                    </button>
                  )}
                </div>
              </div>

              {selectedSnap.message && (
                <div className="callout" style={{ marginBottom: 12 }}>
                  <strong style={{ color: "#fff" }}>Status message: </strong>
                  {selectedSnap.message}
                </div>
              )}

              <pre className="terminal">
                {selectedSnap.tail || "(Waiting for subprocess output…)"}
              </pre>
            </>
          ) : (
            <div className="empty-state">
              <Terminal size={28} color="#64748b" style={{ marginBottom: 8 }} />
              <h3>Select a Job to Inspect Console Output</h3>
              <p className="small">
                Every background synthesis, training, and evaluation worker streams stdout/stderr to a persistent log file on disk.
              </p>
            </div>
          )}
        </div>
      </div>

      <style>{`
        @media (max-width: 860px) {
          .jobs-grid {
            grid-template-columns: 1fr !important;
          }
        }
      `}</style>
    </div>
  );
}
