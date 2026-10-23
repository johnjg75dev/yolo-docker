import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useToast } from "../components/Toast";

export function Train() {
  const [datasets, setDatasets] = useState<any[]>([]);
  const [dataset, setDataset] = useState("");
  const [name, setName] = useState("");
  const [epochs, setEpochs] = useState("");
  const [imgsz, setImgsz] = useState("");
  const [batch, setBatch] = useState("");
  const [device, setDevice] = useState("");
  const [workers, setWorkers] = useState("");
  const [model, setModel] = useState("");
  const [seed, setSeed] = useState("");
  const [project, setProject] = useState("");
  const [runs, setRuns] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const { show } = useToast();

  useEffect(() => {
    api.trainDefaults().then((d) => {
      setEpochs(String(d.epochs));
      setImgsz(String(d.imgsz));
      setBatch(String(d.batch));
      setDevice(d.device);
      setWorkers(String(d.workers));
      setModel(d.model);
      setSeed(String(d.seed));
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
        model: model || undefined,
        seed: seed ? Number(seed) : undefined,
        project: project || undefined,
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
              <span>model</span>
              <input value={model} onChange={(e) => setModel(e.target.value)} />
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
          <div className="mt">
            <button type="submit" disabled={loading}>
              {loading ? "starting…" : "start training"}
            </button>
          </div>
        </form>
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
                <tr key={r.id}>
                  <td>{r.name}</td>
                  <td>{r.dataset}</td>
                  <td>
                    <span className={`pill ${r.status === "done" ? "ok" : r.status === "running" ? "run" : "warn"}`}>
                      {r.status}
                    </span>
                  </td>
                  <td>{r.epoch}/{r.epochs}</td>
                  <td>{r.mAP?.toFixed(3)}</td>
                  <td>
                    <Link to={`/train/${r.id}`}>view</Link>
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