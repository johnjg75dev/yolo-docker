import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useBlocker, useParams, useSearchParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  Crosshair,
  Plus,
  Save,
  Trash2,
} from "lucide-react";
import { CropTools } from "./CropTools";
import { api } from "../api/client";
import { useToast } from "../components/Toast";

interface KeypointPt {
  x: number;
  y: number;
  v: number;
}

const CORNER_NAMES = ["0: TL", "1: TR", "2: BR", "3: BL"];
const CORNER_COLORS = ["#22d3ee", "#10b981", "#f59e0b", "#ec4899"];

export function DatasetEditor() {
  const { name } = useParams<{ name: string }>();
  const { show } = useToast();
  const [searchParams] = useSearchParams();
  const initialIndex = Math.max(0, Number.parseInt(searchParams.get("index") || "0", 10) || 0);
  const initialImage = searchParams.get("image");
  const [split, setSplit] = useState<"train" | "val">(searchParams.get("split") === "val" ? "val" : "train");
  const [images, setImages] = useState<string[]>([]);
  const [idx, setIdx] = useState(0);
  const [offset, setOffset] = useState(Math.floor(initialIndex / 200) * 200);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [labelError, setLabelError] = useState("");
  const [loadedText, setLoadedText] = useState("");
  const [undo, setUndo] = useState<KeypointPt[][]>([]);
  const [redo, setRedo] = useState<KeypointPt[][]>([]);
  const [keypoints, setKeypoints] = useState<KeypointPt[]>([]);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [revision, setRevision] = useState(0);
  const [dragIdx, setDragIdx] = useState<number | null>(null);

  const stageRef = useRef<HTMLDivElement | null>(null);
  const pageIndexRef = useRef(initialIndex % 200);
  const pendingImage = useRef(initialImage);
  const currentImg = images[idx] || null;
  const baseUrl = currentImg && name ? api.datasetImageUrl(name, split, currentImg) : "";
  const imageUrl = baseUrl + (baseUrl.includes("?") ? "&" : "?") + `revision=${revision}`;

  useEffect(() => {
    if (!name) return;
    let active = true;
    setLoading(true); setImages([]); setKeypoints([]);
    api.datasetImages(name, split, 200, offset).then(r => {
      if (active) { setImages(r.images || []); setTotal(r.total); const target = pageIndexRef.current; if (pendingImage.current && r.images[target] !== pendingImage.current) { show("The dataset changed since the check. Recheck labels to find this image.", true); setImages([]); } else setIdx(Math.min(target, Math.max(0, r.images.length - 1))); pendingImage.current = null; pageIndexRef.current = 0; }
    }).catch(e => { if (active) show(e instanceof Error ? e.message : "Failed to list images", true); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [name, split, offset, show]);

  useEffect(() => {
    if (!name || !currentImg) return;
    let active = true;
    setLoading(true); setKeypoints([]); setLabelError(""); setUndo([]); setRedo([]);
    api.datasetLabel(name, split, currentImg).then(r => {
      if (!active) return;
      setLoadedText(r.text);
      const rows = r.text.trim() ? r.text.trim().split(/\r?\n/) : [];
      const fields = rows[0]?.trim().split(/\s+/) || [];
      if (rows.length && (rows.length !== 1 || fields.length !== 17 || fields[0] !== "0" || r.keypoints.length !== 4 || r.keypoints.some(k => !Number.isFinite(k.x) || !Number.isFinite(k.y) || k.x < 0 || k.x > 1 || k.y < 0 || k.y > 1 || ![0, 1, 2].includes(k.v)))) {
        setLabelError("This label has multiple objects or an unsupported format. Editing is disabled to protect it.");
      } else setKeypoints(r.keypoints);
      setDirty(false);
    }).catch(e => { if (active) setLabelError(e instanceof Error ? `Could not load label: ${e.message}` : "Could not load label"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [name, split, currentImg, revision]);

  const confirmLeave = useCallback(() => !dirty || window.confirm("Discard unsaved pose changes?"), [dirty]);
  const blocker = useBlocker(dirty || saving);
  useEffect(() => {
    if (blocker.state !== "blocked") return;
    if (saving) { blocker.reset(); return; }
    if (window.confirm("Discard unsaved pose changes?")) blocker.proceed();
    else blocker.reset();
  }, [blocker, saving]);
  useEffect(() => {
    if (!dirty && !saving) return;
    const before = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", before);
    return () => window.removeEventListener("beforeunload", before);
  }, [dirty, saving]);
  const remember = useCallback(() => { setUndo(u => [...u.slice(-49), keypoints]); setRedo([]); }, [keypoints]);
  const navigate = useCallback((direction: number) => {
    if (loading || saving || !confirmLeave()) return;
    if (direction < 0 && idx === 0 && offset > 0) { pageIndexRef.current = 199; setOffset(Math.max(0, offset - 200)); return; }
    if (direction > 0 && idx === images.length - 1 && offset + images.length < total) { setOffset(offset + 200); return; }
    setIdx(i => Math.max(0, Math.min(images.length - 1, i + direction)));
  }, [loading, saving, confirmLeave, idx, offset, images.length, total]);

  const handleSave = useCallback(async () => {
    if (!name || !currentImg || loading || labelError || saving || !dirty) return;
    setSaving(true);
    try {
      if (keypoints.length === 4) {
        const result = await api.saveDatasetLabel(name, split, currentImg, { keypoints, expected_text: loadedText });
        setLoadedText(result.text);
      } else {
        const result = await api.saveDatasetLabel(name, split, currentImg, { keypoints: [], expected_text: loadedText });
        setLoadedText(result.text);
      }
      setDirty(false);
      setRevision(r => r + 1);
      show(`Saved pose keypoints for ${currentImg}`);
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Save failed", true);
    } finally {
      setSaving(false);
    }
  }, [name, split, currentImg, keypoints, show, loading, labelError, saving, dirty, loadedText]);

  const placeDefaultQuad = useCallback(() => {
    if (loading || labelError || saving) return;
    remember();
    setKeypoints([
      { x: 0.22, y: 0.22, v: 2 },
      { x: 0.78, y: 0.22, v: 2 },
      { x: 0.78, y: 0.78, v: 2 },
      { x: 0.22, y: 0.78, v: 2 },
    ]);
    setDirty(true);
  }, [loading, labelError, saving, remember]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLButtonElement) return;
      if (e.key === "ArrowLeft") {
        navigate(-1);
      } else if (e.key === "ArrowRight") {
        navigate(1);
      } else if (e.key.toLowerCase() === "s") {
        e.preventDefault();
        handleSave();
      } else if (e.key.toLowerCase() === "n") {
        e.preventDefault();
        placeDefaultQuad();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate, handleSave, placeDefaultQuad]);

  const handlePointerMove = (e: React.PointerEvent) => {
    if (dragIdx === null || !stageRef.current || saving) return;
    const rect = stageRef.current.getBoundingClientRect();
    const nx = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const ny = Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height));
    setKeypoints((prev) =>
      prev.map((kp, i) => (i === dragIdx ? { x: nx, y: ny, v: 2 } : kp))
    );
    setDirty(true);
  };

  if (!name) return null;

  const hasQuad = keypoints.length === 4;
  const polyPoints = hasQuad
    ? keypoints.map((k) => `${k.x * 1000},${k.y * 1000}`).join(" ")
    : "";
  const minX = hasQuad ? Math.min(...keypoints.map((k) => k.x)) : 0;
  const maxX = hasQuad ? Math.max(...keypoints.map((k) => k.x)) : 0;
  const minY = hasQuad ? Math.min(...keypoints.map((k) => k.y)) : 0;
  const maxY = hasQuad ? Math.max(...keypoints.map((k) => k.y)) : 0;

  return (
    <div className="wrap">
      <div className="page-header">
        <div>
          <div className="page-eyebrow">
            <Crosshair size={13} /> 4-Corner YOLO-Pose Keypoint Studio
          </div>
          <h1>Pose Studio: {name}</h1>
          <p>
            Drag the 4 ordered corner handles (
            <code>0:TL → 1:TR → 2:BR → 3:BL</code>) to align the optical frame homography. Shortcuts:{" "}
            <kbd>←</kbd> <kbd>→</kbd> navigate · <kbd>N</kbd> place quad ·{" "}
            <kbd>S</kbd> save.
          </p>
        </div>
        <div className="row">
          <Link to="/datasets" className="btn secondary">
            <ArrowLeft size={14} /> Datasets
          </Link>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || !currentImg || loading || !!labelError || !dirty}
          >
            <Save size={15} />
            {saving ? "Saving…" : dirty ? "Save Changes *" : "Saved"}
          </button>
        </div>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 1fr) 310px",
          gap: 16,
          alignItems: "start",
        }}
      >
        {/* Main Interactive Pose Canvas */}
        <div className="card" style={{ marginBottom: 0 }}>
          <div className="row spread" style={{ marginBottom: 12 }}>
            <div className="tabs">
              <button
                type="button"
                className={`tab-btn ${split === "train" ? "active" : ""}`}
                disabled={loading || saving}
                onClick={() => { if (split !== "train" && !loading && !saving && confirmLeave()) { setOffset(0); setSplit("train"); } }}
              >
                Train Split
              </button>
              <button
                type="button"
                className={`tab-btn ${split === "val" ? "active" : ""}`}
                disabled={loading || saving}
                onClick={() => { if (split !== "val" && !loading && !saving && confirmLeave()) { setOffset(0); setSplit("val"); } }}
              >
                Val Split
              </button>
            </div>

            <div className="row" style={{ gap: 8 }}>
              <button
                type="button"
                className="secondary sm"
                disabled={loading || saving || (idx <= 0 && offset === 0)}
                onClick={() => navigate(-1)}
              >
                <ArrowLeft size={14} /> Prev
              </button>
              <span className="mono small">
                {images.length ? `${offset + idx + 1} / ${total}` : "0 / 0"}
              </span>
              {total > 200 && <label className="small">Page <select aria-label="Image page" value={Math.floor(offset / 200)} disabled={loading || saving} onChange={e => { if (confirmLeave()) { pageIndexRef.current = 0; setOffset(Number(e.target.value) * 200); } }}>
                {Array.from({ length: Math.ceil(total / 200) }, (_, page) => <option key={page} value={page}>{page + 1}</option>)}
              </select></label>}
              <button
                type="button"
                className="secondary sm"
                disabled={loading || saving || offset + idx >= total - 1}
                onClick={() => navigate(1)}
              >
                Next <ArrowRight size={14} />
              </button>
            </div>
          </div>

          <div className="row small" style={{ marginBottom: 10 }}>
            <label htmlFor="pose-zoom">Zoom: {zoom.toFixed(1)}×</label>
            <input id="pose-zoom" type="range" min="1" max="4" step="0.25" value={zoom} onChange={e => setZoom(Number(e.target.value))} />
            <button type="button" className="secondary sm" onClick={() => setZoom(1)}>Reset</button>
          </div>
          {currentImg ? (
            <div
              style={{
                overflow: "auto",
                maxHeight: 600,
                background: "#05070c",
                borderRadius: 10,
                padding: 12,
                border: "1px solid var(--border)",
              }}
            >
              <div
                ref={stageRef}
                onPointerMove={handlePointerMove}
                onPointerUp={() => setDragIdx(null)}
                onPointerCancel={() => setDragIdx(null)}
                style={{
                  position: "relative",
                  display: "block",
                  width: `${zoom * 100}%`,
                  userSelect: "none",
                  touchAction: "none",
                }}
              >
                <img
                  src={imageUrl}
                  alt={currentImg}
                  draggable={false}
                  style={{
                    display: "block",
                    width: "100%",
                    height: "auto",
                    borderRadius: 4,
                  }}
                />

                {hasQuad && (
                  <svg
                    viewBox="0 0 1000 1000"
                    preserveAspectRatio="none"
                    style={{
                      position: "absolute",
                      inset: 0,
                      width: "100%",
                      height: "100%",
                      pointerEvents: "none",
                    }}
                  >
                    <rect
                      x={minX * 1000}
                      y={minY * 1000}
                      width={(maxX - minX) * 1000}
                      height={(maxY - minY) * 1000}
                      fill="none"
                      stroke="#64748b"
                      strokeWidth="1.5"
                      strokeDasharray="6 6"
                    />
                    <polygon
                      points={polyPoints}
                      fill="rgba(34, 211, 238, 0.14)"
                      stroke="#22d3ee"
                      strokeWidth="3"
                    />
                    <line
                      x1={keypoints[0].x * 1000}
                      y1={keypoints[0].y * 1000}
                      x2={keypoints[1].x * 1000}
                      y2={keypoints[1].y * 1000}
                      stroke="#f59e0b"
                      strokeWidth="4.5"
                    />
                  </svg>
                )}

                {dragIdx !== null && keypoints[dragIdx] && <div aria-label="Keypoint magnifier" style={{ position: "absolute", right: 8, top: 8, width: 130, height: 130, borderRadius: "50%", border: "3px solid white", boxShadow: "0 2px 8px #000", pointerEvents: "none", backgroundImage: `url(${JSON.stringify(imageUrl)})`, backgroundRepeat: "no-repeat", backgroundSize: `${(stageRef.current?.clientWidth || 1) * 3}px ${(stageRef.current?.clientHeight || 1) * 3}px`, backgroundPosition: `${65 - keypoints[dragIdx].x * (stageRef.current?.clientWidth || 1) * 3}px ${65 - keypoints[dragIdx].y * (stageRef.current?.clientHeight || 1) * 3}px`, zIndex: 3 }}><span style={{ position: "absolute", left: "50%", top: "50%", transform: "translate(-50%, -50%)", color: "red", fontSize: 24, textShadow: "0 0 2px white" }}>+</span></div>}

                {hasQuad && !labelError && !loading && !saving &&
                  keypoints.map((kp, ki) => (
                    <div
                      key={ki}
                      onPointerDown={(e) => {
                        e.stopPropagation();
                        (e.currentTarget as HTMLElement).setPointerCapture(
                          e.pointerId
                        );
                        remember();
                        setDragIdx(ki);
                      }}
                      role="slider" tabIndex={0} aria-label={`${CORNER_NAMES[ki]} keypoint; use arrow keys to move`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(kp.x * 100)}
                      onKeyDown={e => { const delta = e.shiftKey ? 0.01 : 0.001; const dx = e.key === "ArrowLeft" ? -delta : e.key === "ArrowRight" ? delta : 0; const dy = e.key === "ArrowUp" ? -delta : e.key === "ArrowDown" ? delta : 0; if (!dx && !dy) return; e.preventDefault(); e.stopPropagation(); remember(); setKeypoints(prev => prev.map((p, i) => i === ki ? { ...p, x: Math.max(0, Math.min(1, p.x + dx)), y: Math.max(0, Math.min(1, p.y + dy)), v: 2 } : p)); setDirty(true); }}
                      title={`${CORNER_NAMES[ki]} (${kp.x.toFixed(3)}, ${kp.y.toFixed(
                        3
                      )})`}
                      style={{
                        position: "absolute",
                        left: `${kp.x * 100}%`,
                        top: `${kp.y * 100}%`,
                        transform: "translate(-50%, -50%)",
                        width: 20,
                        height: 20,
                        borderRadius: "50%",
                        background: CORNER_COLORS[ki],
                        border: "2px solid #05070c",
                        boxShadow: "0 2px 6px rgba(0,0,0,0.7)",
                        cursor: "grab",
                        display: "grid",
                        placeItems: "center",
                        fontSize: 10,
                        fontWeight: 800,
                        color: "#05070c",
                        fontFamily: "var(--font-mono)",
                      }}
                    >
                      {ki}
                    </div>
                  ))}
              </div>
            </div>
          ) : (
            <div className="empty-state">
              <p className="small">No images in the {split} split.</p>
            </div>
          )}
        </div>

        {/* Right Inspector Sidebar */}
        <div className="card" style={{ marginBottom: 0 }}>
          <div className="row spread" style={{ marginBottom: 12 }}>
            <h3>Pose Keypoints</h3>
            {!hasQuad ? (
              <button
                type="button"
                className="secondary sm"
                disabled={loading || saving || !!labelError || !currentImg}
                onClick={placeDefaultQuad}
              >
                <Plus size={14} /> Place Quad
              </button>
            ) : (
              <button
                type="button"
                className="ghost sm"
                disabled={loading || saving || !!labelError}
                onClick={() => {
                  remember();
                  setKeypoints([]);
                  setDirty(true);
                }}
              >
                <Trash2 size={14} color="#f87171" /> Clear
              </button>
            )}
          </div>

          {currentImg && (
            <div className="small mono" style={{ marginBottom: 12 }}>
              File: {currentImg}
            </div>
          )}

          {labelError && <p role="alert" style={{ color: "var(--err)" }}>{labelError}</p>}
          <div className="row" style={{ marginBottom: 10 }}><button type="button" className="secondary sm" disabled={!undo.length || loading || saving || !!labelError} onClick={() => { setRedo(r => [...r, keypoints]); setKeypoints(undo[undo.length - 1]); setUndo(u => u.slice(0, -1)); setDirty(true); }}>Undo</button><button type="button" className="secondary sm" disabled={!redo.length || loading || saving || !!labelError} onClick={() => { setUndo(u => [...u, keypoints]); setKeypoints(redo[redo.length - 1]); setRedo(r => r.slice(0, -1)); setDirty(true); }}>Redo</button></div>
          {!hasQuad && !labelError && !loading ? (
            <p className="small">
              Negative / background frame (no label). Click <strong>Place Quad</strong> to annotate 4 corners.
            </p>
          ) : (
            <div className="card-subtle">
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {keypoints.map((kp, ki) => (
                  <div
                    key={ki}
                    className="row spread"
                    style={{
                      fontSize: 12,
                      fontFamily: "var(--font-mono)",
                    }}
                  >
                    <span className="row" style={{ gap: 6 }}>
                      <span
                        style={{
                          width: 9,
                          height: 9,
                          borderRadius: "50%",
                          background: CORNER_COLORS[ki],
                          display: "inline-block",
                        }}
                      />
                      <strong>{CORNER_NAMES[ki]}</strong>
                    </span>
                    <span>
                      ({kp.x.toFixed(3)}, {kp.y.toFixed(3)})
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {currentImg && <CropTools name={name} split={split} filename={currentImg} imageUrl={imageUrl} dirty={dirty || loading || !!labelError} keypoints={keypoints} onChanged={() => setRevision(r => r + 1)} />}

          <div className="callout mt" style={{ fontSize: 12 }}>
            <strong>Orientation Convention:</strong> The amber edge connects{" "}
            <code>0:TL → 1:TR</code> (top edge of the optical symbol frame) so homography rectification stays upright.
          </div>
        </div>
      </div>
    </div>
  );
}
