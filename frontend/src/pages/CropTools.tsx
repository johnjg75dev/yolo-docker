import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useToast } from "../components/Toast";

type Rect = { x: number; y: number; width: number; height: number };

export function CropTools({ name, split, filename, imageUrl, dirty, keypoints, onChanged }: {
  name: string; split: string; filename: string; imageUrl: string; dirty: boolean; keypoints: { x: number; y: number; v: number }[]; onChanged: () => void;
}) {
  const { show } = useToast();
  const [info, setInfo] = useState<{ width: number; height: number; has_backup: boolean; revision: string } | null>(null);
  const [rect, setRect] = useState<Rect>({ x: 0, y: 0, width: 0, height: 0 });
  const [preview, setPreview] = useState<Rect | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    setInfo(null); setPreview(null);
    api.cropStatus(name, split, filename).then(r => {
      if (!active) return;
      setInfo(r); setRect({ x: 0, y: 0, width: r.width, height: r.height });
    }).catch(e => show(e.message, true));
    return () => { active = false; };
  }, [name, split, filename, imageUrl, show]);
  if (!info) return null;
  const valid = Object.values(rect).every(Number.isInteger) && rect.x >= 0 && rect.y >= 0 && rect.width > 0 && rect.height > 0 && rect.x + rect.width <= info.width && rect.y + rect.height <= info.height;
  const apply = async (revert: boolean) => {
    if (dirty) { show("Save or discard pose changes before cropping or reverting", true); return; }
    if (revert && !window.confirm("Restore the original image and its original label? Later edits will be lost.")) return;
    setBusy(true);
    try {
      if (revert) await api.revertImage(name, split, filename);
      else if (preview) await api.cropImage(name, split, filename, { ...preview, revision: info.revision });
      setPreview(null); onChanged(); show(revert ? "Original restored" : "Crop saved; original backed up");
    } catch (e) { show(e instanceof Error ? e.message : "Operation failed", true); }
    finally { setBusy(false); }
  };
  return <div className="card-subtle mt" style={{ padding: 12 }}>
    <h3>Crop image</h3>
    <p className="small">Original: {info.width} × {info.height} px. Crops cannot clip labels. The first original image and label are backed up.</p>
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
      {(["x", "y", "width", "height"] as const).map(k => <label key={k} className="small">{k === "x" ? "Left" : k === "y" ? "Top" : k} (px)
        <input type="number" min={0} step={1} value={rect[k]} style={{ width: "100%" }} onChange={e => { setRect({ ...rect, [k]: e.target.value === "" ? -1 : Number(e.target.value) }); setPreview(null); }} />
      </label>)}
    </div>
    <div className="row mt" style={{ flexWrap: "wrap" }}>
      <button type="button" className="secondary sm" disabled={!valid || busy} onClick={() => setPreview({ ...rect })}>Preview crop</button>
      <button type="button" className="secondary sm" disabled={!info.has_backup || busy || dirty} onClick={() => apply(true)}>Revert original</button>
    </div>
    {!valid && <p className="small">Enter whole pixel values within the image bounds.</p>}
    {preview && <div className="mt">
      <strong>Preview: {preview.width} × {preview.height} px</strong>
      <div style={{ width: "100%", aspectRatio: `${preview.width} / ${preview.height}`, marginTop: 8, backgroundImage: `url(${JSON.stringify(imageUrl)})`, backgroundSize: `${info.width / preview.width * 100}% ${info.height / preview.height * 100}%`, backgroundPosition: `${info.width === preview.width ? 0 : preview.x / (info.width - preview.width) * 100}% ${info.height === preview.height ? 0 : preview.y / (info.height - preview.height) * 100}%`, backgroundRepeat: "no-repeat", border: "1px solid var(--border)", position: "relative" }} >
        <svg viewBox={`0 0 ${preview.width} ${preview.height}`} preserveAspectRatio="none" style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}>
          {keypoints.length === 4 && <polygon points={keypoints.map(k => `${k.x * info.width - preview.x},${k.y * info.height - preview.y}`).join(" ")} fill="rgba(34,211,238,.12)" stroke="#22d3ee" vectorEffect="non-scaling-stroke" strokeWidth="2" />}
          {keypoints.filter(k => k.v > 0).map((k, i) => <circle key={i} cx={k.x * info.width - preview.x} cy={k.y * info.height - preview.y} r={Math.max(preview.width, preview.height) * .008} fill="#ffcf56" stroke="#000" vectorEffect="non-scaling-stroke" />)}
        </svg>
      </div>
      <button type="button" className="mt sm" disabled={busy || dirty} onClick={() => apply(false)}>Save this crop</button>
      {dirty && <p className="small">Save pose changes before cropping.</p>}
    </div>}
  </div>;
}
