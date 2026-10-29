import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Award,
  BarChart3,
  Cpu,
  FastForward,
  Play,
  RotateCcw,
} from "lucide-react";
import { api } from "../api/client";
import { useToast } from "../components/Toast";
import { useEventStream } from "../hooks/useEventStream";
import { TrainingCurvesChart } from "../components/Visuals";
import type {
  CurveData,
  DatasetSummary,
  JobSnapshot,
  ModelSummary,
  ProjectItem,
  RunSummary,
} from "../types";

export function Train() {
  const { show } = useToast();
  const [searchParams] = useSearchParams();
  const initialDataset = searchParams.get("dataset") || "";
  const initialProject = searchParams.get("project") || "";

  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [projects, setProjects] = useState<ProjectItem[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [shippedModels, setShippedModels] = useState<ModelSummary[]>([]);
  const [envInfo, setEnvInfo] = useState<any>({});

  // Form state
  const [dataset, setDataset] = useState(initialDataset);
  const [runName, setRunName] = useState("pose-v1");
  const [baseModel, setBaseModel] = useState("yolo26n-pose.pt");
  const [epochs, setEpochs] = useState(15);
  const [imgsz, setImgsz] = useState(448);
  const [batch, setBatch] = useState(0); // 0 = auto RAM-sized
  const [device, setDevice] = useState("auto");
  const [patience, setPatience] = useState(12);
  const [mosaic, setMosaic] = useState(0.5);
  const [projectSlug, setProjectSlug] = useState(initialProject);
  const [busy, setBusy] = useState(false);

  // Live job + Selected Run Curve inspector
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [jobSnap, setJobSnap] = useState<JobSnapshot | null>(null);
  const [selectedRun, setSelectedRun] = useState<string>("yolo26n_optical");
  const [curve, setCurve] = useState<CurveData | null>(null);
  const [plots, setPlots] = useState<string[]>([]);

  const loadAll = useCallback(async () => {
    try {
      const [ds, pr, md, st] = await Promise.all([
        api.listDatasets(),
        api.listProjects(),
        api.listModels(),
        api.status(),
      ]);
      setDatasets(ds);
      if (!dataset && ds.length > 0) setDataset(ds[0].name);
      setProjects(pr);
      setRuns(md.runs || []);
      setShippedModels(md.shipped || []);
      setEnvInfo(st?.env || {});
      if (md.runs && md.runs.length > 0 && selectedRun === "yolo26n_optical") {
        setSelectedRun(md.runs[0].name);
      }
    } catch {
      // ignore
    }
  }, [dataset, selectedRun]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const loadRunInspection = useCallback((name: string) => {
    if (!name) return;
    api
      .runCurve(name)
      .then(setCurve)
      .catch(() => setCurve(null));
    api
      .runImages(name)
      .then((r) => setPlots(r.images || []))
      .catch(() => setPlots([]));
  }, []);

  useEffect(() => {
    loadRunInspection(selectedRun);
  }, [selectedRun, loadRunInspection]);

  useEventStream(true, (ev) => {
    if (ev?.kind?.startsWith("job.") || ev?.kind?.startsWith("model.")) {
      loadAll();
      loadRunInspection(selectedRun);
    }
  });

  useEffect(() => {
    if (!activeJobId) return;
    const poll = async () => {
      try {
        const snap = await api.getJob(activeJobId, 50);
        setJobSnap(snap);
        loadRunInspection(selectedRun);
        if (!snap.live) loadAll();
      } catch {
        // ignore
      }
    };
    poll();
    const t = setInterval(poll, 2000);
    return () => clearInterval(t);
  }, [activeJobId, selectedRun, loadAll, loadRunInspection]);

  const handleStartTrain = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!dataset) {
      show("Select or create a dataset first", true);
      return;
    }
    setBusy(true);
    try {
      const res = await api.startTrain({
        dataset,
        name: runName.trim() || "pose-run",
        model: baseModel,
        epochs,
        imgsz,
        batch,
        device,
        patience,
        mosaic,
        project: projectSlug || undefined,
      });
      setActiveJobId(res.job.id);
      if (res.run) setSelectedRun(res.run);
      show(`Training job queued: ${res.run || res.job.id}`);
    } catch (err: any) {
      show(err instanceof Error ? err.message : "Failed to start training", true);
    } finally {
      setBusy(false);
    }
  };

  const handleExtend = async (name: string) => {
    try {
      const res = await api.extendRun(name, { epochs: 10 });
      setActiveJobId(res.job.id);
      show(`Extending ${name} by +10 epochs`);
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Extend failed", true);
    }
  };

  const handleResume = async (name: string) => {
    try {
      const res = await api.resumeRun(name);
      setActiveJobId(res.job.id);
      show(`Resuming ${name}`);
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Resume failed", true);
    }
  };

  const handlePromote = async (name: string) => {
    try {
      await api.promoteModel(name);
      show(`Promoted ${name} to active model!`);
      loadAll();
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Promote failed", true);
    }
  };

  return (
    <div className="wrap">
      <div className="page-header">
        <div>
          <div className="page-eyebrow">
            <Cpu size={13} /> Stage 3 · Neural Corner Pose Detector
          </div>
          <h1>Train YOLO26-Pose</h1>
          <p>
            Fine-tune a lightweight 4-keypoint pose network to localize skewed, blurred, or glared optical frames in under 25 ms on CPU.
          </p>
        </div>
        <div className="row">
          <span className="pill ok">
            {envInfo.cpu_count || "—"} vCPU · {envInfo.cuda ? "CUDA GPU" : "CPU Mode"}
          </span>
          {!envInfo.ultralytics && (
            <span className="pill warn">Ultralytics optional</span>
          )}
        </div>
      </div>

      <div className="grid-2" style={{ alignItems: "start" }}>
        {/* Training Configuration Form */}
        <form onSubmit={handleStartTrain} className="card" style={{ marginBottom: 0 }}>
          <div className="card-header">
            <h2>Launch Training Run</h2>
            <span className="small mono">RAM-safe batching</span>
          </div>

          <div className="grid-2">
            <label className="field">
              <span>Dataset</span>
              <select
                value={dataset}
                onChange={(e) => setDataset(e.target.value)}
                required
              >
                {datasets.length === 0 && (
                  <option value="">No datasets — generate one first</option>
                )}
                {datasets.map((d) => (
                  <option key={d.name} value={d.name}>
                    {d.name} ({d.images} imgs)
                  </option>
                ))}
              </select>
            </label>

            <label className="field">
              <span>Run Name</span>
              <input
                value={runName}
                onChange={(e) => setRunName(e.target.value)}
                placeholder="pose-v1"
                required
              />
            </label>
          </div>

          <div className="grid-2 mt">
            <label className="field">
              <span>Base Weights / Fine-Tune Source</span>
              <select
                value={baseModel}
                onChange={(e) => setBaseModel(e.target.value)}
              >
                <option value="yolo26n-pose.pt">yolo26n-pose.pt (Nano Pose)</option>
                {shippedModels.map((sm) => (
                  <option key={sm.name} value={sm.weights}>
                    Shipped: {sm.name}
                  </option>
                ))}
              </select>
            </label>

            <label className="field">
              <span>Link Project</span>
              <select
                value={projectSlug}
                onChange={(e) => setProjectSlug(e.target.value)}
              >
                <option value="">None</option>
                {projects.map((p) => (
                  <option key={p.slug} value={p.slug}>
                    {p.title || p.slug}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="grid-4 mt">
            <label className="field">
              <span>Epochs</span>
              <input
                type="number"
                min={1}
                max={300}
                value={epochs}
                onChange={(e) => setEpochs(Number(e.target.value) || 10)}
              />
            </label>

            <label className="field">
              <span>Image Size</span>
              <select
                value={imgsz}
                onChange={(e) => setImgsz(Number(e.target.value))}
              >
                <option value={320}>320 px</option>
                <option value={448}>448 px (CPU Rec)</option>
                <option value={640}>640 px (HD)</option>
              </select>
            </label>

            <label className="field">
              <span>Batch (0=Auto)</span>
              <input
                type="number"
                min={0}
                max={64}
                value={batch}
                onChange={(e) => setBatch(Number(e.target.value) || 0)}
              />
            </label>

            <label className="field">
              <span>Patience</span>
              <input
                type="number"
                min={3}
                max={100}
                value={patience}
                onChange={(e) => setPatience(Number(e.target.value) || 12)}
              />
            </label>
          </div>

          <div className="grid-2 mt">
            <label className="field">
              <span>Compute Device</span>
              <select value={device} onChange={(e) => setDevice(e.target.value)}>
                <option value="auto">Auto (CUDA if available, else CPU)</option>
                <option value="cpu">Force CPU</option>
                <option value="0">GPU 0</option>
              </select>
            </label>

            <label className="field">
              <span>Mosaic Augmentation ({mosaic.toFixed(2)})</span>
              <input
                type="range"
                min={0}
                max={1}
                step={0.1}
                value={mosaic}
                onChange={(e) => setMosaic(Number(e.target.value))}
              />
            </label>
          </div>

          <div className="row spread mt" style={{ marginTop: 18 }}>
            <Link to="/datasets" className="small">
              Need more frames? Open Dataset Studio →
            </Link>
            <button type="submit" disabled={busy || !dataset}>
              <Play size={15} />
              {busy ? "Queueing…" : "Start Training Job"}
            </button>
          </div>

          {jobSnap && (
            <div className="card-subtle mt">
              <div className="row spread" style={{ marginBottom: 6 }}>
                <span
                  className={`pill ${
                    jobSnap.live
                      ? "run"
                      : jobSnap.status === "ok"
                      ? "ok"
                      : "warn"
                  }`}
                >
                  {jobSnap.status}
                </span>
                <span className="mono small">
                  {Math.round((jobSnap.progress || 0) * 100)}%
                </span>
              </div>
              <div className="progress-track">
                <div
                  className="progress-fill"
                  style={{
                    width: `${Math.round((jobSnap.progress || 0) * 100)}%`,
                  }}
                />
              </div>
              {jobSnap.message && (
                <p className="small mt" style={{ marginBottom: 0 }}>
                  {jobSnap.message}
                </p>
              )}
            </div>
          )}
        </form>

        {/* Interactive Training Curves & Run Inspector */}
        <div className="card" style={{ marginBottom: 0 }}>
          <div className="card-header">
            <div>
              <h2>Convergence &amp; Pose mAP Curves</h2>
              <p className="small" style={{ margin: 0 }}>
                Hover over any epoch to inspect precision, recall, box &amp; pose mAP50-95, or loss terms.
              </p>
            </div>
            <select
              value={selectedRun}
              onChange={(e) => setSelectedRun(e.target.value)}
              style={{ width: 205 }}
            >
              <option value="yolo26n_optical">yolo26n_optical (Shipped)</option>
              {runs.map((r) => (
                <option key={r.name} value={r.name}>
                  {r.name}
                </option>
              ))}
            </select>
          </div>

          <TrainingCurvesChart curve={curve} />

          {plots.length > 0 && (
            <div className="mt">
              <h4 style={{ marginBottom: 8 }}>Validation Predictions &amp; Plots</h4>
              <div className="grid-3" style={{ gap: 8 }}>
                {plots.slice(0, 3).map((p) => (
                  <a
                    key={p}
                    href={api.runImageUrl(selectedRun, p)}
                    target="_blank"
                    rel="noreferrer"
                    className="card-subtle"
                    style={{ padding: 6, textDecoration: "none" }}
                  >
                    <img
                      src={api.runImageUrl(selectedRun, p)}
                      alt={p}
                      style={{ width: "100%", borderRadius: 6, display: "block" }}
                    />
                    <div className="small mono" style={{ marginTop: 4, fontSize: 10 }}>
                      {p}
                    </div>
                  </a>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Runs Table */}
      {runs.length > 0 && (
        <div className="card mt" style={{ marginTop: 18 }}>
          <div className="card-header">
            <h3>Training Runs ({runs.length})</h3>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Weights</th>
                  <th className="num">Epochs</th>
                  <th className="num">Pose mAP50-95</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.name}>
                    <td>
                      <strong className="mono">{r.name}</strong>
                    </td>
                    <td>
                      <span className={`pill ${r.has_weights ? "ok" : "warn"}`}>
                        {r.has_weights ? "best.pt ✓" : r.status || "in progress"}
                      </span>
                    </td>
                    <td className="num">
                      {r.epoch ?? "—"}/{r.epochs ?? "—"}
                    </td>
                    <td className="num">
                      {typeof r.mAP === "number" ? r.mAP.toFixed(4) : "—"}
                    </td>
                    <td>
                      <div className="row" style={{ gap: 6 }}>
                        <button
                          type="button"
                          className="secondary sm"
                          onClick={() => setSelectedRun(r.name)}
                        >
                          Curves
                        </button>
                        {r.has_weights && (
                          <button
                            type="button"
                            className="success sm"
                            onClick={() => handlePromote(r.name)}
                          >
                            <Award size={13} /> Promote
                          </button>
                        )}
                        <Link
                          to={`/evaluate?run=${encodeURIComponent(r.name)}`}
                          className="btn ghost sm"
                        >
                          <BarChart3 size={13} /> Benchmark
                        </Link>
                        <button
                          type="button"
                          className="ghost sm"
                          onClick={() => handleExtend(r.name)}
                          title="Extend +10 epochs"
                        >
                          <FastForward size={13} /> +10 ep
                        </button>
                        <button
                          type="button"
                          className="ghost sm"
                          onClick={() => handleResume(r.name)}
                          title="Resume interrupted run"
                        >
                          <RotateCcw size={13} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
