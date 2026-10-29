import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  CheckCircle2,
  Cpu,
  Database,
  Eye,
  FolderKanban,
  Plus,
  Radio,
  Trash2,
  X,
} from "lucide-react";
import { api } from "../api/client";
import { useToast } from "../components/Toast";
import type { DatasetSummary, ProjectItem } from "../types";

const STATUSES = ["development", "finished", "archived"] as const;

export function Projects() {
  const { show } = useToast();
  const [projects, setProjects] = useState<ProjectItem[]>([]);
  const [filter, setFilter] = useState<string>("all");
  const [showCreate, setShowCreate] = useState(false);

  // Create form
  const [title, setTitle] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [status, setStatus] = useState<string>("development");
  const [tagsText, setTagsText] = useState("optical, yolo26, s2c");
  const [busy, setBusy] = useState(false);
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [existingDataset, setExistingDataset] = useState("");

  // Detail modal
  const [detail, setDetail] = useState<any | null>(null);

  const load = useCallback(async () => {
    try {
      const list = await api.listProjects();
      setProjects(list);
      api.listDatasets().then(setDatasets).catch(() => {});
      if (list.length === 0) setShowCreate(true);
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Failed to load projects", true);
    }
  }, [show]);

  useEffect(() => {
    load();
  }, [load]);

  const autoSlug = (val: string) =>
    val
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 48);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const finalSlug = (slug || autoSlug(title)).trim();
    if (!finalSlug) {
      show("Provide a project title or slug", true);
      return;
    }
    setBusy(true);
    try {
      const tags = tagsText
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean);
      await api.createProject({
        slug: finalSlug,
        title: title.trim() || finalSlug,
        description: description.trim(),
        status,
        tags,
      });
      let attachmentError = "";
      if (existingDataset) {
        try { await api.assignDataset(existingDataset, finalSlug); }
        catch (e) { attachmentError = e instanceof Error ? e.message : "unknown error"; }
      }
      setExistingDataset("");
      if (attachmentError) show(`Project created, but dataset could not be attached: ${attachmentError}`, true);
      else show(`Project "${finalSlug}" created`);
      setTitle("");
      setSlug("");
      setDescription("");
      setShowCreate(false);
      await load();
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Create failed", true);
    } finally {
      setBusy(false);
    }
  };

  const handleStatusChange = async (p: ProjectItem, nextStatus: string) => {
    try {
      await api.updateProject(p.slug, { status: nextStatus });
      show(`${p.slug} → ${nextStatus}`);
      await load();
      if (detail?.project?.slug === p.slug) {
        const d = await api.getProject(p.slug);
        setDetail(d);
      }
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Update failed", true);
    }
  };

  const handleDelete = async (p: ProjectItem) => {
    if (
      !confirm(
        `Delete project "${p.slug}"? Underlying dataset/model files remain intact.`
      )
    ) {
      return;
    }
    try {
      await api.deleteProject(p.slug);
      show(`Deleted project ${p.slug}`);
      if (detail?.project?.slug === p.slug) setDetail(null);
      await load();
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Delete failed", true);
    }
  };

  const openDetail = async (slugToOpen: string) => {
    try {
      const d = await api.getProject(slugToOpen);
      setDetail(d);
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Failed to inspect project", true);
    }
  };

  const shown = projects.filter((p) => filter === "all" || p.status === filter);

  const statusPillClass = (st: string) => {
    if (st === "finished") return "ok";
    if (st === "development") return "run";
    return "";
  };

  return (
    <div className="wrap">
      <div className="page-header">
        <div>
          <div className="page-eyebrow">
            <FolderKanban size={13} /> Stage 1 · Experiment Organization
          </div>
          <h1>Projects</h1>
          <p>
            Group synthetic/captured datasets, YOLO26-pose runs, and S2C optical transfers under versioned lifecycle milestones.
          </p>
        </div>
        <div className="row">
          <div className="tabs">
            {["all", ...STATUSES].map((st) => (
              <button
                key={st}
                type="button"
                className={`tab-btn ${filter === st ? "active" : ""}`}
                onClick={() => setFilter(st)}
                style={{ textTransform: "capitalize" }}
              >
                {st}
              </button>
            ))}
          </div>
          <button type="button" onClick={() => setShowCreate((v) => !v)}>
            <Plus size={15} />
            {showCreate ? "Hide Form" : "New Project"}
          </button>
        </div>
      </div>

      {showCreate && (
        <form onSubmit={handleCreate} className="card">
          <div className="card-header">
            <h2>Create Optical Modem Project</h2>
          </div>
          <div className="grid-3">
            <label className="field">
              <span>Project Title</span>
              <input
                value={title}
                onChange={(e) => {
                  setTitle(e.target.value);
                  if (!slug) setSlug(autoSlug(e.target.value));
                }}
                placeholder="e.g. Handheld RGB8 v0.3"
                required
              />
            </label>
            <label className="field">
              <span>Slug Identifier</span>
              <input
                value={slug}
                onChange={(e) => setSlug(autoSlug(e.target.value))}
                placeholder="handheld-rgb8-v03"
              />
            </label>
            <label className="field">
              <span>Lifecycle Stage</span>
              <select value={status} onChange={(e) => setStatus(e.target.value)}>
                {STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="grid-2 mt">
            <label className="field">
              <span>Notes / Hypothesis</span>
              <input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Target display distance, lighting conditions, modulation goals…"
              />
            </label>
            <label className="field">
              <span>Tags (comma separated)</span>
              <input
                value={tagsText}
                onChange={(e) => setTagsText(e.target.value)}
                placeholder="optical, 1080p, glare"
              />
            </label>
          </div>

          <label className="field mt"><span>Add an existing unassigned dataset (optional)</span>
            <select value={existingDataset} onChange={e => setExistingDataset(e.target.value)}><option value="">None — choose later</option>{datasets.filter(d => !d.project && !d.missing).map(d => <option key={d.name} value={d.name}>{d.name} ({d.images} images)</option>)}</select>
          </label>
          <div className="row mt" style={{ justifyContent: "flex-end" }}>
            <button type="submit" disabled={busy}>
              <CheckCircle2 size={15} />
              {busy ? "Creating…" : "Create Project"}
            </button>
          </div>
        </form>
      )}

      {shown.length === 0 ? (
        <div className="card empty-state">
          <FolderKanban size={28} color="#22d3ee" style={{ marginBottom: 8 }} />
          <h3>No Matching Projects</h3>
          <p className="small">
            Create a project above to organize datasets, training runs, and optical transfers.
          </p>
        </div>
      ) : (
        <div className="grid-2">
          {shown.map((p) => (
            <div key={p.slug} className="card" style={{ marginBottom: 0 }}>
              <div className="row spread" style={{ alignItems: "flex-start" }}>
                <div>
                  <div className="row" style={{ gap: 8 }}>
                    <h2 style={{ marginBottom: 2 }}>{p.title || p.slug}</h2>
                    <span className={`pill ${statusPillClass(p.status)}`}>
                      {p.status}
                    </span>
                  </div>
                  <code style={{ fontSize: 11 }}>{p.slug}</code>
                </div>
                <div className="row" style={{ gap: 6 }}>
                  <button
                    type="button"
                    className="secondary sm"
                    onClick={() => openDetail(p.slug)}
                  >
                    <Eye size={14} /> Artefacts
                  </button>
                  <button
                    type="button"
                    className="ghost sm"
                    onClick={() => handleDelete(p)}
                    title="Delete project"
                  >
                    <Trash2 size={14} color="#f87171" />
                  </button>
                </div>
              </div>

              {p.description && (
                <p className="small" style={{ marginTop: 10, marginBottom: 10 }}>
                  {p.description}
                </p>
              )}

              {p.tags && p.tags.length > 0 && (
                <div className="row" style={{ gap: 6, marginTop: 8 }}>
                  {p.tags.map((t: string) => (
                    <span key={t} className="pill" style={{ fontSize: 11 }}>
                      #{t}
                    </span>
                  ))}
                </div>
              )}

              <div
                className="row spread mt"
                style={{
                  paddingTop: 12,
                  borderTop: "1px solid var(--border)",
                }}
              >
                <div className="row" style={{ gap: 8 }}>
                  <Link
                    to={`/datasets?project=${encodeURIComponent(p.slug)}`}
                    className="btn ghost sm"
                  >
                    <Database size={13} /> + Dataset
                  </Link>
                  <Link
                    to={`/train?project=${encodeURIComponent(p.slug)}`}
                    className="btn ghost sm"
                  >
                    <Cpu size={13} /> + Train
                  </Link>
                </div>

                <select
                  value={p.status}
                  onChange={(e) => handleStatusChange(p, e.target.value)}
                  style={{ width: 145, padding: "4px 8px", fontSize: 12 }}
                  aria-label="Lifecycle status"
                >
                  {STATUSES.map((s) => (
                    <option key={s} value={s}>
                      Stage: {s}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Project Artefacts Modal */}
      {detail && (
        <div className="modal-backdrop" onClick={() => setDetail(null)}>
          <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <h2>{detail.project?.title || detail.project?.slug}</h2>
                <span className="small mono">
                  slug: {detail.project?.slug} · status: {detail.project?.status}
                </span>
              </div>
              <button
                type="button"
                className="ghost sm"
                onClick={() => setDetail(null)}
              >
                <X size={16} />
              </button>
            </div>
            <div className="modal-body">
              <div className="grid-3" style={{ marginBottom: 16 }}>
                <div className="kpi-card">
                  <div className="kpi-label">
                    <span>Datasets</span> <Database size={14} />
                  </div>
                  <div className="kpi-value">{(detail.datasets || []).length}</div>
                </div>
                <div className="kpi-card">
                  <div className="kpi-label">
                    <span>Runs</span> <Cpu size={14} />
                  </div>
                  <div className="kpi-value">{(detail.runs || []).length}</div>
                </div>
                <div className="kpi-card">
                  <div className="kpi-label">
                    <span>S2C Transfers</span> <Radio size={14} />
                  </div>
                  <div className="kpi-value">{(detail.transfers || []).length}</div>
                </div>
              </div>

              <h4>Linked Datasets</h4>
              {(detail.datasets || []).length === 0 ? (
                <p className="small">No datasets tagged with this project yet.</p>
              ) : (
                <ul className="small">
                  {detail.datasets.map((d: any) => (
                    <li key={d.name}>
                      <code>{d.name}</code> ({d.source || d.kind || "dataset"}) —{" "}
                      {d.images ?? d.n_images ?? 0} images
                    </li>
                  ))}
                </ul>
              )}

              <h4 className="mt">Linked Training Runs</h4>
              {(detail.runs || []).length === 0 ? (
                <p className="small">No runs tagged with this project yet.</p>
              ) : (
                <ul className="small">
                  {detail.runs.map((r: any) => (
                    <li key={r.name}>
                      <code>{r.name}</code> ({r.status || "ready"}) — best mAP:{" "}
                      {r.mAP ?? r.best_map50_95 ?? "—"}
                    </li>
                  ))}
                </ul>
              )}

              <h4 className="mt">Linked Optical Transfers</h4>
              {(detail.transfers || []).length === 0 ? (
                <p className="small">No transfers tagged with this project yet.</p>
              ) : (
                <ul className="small">
                  {detail.transfers.map((t: any) => (
                    <li key={t.id}>
                      <code>{t.id}</code> — {t.modulation}/{t.ecc} ({t.bytes ?? t.size} B,{" "}
                      {t.complete ? "complete ✓" : "in progress"})
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
