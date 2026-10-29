import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useToast } from "../components/Toast";
import type { DatasetSummary, ProjectItem } from "../types";

const PAGE = 60;
type Split = "train" | "val";
export function DatasetGallery({ dataset, projects, initialProject, onClose, onCreated }: {
  dataset: DatasetSummary; projects: ProjectItem[]; initialProject: string; onClose: () => void; onCreated: () => void;
}) {
  const { show } = useToast();
  const [split, setSplit] = useState<Split>("train");
  const [offset, setOffset] = useState(0);
  const [images, setImages] = useState<string[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Record<Split, string[]>>({ train: [], val: [] });
  const [dest, setDest] = useState(`${dataset.name}-selected`);
  const [project, setProject] = useState(initialProject);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    setLoading(true);
    api.datasetImages(dataset.name, split, PAGE, offset).then(r => {
      if (active) { setImages(r.images); setTotal(r.total); setLoading(false); }
    }).catch(e => { if (active) { setImages([]); setLoading(false); show(e.message, true); } });
    return () => { active = false; };
  }, [dataset.name, split, offset, show]);
  const update = (fn: (current: string[]) => string[]) => setSelected(prev => ({ ...prev, [split]: fn(prev[split]) }));
  const copy = async () => {
    setBusy(true);
    try {
      const r = await api.curateDataset(dataset.name, dest.trim(), project, selected);
      show(`Created ${r.dataset} with ${r.images} images; original unchanged`);
      onCreated();
    } catch (e) { show(e instanceof Error ? e.message : "Could not create dataset", true); }
    finally { setBusy(false); }
  };
  return <div className="card" style={{ marginTop: 16 }}>
    <div className="card-header"><div><h2>Choose images from {dataset.name}</h2><p className="small">Create a separate dataset with the selected images and matching labels. The source is never changed.</p></div><button type="button" className="secondary sm" onClick={onClose}>Close gallery</button></div>
    <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
      {(["train", "val"] as Split[]).map(s => <button type="button" className={split === s ? "sm" : "secondary sm"} key={s} onClick={() => { setSplit(s); setOffset(0); }}>{s} ({selected[s].length} selected)</button>)}
      <button type="button" className="secondary sm" disabled={loading} onClick={() => update(old => Array.from(new Set([...old, ...images])))}>Select page</button>
      <button type="button" className="secondary sm" disabled={loading} onClick={() => update(old => old.filter(x => !images.includes(x)))}>Clear page</button>
      <span className="small">{total ? `${offset + 1}–${Math.min(total, offset + PAGE)} of ${total}` : "No images"}</span>
      <button type="button" className="secondary sm" disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
      <button type="button" className="secondary sm" disabled={offset + PAGE >= total || loading} onClick={() => setOffset(offset + PAGE)}>Next</button>
    </div>
    {loading ? <p className="small mt">Loading thumbnails…</p> : <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))", gap: 8, marginTop: 12 }}>
      {images.map(img => <label key={img} className="card-subtle" style={{ padding: 6, cursor: "pointer", border: selected[split].includes(img) ? "2px solid var(--accent)" : "1px solid var(--border)" }}>
        <img loading="lazy" crossOrigin="use-credentials" src={api.datasetImageUrl(dataset.name, split, img, 180)} alt="" style={{ width: "100%", height: 100, objectFit: "contain" }} />
        <span className="small" style={{ display: "flex", gap: 5, overflowWrap: "anywhere" }}><input type="checkbox" checked={selected[split].includes(img)} onChange={e => update(old => e.target.checked ? [...old, img] : old.filter(x => x !== img))} />{img}</span>
      </label>)}
    </div>}
    <div className="grid-2 mt"><label className="field"><span>New dataset name</span><input value={dest} onChange={e => setDest(e.target.value)} /></label><label className="field"><span>Project (optional)</span><select value={project} onChange={e => setProject(e.target.value)}><option value="">None</option>{projects.map(p => <option key={p.slug} value={p.slug}>{p.title || p.slug}</option>)}</select></label></div>
    <div className="row mt"><button type="button" disabled={busy || !dest.trim() || selected.train.length + selected.val.length === 0} onClick={copy}>{busy ? "Creating…" : `Create dataset (${selected.train.length + selected.val.length} selected)`}</button><span className="small">Keep both train and val images for training.</span></div>
  </div>;
}
