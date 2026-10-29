import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Cpu,
  Database,
  Download,
  Edit3,
  Eye,
  GitMerge,
  Layers,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { DatasetGallery } from "./DatasetGallery";
import { DatasetReview } from "./DatasetReview";
import { api } from "../api/client";
import { useToast } from "../components/Toast";
import { useEventStream } from "../hooks/useEventStream";
import type { DatasetSummary, JobSnapshot, ProjectItem } from "../types";

function trainCount(d: DatasetSummary): number {
  return d.splits?.train?.images ?? 0;
}

function valCount(d: DatasetSummary): number {
  return d.splits?.val?.images ?? 0;
}

export function Datasets() {
  const { show } = useToast();
  const [searchParams] = useSearchParams();
  const initialProject = searchParams.get("project") || "";

  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [projects, setProjects] = useState<ProjectItem[]>([]);
  const [captures, setCaptures] = useState<any[]>([]);
  const [builderTab, setBuilderTab] = useState<"synth" | "captures" | "merge">(
    "synth"
  );

  // Synth form
  const [synthName, setSynthName] = useState("synth-optical");
  const [nTrain, setNTrain] = useState(120);
  const [nVal, setNVal] = useState(24);
  const [level, setLevel] = useState(0.5);
  const [seed, setSeed] = useState(42);
  const [projectSlug, setProjectSlug] = useState(initialProject);

  // Import captures form
  const [capName, setCapName] = useState("captured-pose");
  const [valFrac, setValFrac] = useState(0.2);
  const [verifiedOnly, setVerifiedOnly] = useState(false);

  // Merge form
  const [mergeName, setMergeName] = useState("hybrid-merged");
  const [mergeSources, setMergeSources] = useState<string[]>([]);

  // Active job tracking
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [jobSnap, setJobSnap] = useState<JobSnapshot | null>(null);
  const [busy, setBusy] = useState(false);

  const [review, setReview] = useState<string | null>(null);
  const [gallery, setGallery] = useState<DatasetSummary | null>(null);

  // Sample preview modal
  const [previewDs, setPreviewDs] = useState<DatasetSummary | null>(null);
  const [previewSplit, setPreviewSplit] = useState<"train" | "val">("train");
  const [previewIdx, setPreviewIdx] = useState(0);

  const load = useCallback(async () => {
    try {
      const list = await api.listDatasets();
      setDatasets(list);
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Failed to load datasets", true);
    }
  }, [show]);

  useEffect(() => {
    load();
    api.listProjects().then(setProjects).catch(() => {});
    api.listCaptureSessions().then(setCaptures).catch(() => {});
  }, [load]);

  useEventStream(true, (ev) => {
    if (ev?.kind?.startsWith("dataset.") || ev?.kind === "job.finished") {
      load();
    }
  });

  useEffect(() => {
    if (!activeJobId) return;
    const poll = async () => {
      try {
        const snap = await api.getJob(activeJobId, 40);
        setJobSnap(snap);
        if (!snap.live) {
          load();
        }
      } catch {
        // ignore
      }
    };
    poll();
    const t = setInterval(poll, 1500);
    return () => clearInterval(t);
  }, [activeJobId, load]);

  const handleSynth = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const res = await api.synthDataset({
        name: synthName.trim() || "synth",
        train: nTrain,
        val: nVal,
        level,
        seed,
        project: projectSlug || undefined,
      });
      setActiveJobId(res.job.id);
      show(`Synthesizing dataset "${res.dataset}" (${nTrain}+${nVal} frames)…`);
    } catch (err: any) {
      show(err instanceof Error ? err.message : "Synthesis failed", true);
    } finally {
      setBusy(false);
    }
  };

  const handleImportCaptures = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const res = await api.capturesDataset({
        name: capName.trim() || "captures",
        val_frac: valFrac,
        verified_only: verifiedOnly,
        project: projectSlug || undefined,
      });
      setActiveJobId(res.job.id);
      show(`Importing camera shots into "${res.dataset}"…`);
    } catch (err: any) {
      show(err instanceof Error ? err.message : "Capture import failed", true);
    } finally {
      setBusy(false);
    }
  };

  const handleMerge = async (e: React.FormEvent) => {
    e.preventDefault();
    if (mergeSources.length < 2) {
      show("Select at least 2 datasets to merge", true);
      return;
    }
    setBusy(true);
    try {
      const res = await api.mergeDatasets({
        name: mergeName.trim() || "merged",
        sources: mergeSources,
        project: projectSlug || undefined,
      });
      setActiveJobId(res.job.id);
      show(`Merging ${mergeSources.length} datasets into "${res.dataset}"…`);
    } catch (err: any) {
      show(err instanceof Error ? err.message : "Merge failed", true);
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (name: string) => {
    if (!confirm(`Delete dataset "${name}" and all its images/labels?`)) return;
    try {
      await api.deleteDataset(name);
      show(`Deleted ${name}`);
      load();
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Delete failed", true);
    }
  };

  const toggleArr = (
    arr: string[],
    v: string,
    set: (next: string[]) => void
  ) => {
    set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  };

  const maxPreviewIdx = previewDs
    ? Math.max(
        0,
        (previewSplit === "train"
          ? trainCount(previewDs)
          : valCount(previewDs)) - 1
      )
    : 0;

  return (
    <div className="wrap">
      <div className="page-header">
        <div>
          <div className="page-eyebrow">
            <Database size={13} /> Stage 2 · YOLO-Pose Corner Datasets
          </div>
          <h1>Dataset Studio</h1>
          <p>
            Build 4-corner pose datasets via physics-accurate channel synthesis, real smartphone captures, or multi-source merges.
          </p>
        </div>
        <div className="tabs">
          <button
            type="button"
            className={`tab-btn ${builderTab === "synth" ? "active" : ""}`}
            onClick={() => setBuilderTab("synth")}
          >
            1. Synthetic Generator
          </button>
          <button
            type="button"
            className={`tab-btn ${builderTab === "captures" ? "active" : ""}`}
            onClick={() => setBuilderTab("captures")}
          >
            2. From Camera Captures
          </button>
          <button
            type="button"
            className={`tab-btn ${builderTab === "merge" ? "active" : ""}`}
            onClick={() => setBuilderTab("merge")}
          >
            3. Merge Datasets
          </button>
        </div>
      </div>

      {/* Builder Forms */}
      {builderTab === "synth" && (
        <form onSubmit={handleSynth} className="card">
          <div className="card-header">
            <div>
              <h2>Generate Synthetic YOLO-Pose Dataset</h2>
              <p className="small" style={{ margin: 0 }}>
                Renders randomized payloads, constellations &amp; finders through perspective homographies, screen glare, motion blur, and sensor noise.
              </p>
            </div>
            <Sparkles size={18} color="#22d3ee" />
          </div>

          <div className="grid-4">
            <label className="field">
              <span>Dataset Name</span>
              <input
                value={synthName}
                onChange={(e) => setSynthName(e.target.value)}
                required
              />
            </label>
            <label className="field">
              <span>Train Frames</span>
              <input
                type="number"
                min={8}
                max={5000}
                value={nTrain}
                onChange={(e) => setNTrain(Number(e.target.value) || 64)}
              />
            </label>
            <label className="field">
              <span>Validation Frames</span>
              <input
                type="number"
                min={4}
                max={1000}
                value={nVal}
                onChange={(e) => setNVal(Number(e.target.value) || 16)}
              />
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

          <div className="grid-2 mt">
            <label className="field">
              <span>Channel Impairment Ceiling ({level.toFixed(2)})</span>
              <input
                type="range"
                min={0.1}
                max={0.95}
                step={0.05}
                value={level}
                onChange={(e) => setLevel(Number(e.target.value))}
              />
            </label>
            <label className="field">
              <span>RNG Seed</span>
              <input
                type="number"
                value={seed}
                onChange={(e) => setSeed(Number(e.target.value) || 42)}
              />
            </label>
          </div>

          <div className="row spread mt" style={{ marginTop: 16 }}>
            <div className="row" style={{ gap: 6 }}>
              <span className="small">Quick presets:</span>
              <button
                type="button"
                className="ghost sm"
                onClick={() => {
                  setSynthName("smoke-fast");
                  setNTrain(48);
                  setNVal(12);
                }}
              >
                Fast Smoke (60)
              </button>
              <button
                type="button"
                className="ghost sm"
                onClick={() => {
                  setSynthName("standard-pose");
                  setNTrain(200);
                  setNVal(40);
                }}
              >
                Standard (240)
              </button>
              <button
                type="button"
                className="ghost sm"
                onClick={() => {
                  setSynthName("production-hd");
                  setNTrain(800);
                  setNVal(160);
                }}
              >
                Production (960)
              </button>
            </div>
            <button type="submit" disabled={busy}>
              <Sparkles size={15} />
              {busy ? "Queueing…" : `Synthesize ${nTrain + nVal} Frames`}
            </button>
          </div>
        </form>
      )}

      {builderTab === "captures" && (
        <form onSubmit={handleImportCaptures} className="card">
          <div className="card-header">
            <div>
              <h2>Convert Camera Capture Sessions to YOLO-Pose</h2>
              <p className="small" style={{ margin: 0 }}>
                Imports accepted camera photos from <code>DATA_DIR/sessions</code> so you can inspect or refine keypoints in Pose Studio.
              </p>
            </div>
            <Layers size={18} color="#10b981" />
          </div>

          <div className="grid-3">
            <label className="field">
              <span>Output Dataset Name</span>
              <input
                value={capName}
                onChange={(e) => setCapName(e.target.value)}
                required
              />
            </label>
            <label className="field">
              <span>Validation Split Fraction</span>
              <input
                type="number"
                step={0.05}
                min={0.05}
                max={0.5}
                value={valFrac}
                onChange={(e) => setValFrac(Number(e.target.value) || 0.2)}
              />
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

          <div className="row spread mt">
            <label className="row small" style={{ gap: 8, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={verifiedOnly}
                onChange={(e) => setVerifiedOnly(e.target.checked)}
                style={{ width: 16 }}
              />
              <span>Only include shots with verified payload decode</span>
            </label>

            <span className="small">
              Active capture sessions found: <code>{captures.length}</code> ({" "}
              <Link to="/capture">Capture Station</Link> )
            </span>
          </div>

          <div className="row mt" style={{ justifyContent: "flex-end" }}>
            <button type="submit" disabled={busy}>
              <Layers size={15} /> Import Captures
            </button>
          </div>
        </form>
      )}

      {builderTab === "merge" && (
        <form onSubmit={handleMerge} className="card">
          <div className="card-header">
            <div>
              <h2>Merge Datasets (Synthetic + Real Domain Mix)</h2>
              <p className="small" style={{ margin: 0 }}>
                Combine synthetic channel coverage with real display/camera shots into a single unified <code>dataset.yaml</code>.
              </p>
            </div>
            <GitMerge size={18} color="#8b5cf6" />
          </div>

          <div className="grid-2">
            <label className="field">
              <span>Merged Dataset Name</span>
              <input
                value={mergeName}
                onChange={(e) => setMergeName(e.target.value)}
                required
              />
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

          <div className="mt">
            <span className="small" style={{ display: "block", marginBottom: 6 }}>
              Select 2 or More Source Datasets ({mergeSources.length} chosen)
            </span>
            <div className="row" style={{ gap: 8 }}>
              {datasets.map((d) => {
                const on = mergeSources.includes(d.name);
                return (
                  <button
                    key={d.name}
                    type="button"
                    className={`sm ${on ? "" : "secondary"}`}
                    onClick={() =>
                      toggleArr(mergeSources, d.name, setMergeSources)
                    }
                  >
                    {d.name} ({d.images} imgs)
                  </button>
                );
              })}
            </div>
          </div>

          <div className="row mt" style={{ justifyContent: "flex-end" }}>
            <button type="submit" disabled={busy || mergeSources.length < 2}>
              <GitMerge size={15} /> Merge Selected Datasets
            </button>
          </div>
        </form>
      )}

      {/* Live Job Banner */}
      {jobSnap && (
        <div className="card" style={{ borderLeft: "3px solid var(--accent)" }}>
          <div className="row spread" style={{ marginBottom: 8 }}>
            <div className="row" style={{ gap: 8 }}>
              <span
                className={`pill ${
                  jobSnap.live
                    ? "run"
                    : jobSnap.status === "ok"
                    ? "ok"
                    : "bad"
                }`}
              >
                {jobSnap.status}
              </span>
              <strong>{jobSnap.title}</strong>
            </div>
            <span className="mono small">
              {Math.round((jobSnap.progress || 0) * 100)}%
            </span>
          </div>
          <div className="progress-track">
            <div
              className="progress-fill"
              style={{ width: `${Math.round((jobSnap.progress || 0) * 100)}%` }}
            />
          </div>
          {jobSnap.message && (
            <p className="small mt" style={{ marginBottom: 0 }}>
              {jobSnap.message}
            </p>
          )}
        </div>
      )}

      {/* Existing Datasets Table */}
      <div className="card">
        <div className="card-header">
          <h2>Available Datasets ({datasets.length})</h2>
        </div>

        {review && <DatasetReview key={review} name={review} onClose={() => setReview(null)} />}
        {gallery && <DatasetGallery key={gallery.name} dataset={gallery} projects={projects} initialProject={projectSlug} onClose={() => setGallery(null)} onCreated={() => { setGallery(null); load(); }} />}
        {datasets.length === 0 ? (
          <div className="empty-state">
            <Database size={28} color="#22d3ee" style={{ marginBottom: 8 }} />
            <h3>No Datasets Yet</h3>
            <p className="small">
              Click <strong>Synthesize Frames</strong> above to build a calibrated training set in seconds.
            </p>
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Dataset</th>
                  <th className="num">Train</th>
                  <th className="num">Val</th>
                  <th className="num">Total</th>
                  <th>YAML</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {datasets.map((d) => (
                  <tr key={d.name}>
                    <td>
                      <strong className="mono">{d.name}</strong>
                      {d.project && (
                        <div className="small">project: {d.project}</div>
                      )}
                    </td>
                    <td className="num">{trainCount(d)}</td>
                    <td className="num">{valCount(d)}</td>
                    <td className="num">{d.images}</td>
                    <td>
                      <span className={`pill ${d.yaml ? "ok" : "warn"}`}>
                        {d.yaml ? "yaml ✓" : "missing"}
                      </span>
                    </td>
                    <td>
                      <div className="row" style={{ gap: 6 }}>
                        <button
                          type="button"
                          className="secondary sm"
                          onClick={() => {
                            setPreviewDs(d);
                            setPreviewSplit("train");
                            setPreviewIdx(0);
                          }}
                        >
                          <Eye size={13} /> Inspect
                        </button>
                        <button type="button" className="secondary sm" onClick={() => setReview(d.name)}>Check labels</button>
                        <button type="button" className="secondary sm" onClick={() => setGallery(d)}>Choose images</button>
                        <select aria-label={`Assign ${d.name} to project`} value={d.project || ""} style={{ width: 155 }} onChange={async e => {
                          const target = e.target.value;
                          if (!target) return;
                          const moving = Boolean(d.project && d.project !== target);
                          if (moving && !window.confirm(`Move ${d.name} from ${d.project} to ${target}? A dataset can belong to one project.`)) return;
                          try { await api.assignDataset(d.name, target, moving); show(`Attached ${d.name} to ${target}`); load(); }
                          catch (err) { show(err instanceof Error ? err.message : "Assignment failed", true); }
                        }}><option value="">Assign project…</option>{projects.map(p => <option key={p.slug} value={p.slug}>{p.title || p.slug}</option>)}</select>
                        <Link
                          to={`/datasets/edit/${encodeURIComponent(d.name)}`}
                          className="btn secondary sm"
                        >
                          <Edit3 size={13} /> Pose Studio
                        </Link>
                        <Link
                          to={`/train?dataset=${encodeURIComponent(d.name)}`}
                          className="btn sm"
                        >
                          <Cpu size={13} /> Train
                        </Link>
                        <a
                          href={api.datasetExportZipUrl(d.name)}
                          className="btn ghost sm"
                          title="Download dataset .zip"
                        >
                          <Download size={13} /> .zip
                        </a>
                        <button
                          type="button"
                          className="ghost sm"
                          onClick={() => handleDelete(d.name)}
                          title="Delete dataset"
                        >
                          <Trash2 size={13} color="#f87171" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Annotated Sample Preview Modal */}
      {previewDs && (
        <div className="modal-backdrop" onClick={() => setPreviewDs(null)}>
          <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <h2>Annotated Pose Sample: {previewDs.name}</h2>
                <span className="small">
                  Rendered server-side with YOLO-pose 4-corner polygon &amp; keypoint indices (TL, TR, BR, BL)
                </span>
              </div>
              <button
                type="button"
                className="ghost sm"
                onClick={() => setPreviewDs(null)}
              >
                <X size={16} />
              </button>
            </div>
            <div className="modal-body">
              <div className="row spread" style={{ marginBottom: 12 }}>
                <div className="tabs">
                  <button
                    type="button"
                    className={`tab-btn ${previewSplit === "train" ? "active" : ""}`}
                    onClick={() => {
                      setPreviewSplit("train");
                      setPreviewIdx(0);
                    }}
                  >
                    Train ({trainCount(previewDs)})
                  </button>
                  <button
                    type="button"
                    className={`tab-btn ${previewSplit === "val" ? "active" : ""}`}
                    onClick={() => {
                      setPreviewSplit("val");
                      setPreviewIdx(0);
                    }}
                  >
                    Val ({valCount(previewDs)})
                  </button>
                </div>

                <div className="row" style={{ gap: 8 }}>
                  <span className="mono small">
                    Sample #{previewIdx + 1} / {maxPreviewIdx + 1}
                  </span>
                  <input
                    type="range"
                    min={0}
                    max={maxPreviewIdx}
                    value={previewIdx}
                    onChange={(e) => setPreviewIdx(Number(e.target.value))}
                    style={{ width: 160 }}
                  />
                </div>
              </div>

              <div className="optical-frame-stage" style={{ minHeight: 380 }}>
                <img
                  crossOrigin="use-credentials" src={api.datasetPreviewUrl(
                    previewDs.name,
                    previewSplit,
                    previewIdx
                  )}
                  alt="Annotated dataset sample"
                  style={{ maxHeight: 460, imageRendering: "auto" }}
                />
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
