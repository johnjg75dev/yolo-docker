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
  const [trainJobs, setTrainJobs] = useState<any[]>([]);
  const [earlyStop, setEarlyStop] = useState(false);
  const [earlyStopPatience, setEarlyStopPatience] = useState("5");
  const [earlyStopStart, setEarlyStopStart] = useState("0");
  const [evalEvery, setEvalEvery] = useState("1");
  const [loading, setLoading] = useState(false);
  const { show } = useToast();

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
      api.listJobs("train", 20).then(setTrainJobs);
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
                    ) : (
                      <button className="secondary" onClick={async () => {
                        if (!confirm(`Delete job ${j.id}?`)) return;
                        try { await api.deleteJob(j.id); show("deleted"); api.listJobs("train", 20).then(setTrainJobs); }
                        catch (e: any) { show(e instanceof Error ? e.message : "delete failed", true); }
                      }}>delete</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="small mt"><Link to="/jobs">View all jobs and run history →</Link></p>
      </div>
    </div>
  );
}