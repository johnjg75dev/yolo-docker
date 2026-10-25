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
  const [runs, setRuns] = useState<any[]>([]);
  const [viewRun, setViewRun] = useState("");
  const [runImgs, setRunImgs] = useState<string[]>([]);
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

  useEffect(() => {
    const refresh = () => api.listRuns().then(setRuns).catch(() => {});
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (!viewRun) { setRunImgs([]); return; }
    const load = () => api.runImages(viewRun).then((r) => setRunImgs(r.images || [])).catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [viewRun]);

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

  const del = async (jid: string) => {
    if (!confirm(`Delete job ${jid}?`)) return;
    try {
      await api.deleteJob(jid);
      show("deleted");
      load();
    } catch (e: any) {
      show(e instanceof Error ? e.message : "delete failed", true);
    }
  };

  const pause = async (jid: string) => {
    try { await api.pauseJob(jid); show("paused"); load(); }
    catch (e: any) { show(e instanceof Error ? e.message : "pause failed", true); }
  };
  const resume = async (jid: string) => {
    try { await api.resumeJob(jid); show("resumed"); load(); }
    catch (e: any) { show(e instanceof Error ? e.message : "resume failed", true); }
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
                      <div className="row" style={{ gap: 4 }}>
                        {j.live ? (
                          <>
                            {j.message === "paused" ? (
                              <button onClick={(e) => { e.stopPropagation(); resume(j.id); }}>resume</button>
                            ) : (
                              <button onClick={(e) => { e.stopPropagation(); pause(j.id); }}>pause</button>
                            )}
                            <button onClick={(e) => { e.stopPropagation(); cancel(j.id); }}>cancel</button>
                          </>
                        ) : null}
                        <button className="secondary" onClick={(e) => { e.stopPropagation(); del(j.id); }}>delete</button>
                      </div>
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

      <div className="card">
        <h2>Live training viewer</h2>
        <p className="small">View prediction images Ultralytics saved during validation (val_batch*_pred.jpg). Same images each epoch — visually track progress.</p>
        <label className="field">
          <span>run</span>
          <select value={viewRun} onChange={(e) => setViewRun(e.target.value)}>
            <option value="">select a run…</option>
            {runs.map((r: any) => <option key={r.name} value={r.name}>{r.name}</option>)}
          </select>
        </label>
        {viewRun && runImgs.length === 0 && <p className="small">No prediction images yet (Ultralytics saves them after the first validation pass).</p>}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 8, marginTop: 8 }}>
          {runImgs.filter((f) => /pred/i.test(f)).map((f) => (
            <div key={f} style={{ border: "1px solid #ddd", borderRadius: 4, overflow: "hidden" }}>
              <img src={api.runImageUrl(viewRun, f)} alt={f} style={{ width: "100%", display: "block" }} />
              <div className="small" style={{ padding: 4 }}>{f}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <h2>Runs</h2>
        {runs.length === 0 ? (
          <p className="small">No runs yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Dataset</th>
                <th>Status</th>
                <th>Epoch</th>
                <th>mAP</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r: any) => (
                <tr key={r.name}>
                  <td>
                    <button style={{ background: "none", border: "none", color: "#4f8cff", cursor: "pointer", padding: 0, textDecoration: "underline" }} onClick={() => setViewRun(r.name)}>
                      {r.name}
                    </button>
                  </td>
                  <td>{r.dataset}</td>
                  <td>
                    <span className={`pill ${r.status === "done" ? "ok" : r.status === "running" ? "run" : "warn"}`}>
                      {r.status}
                    </span>
                  </td>
                  <td>{r.epoch}/{r.epochs}</td>
                  <td>{r.mAP?.toFixed(3)}</td>
                  <td>
                    <button
                      className="secondary"
                      style={{ marginLeft: 6 }}
                      onClick={async () => {
                        try {
                          await api.resumeRun(r.name, {});
                          show("resumed");
                          api.listRuns().then(setRuns);
                        } catch (e: any) {
                          show(e instanceof Error ? e.message : "resume failed", true);
                        }
                      }}
                    >
                      resume
                    </button>
                    <button
                      className="secondary"
                      style={{ marginLeft: 6 }}
                      onClick={async () => {
                        const extra = prompt("Additional epochs:", "10");
                        if (!extra) return;
                        try {
                          await api.extendRun(r.name, { epochs: Number(extra) });
                          show("extension started");
                          api.listRuns().then(setRuns);
                        } catch (e: any) {
                          show(e instanceof Error ? e.message : "extend failed", true);
                        }
                      }}
                    >
                      extend
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
