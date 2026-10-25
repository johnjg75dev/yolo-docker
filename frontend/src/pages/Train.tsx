import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useToast } from "../components/Toast";

export function Train() {
  const [architectures, setArchitectures] = useState<any[]>([]);
  const [datasets, setDatasets] = useState<any[]>([]);
  const [dataset, setDataset] = useState("");
  const [name, setName] = useState("");
  const [epochs, setEpochs] = useState("");
  const [imgsz, setImgsz] = useState("");
  const [batch, setBatch] = useState("");
  const [device, setDevice] = useState("");
  const [workers, setWorkers] = useState("");
  const [model, setModel] = useState("");
  const [customModel, setCustomModel] = useState("");
  const [seed, setSeed] = useState("");
  const [project, setProject] = useState("");
  const [runs, setRuns] = useState<any[]>([]);
  const [trainJobs, setTrainJobs] = useState<any[]>([]);
  const [earlyStop, setEarlyStop] = useState(false);
  const [earlyStopPatience, setEarlyStopPatience] = useState("5");
  const [earlyStopStart, setEarlyStopStart] = useState("0");
  const [evalEvery, setEvalEvery] = useState("1");
  const [viewRun, setViewRun] = useState("");
  const [runImgs, setRunImgs] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const { show } = useToast();

  useEffect(() => {
    if (!viewRun) { setRunImgs([]); return; }
    const load = () => api.runImages(viewRun).then((r) => setRunImgs(r.images || [])).catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [viewRun]);

  useEffect(() => {
    const refresh = () => api.listJobs("train", 20).then(setTrainJobs).catch(() => {});
    refresh();
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    api.trainDefaults().then((d) => {
      setArchitectures(d.architectures || []);
      setDatasets(d.datasets || []);
      setEpochs(String(d.suggested?.epochs ?? d.epochs ?? 40));
      setImgsz(String(d.suggested?.imgsz ?? d.imgsz ?? 448));
      setBatch(String(d.suggested?.batch ?? d.batch ?? 8));
      setDevice(d.suggested?.device ?? d.device ?? "cpu");
      setModel(d.suggested?.model ?? d.model ?? "yolo26n-pose.pt");
      setWorkers(String(d.suggested?.workers ?? d.env?.cpus_available ?? 2));
      setSeed(String(d.seed ?? 0));
      setProject(d.project);
    }).catch((e) => show(e.message, true));
    api.listDatasets().then(setDatasets).catch((e) => show(e.message, true));
    api.listRuns().then(setRuns).catch((e) => show(e.message, true));
  }, []);

  const handleStart = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!dataset) { show("select a dataset", true); return; }
    setLoading(true);
    try {
      await api.startTrain({
        dataset,
        name: name || undefined,
        epochs: epochs ? Number(epochs) : undefined,
        imgsz: imgsz ? Number(imgsz) : undefined,
        batch: batch ? Number(batch) : undefined,
        device: device || undefined,
        workers: workers ? Number(workers) : undefined,
        model: (customModel.trim() || model) || undefined,
        seed: seed ? Number(seed) : undefined,
        project: project || undefined,
        patience: earlyStop ? Number(earlyStopPatience) : 0,
        early_stop_start: earlyStop ? Number(earlyStopStart) : 0,
        eval_every: evalEvery ? Number(evalEvery) : 1,
      });
      show("training started");
      api.listRuns().then(setRuns);
    } catch (e: any) {
      show(e instanceof Error ? e.message : "start failed", true);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="wrap">
      <h1>Train</h1>
      <p className="small">Train a YOLO finder model on a dataset.</p>

      <div className="card">
        <h2>Start training</h2>
        <form onSubmit={handleStart}>
          <label className="field">
            <span>dataset</span>
            <select value={dataset} onChange={(e) => setDataset(e.target.value)}>
              <option value="">select…</option>
              {datasets.map((d: any) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </select>
          </label>
          <label className="field mt">
            <span>run name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="optional" />
          </label>
          <div className="row mt">
            <label className="field grow">
              <span>epochs</span>
              <input type="number" value={epochs} onChange={(e) => setEpochs(e.target.value)} />
            </label>
            <label className="field grow">
              <span>imgsz</span>
              <input type="number" value={imgsz} onChange={(e) => setImgsz(e.target.value)} />
            </label>
            <label className="field grow">
              <span>batch</span>
              <input type="number" value={batch} onChange={(e) => setBatch(e.target.value)} />
            </label>
          </div>
          <div className="row mt">
            <label className="field grow">
              <span>device</span>
              <input value={device} onChange={(e) => setDevice(e.target.value)} />
            </label>
            <label className="field grow">
              <span>workers</span>
              <input type="number" value={workers} onChange={(e) => setWorkers(e.target.value)} />
            </label>
            <label className="field grow">
              <span>architecture</span>
              <select value={model} onChange={(e) => setModel(e.target.value)}>
                {architectures.map((a: any) => (
                  <option key={a.name} value={a.name}>
                    {a.name} — {a.family} {a.size} ({a.notes})
                  </option>
                ))}
              </select>
            </label>
            <label className="field grow">
              <span>custom model (override)</span>
              <input value={customModel} onChange={(e) => setCustomModel(e.target.value)}
                     placeholder="e.g. yolo26x-pose.pt or /path/to/best.pt" />
            </label>
          </div>
          <div className="row mt">
            <label className="field grow">
              <span>seed</span>
              <input type="number" value={seed} onChange={(e) => setSeed(e.target.value)} />
            </label>
            <label className="field grow">
              <span>project</span>
              <input value={project} onChange={(e) => setProject(e.target.value)} />
            </label>
          </div>
          <div className="row mt">
            <label className="field grow">
              <span>eval every (epochs)</span>
              <input type="number" value={evalEvery} onChange={(e) => setEvalEvery(e.target.value)} min="1" />
            </label>
            <label className="field grow">
              <span>
                <input type="checkbox" checked={earlyStop} onChange={(e) => setEarlyStop(e.target.checked)} />
                {" "}enable early stopping
              </span>
            </label>
            {earlyStop && (
              <>
                <label className="field grow">
                  <span>patience (epochs)</span>
                  <input type="number" value={earlyStopPatience} onChange={(e) => setEarlyStopPatience(e.target.value)} min="1" />
                </label>
                <label className="field grow">
                  <span>start at epoch</span>
                  <input type="number" value={earlyStopStart} onChange={(e) => setEarlyStopStart(e.target.value)} min="0" />
                </label>
              </>
            )}
          </div>
          <div className="mt">
            <button type="submit" disabled={loading}>
              {loading ? "starting…" : "start training"}
            </button>
          </div>
        </form>
      </div>

      <div className="card">
        <h2>Active training jobs</h2>
        {trainJobs.length === 0 ? (
          <p className="small">No training jobs.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Title</th>
                <th>Status</th>
                <th>Progress</th>
                <th>Elapsed</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {trainJobs.map((j: any) => (
                <tr key={j.id}>
                  <td className="small">{j.id}</td>
                  <td>{j.title}</td>
                  <td>
                    <span className={`pill ${j.status === "ok" ? "ok" : j.live ? "run" : "warn"}`}>{j.status}</span>
                  </td>
                  <td style={{ minWidth: 160 }}>
                    <div style={{ background: "#eee", borderRadius: 4, height: 8, overflow: "hidden" }}>
                      <div style={{ width: `${Math.round((j.progress || 0) * 100)}%`, height: "100%", background: "#4f8cff" }} />
                    </div>
                    <div className="small">{j.message || ""} {j.metrics && Object.keys(j.metrics).length ? `· ${Object.entries(j.metrics).map(([k, v]) => `${k}=${typeof v === "number" ? v.toFixed(3) : v}`).join(" ")}` : ""}</div>
                  </td>
                  <td className="small">{Math.round(j.elapsed || 0)}s</td>
                  <td>
                    {j.live ? (
                      <div className="row" style={{ gap: 4 }}>
                        {j.message === "paused" ? (
                          <button onClick={async () => { await api.resumeJob(j.id); show("resumed"); }}>resume</button>
                        ) : (
                          <button onClick={async () => { await api.pauseJob(j.id); show("paused"); }}>pause</button>
                        )}
                        <button onClick={async () => { await api.cancelJob(j.id); show("cancelled"); }}>cancel</button>
                      </div>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="small mt"><Link to="/jobs">View all jobs →</Link></p>
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