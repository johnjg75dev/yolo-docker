import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useToast } from "../components/Toast";

export function Wizard() {
  const navigate = useNavigate();
  const { show } = useToast();
  const [progress, setProgress] = useState<any>(null);
  const [idx, setIdx] = useState(0);
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobSnap, setJobSnap] = useState<any>(null);
  const [running, setRunning] = useState(false);
  const timer = useRef<any>(null);

  const load = async () => {
    try {
      const p = await api.progress();
      setProgress(p);
      const stages = p?.stages || [];
      const firstOpen = stages.findIndex((s: any) => !s.done);
      if (firstOpen >= 0) setIdx(firstOpen);
      else if (stages.length) setIdx(stages.length - 1);
    } catch (e: any) {
      show(e instanceof Error ? e.message : "progress failed", true);
    }
  };

  useEffect(() => {
    load();
    timer.current = setInterval(load, 6000);
    return () => clearInterval(timer.current);
  }, []);

  useEffect(() => {
    if (!jobId) { setJobSnap(null); return; }
    const t = setInterval(async () => {
      try {
        const snap = await api.getJob(jobId);
        setJobSnap(snap);
        if (!snap.live) {
          setRunning(false);
          load();
        }
      } catch (e: any) {
        show(e instanceof Error ? e.message : "job poll failed", true);
      }
    }, 1500);
    return () => clearInterval(t);
  }, [jobId]);

  const runAction = async (action: any) => {
    if (!action) return;
    if (action.kind === "link") {
      if (action.href) navigate(action.href);
      return;
    }
    if (action.kind === "post") {
      setRunning(true);
      try {
        const res = await api.post(action.endpoint, action.payload || {});
        const jid = res?.job?.id || res?.id;
        if (jid) setJobId(jid);
        else { setRunning(false); show("done"); load(); }
      } catch (e: any) {
        setRunning(false);
        show(e instanceof Error ? e.message : "action failed", true);
      }
    }
  };

  const stages = progress?.stages || [];
  const current = stages[idx];
  const pct = progress?.percent ?? 0;

  return (
    <div className="wrap">
      <h1>Wizard</h1>
      <p className="small">Guided flow: check the box, name the work, build data, train, verify, send.</p>

      <div className="card">
        <div className="row" style={{ alignItems: "center", gap: 12 }}>
          <div style={{ flex: 1 }}>
            <div style={{ background: "#eee", borderRadius: 4, height: 8, overflow: "hidden" }}>
              <div style={{ width: `${Math.round(pct * 100)}%`, height: "100%", background: "#4f8cff", transition: "width .3s" }} />
            </div>
          </div>
          <span className="small">{Math.round(pct * 100)}% complete</span>
        </div>
      </div>

      <div className="row" style={{ alignItems: "flex-start", gap: 12 }}>
        <div className="card" style={{ minWidth: 180, flex: "0 0 200px" }}>
          <h3 style={{ marginTop: 0 }}>Stages</h3>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {stages.map((s: any, i: number) => (
              <button
                key={s.id}
                className={i === idx ? "primary" : "secondary"}
                style={{ textAlign: "left", opacity: s.done ? 1 : 0.85 }}
                onClick={() => setIdx(i)}
              >
                <span className={`pill ${s.done ? "ok" : i === idx ? "run" : ""}`} style={{ marginRight: 6 }}>
                  {s.done ? "✓" : i + 1}
                </span>
                {s.title}
              </button>
            ))}
          </div>
        </div>

        <div className="card" style={{ flex: 1 }}>
          {current ? (
            <>
              <h2 style={{ marginTop: 0 }}>{current.title}</h2>
              <p className="small">{current.why}</p>
              <p>{current.detail}</p>
              {current.blockers?.length ? (
                <p className="small" style={{ color: "#b00" }}>Waiting on: {current.blockers.join(", ")}</p>
              ) : null}
              <h4>Checklist</h4>
              <ul style={{ paddingLeft: 18 }}>
                {(current.checklist || []).map((c: any, i: number) => (
                  <li key={i} style={{ color: c.done ? "#1a7f37" : undefined }}>
                    {c.done ? "✓" : "○"} {c.label}
                  </li>
                ))}
              </ul>
              <div className="row mt" style={{ gap: 8 }}>
                {current.action && (
                  <button
                    disabled={running}
                    onClick={() => runAction(current.action)}
                  >
                    {running ? "running…" : (current.action.label || current.cta)}
                  </button>
                )}
                {current.href && (
                  <Link to={current.href}>
                    <button className="secondary">open full page →</button>
                  </Link>
                )}
                {idx > 0 && (
                  <button className="secondary" onClick={() => setIdx(idx - 1)}>back</button>
                )}
                {idx < stages.length - 1 && (
                  <button className="secondary" onClick={() => setIdx(idx + 1)}>next</button>
                )}
              </div>

              {jobId && (
                <div className="card mt">
                  <h4 style={{ marginTop: 0 }}>Live job</h4>
                  {jobSnap ? (
                    <>
                      <div className="small">{jobSnap.id} — {jobSnap.status}</div>
                      <div style={{ background: "#eee", borderRadius: 4, height: 8, overflow: "hidden", marginTop: 4 }}>
                        <div style={{ width: `${Math.round((jobSnap.progress || 0) * 100)}%`, height: "100%", background: "#4f8cff" }} />
                      </div>
                      <div className="small mt">{jobSnap.message || ""}</div>
                      {jobSnap.tail && (
                        <pre style={{ maxHeight: 200, overflow: "auto", fontSize: 11, background: "#111", color: "#eee", padding: 6, borderRadius: 4, marginTop: 6 }}>
                          {jobSnap.tail}
                        </pre>
                      )}
                      {!jobSnap.live && (
                        <button className="secondary mt" onClick={() => { setJobId(null); load(); }}>dismiss</button>
                      )}
                    </>
                  ) : (
                    <p className="small">loading…</p>
                  )}
                </div>
              )}
            </>
          ) : (
            <p className="small">Loading progress…</p>
          )}
        </div>
      </div>
    </div>
  );
}
