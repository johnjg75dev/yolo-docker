import { useEffect, useRef, useState, Fragment } from "react";
import { api } from "../api/client";
import { useToast } from "../components/Toast";

const KINDS = ["", "train", "synth", "merge", "evaluate", "promote"];

function statusPill(s: string) {
  const cls =
    s === "ok" || s === "done" ? "ok" :
    s === "running" || s === "queued" ? "run" :
    s === "failed" || s === "interrupted" ? "warn" : "";
  return <span className={`pill ${cls}`}>{s}</span>;
}

function ProgressBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, (value || 0) * 100));
  return (
    <div style={{ background: "#eee", borderRadius: 4, height: 8, overflow: "hidden", width: "100%" }}>
      <div style={{ width: `${pct}%`, height: "100%", background: "#4f8cff", transition: "width .3s" }} />
    </div>
  );
}

export function Jobs() {
  const [jobs, setJobs] = useState<any[]>([]);
  const [kind, setKind] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [tails, setTails] = useState<Record<string, string>>({});
  const { show } = useToast();
  const timer = useRef<any>(null);

  const load = async () => {
    try {
      const j = await api.listJobs(kind || undefined, 100);
      setJobs(j);
    } catch (e: any) {
      show(e instanceof Error ? e.message : "load failed", true);
    }
  };

  useEffect(() => {
    load();
    timer.current = setInterval(load, 2000);
    return () => clearInterval(timer.current);
  }, [kind]);

  const cancel = async (jid: string) => {
    if (!confirm(`Cancel job ${jid}?`)) return;
    try {
      await api.cancelJob(jid);
      show("cancelled");
      load();
    } catch (e: any) {
      show(e instanceof Error ? e.message : "cancel failed", true);
    }
  };

  const toggle = async (jid: string) => {
    if (expanded === jid) { setExpanded(null); return; }
    setExpanded(jid);
    try {
      const snap = await api.getJob(jid);
      setTails((t) => ({ ...t, [jid]: snap.tail || "" }));
      setJobs((prev) => prev.map((j) => (j.id === jid ? snap : j)));
    } catch (e: any) {
      show(e instanceof Error ? e.message : "load failed", true);
    }
  };

  return (
    <div className="wrap">
      <h1>Jobs</h1>
      <p className="small">All background jobs (training, synthesis, evaluation, …). Live updates every 2 s. Click a row to view its log tail.</p>

      <div className="card">
        <div className="row">
          <label className="field">
            <span>kind</span>
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              {KINDS.map((k) => <option key={k} value={k}>{k || "all"}</option>)}
            </select>
          </label>
          <div className="mt">
            <button onClick={load}>refresh</button>
          </div>
        </div>
      </div>

      <div className="card">
        {jobs.length === 0 ? (
          <p className="small">No jobs.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Kind</th>
                <th>Title</th>
                <th>Status</th>
                <th>Progress</th>
                <th>Elapsed</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j: any) => (
                <Fragment key={j.id}>
                  <tr onClick={() => toggle(j.id)} style={{ cursor: "pointer" }}>
                    <td className="small">{j.id}</td>
                    <td>{j.kind}</td>
                    <td>{j.title}</td>
                    <td>{statusPill(j.status)}</td>
                    <td style={{ minWidth: 140 }}>
                      <ProgressBar value={j.progress} />
                      <div className="small">{j.message || ""} {j.metrics && Object.keys(j.metrics).length ? `· ${Object.entries(j.metrics).map(([k, v]) => `${k}=${typeof v === "number" ? v.toFixed(3) : v}`).join(" ")}` : ""}</div>
                    </td>
                    <td className="small">{Math.round(j.elapsed || 0)}s</td>
                    <td>
                      {j.live ? (
                        <button onClick={(e) => { e.stopPropagation(); cancel(j.id); }}>cancel</button>
                      ) : null}
                    </td>
                  </tr>
                  {expanded === j.id && (
                    <tr key={j.id + "-log"}>
                      <td colSpan={7}>
                        <pre style={{ maxHeight: 300, overflow: "auto", fontSize: 12, background: "#111", color: "#eee", padding: 8, borderRadius: 4 }}>
                          {tails[j.id] || "(no log)"}
                        </pre>
                        {j.error ? <p className="small" style={{ color: "red" }}>{j.error}</p> : null}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
