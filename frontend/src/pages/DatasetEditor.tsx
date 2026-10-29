import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  Crosshair,
  Plus,
  Save,
  Trash2,
} from "lucide-react";
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

  const [split, setSplit] = useState<"train" | "val">("train");
  const [images, setImages] = useState<string[]>([]);
  const [idx, setIdx] = useState(0);
  const [keypoints, setKeypoints] = useState<KeypointPt[]>([]);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dragIdx, setDragIdx] = useState<number | null>(null);

  const stageRef = useRef<HTMLDivElement | null>(null);
  const currentImg = images[idx] || null;

  useEffect(() => {
    if (!name) return;
    api
      .datasetImages(name, split)
      .then((r) => {
        setImages(r.images || []);
        setIdx(0);
      })
      .catch((e) =>
        show(
          e instanceof Error ? e.message : "Failed to list split images",
          true
        )
      );
  }, [name, split, show]);

  useEffect(() => {
    if (!name || !currentImg) {
      setKeypoints([]);
      return;
    }
    api
      .datasetLabel(name, split, currentImg)
      .then((r) => {
        const kps = (r.keypoints || []).slice(0, 4);
        setKeypoints(kps);
        setDirty(false);
      })
      .catch(() => setKeypoints([]));
  }, [name, split, currentImg]);

  const handleSave = useCallback(async () => {
    if (!name || !currentImg) return;
    setSaving(true);
    try {
      if (keypoints.length === 4) {
        await api.saveDatasetLabel(name, split, currentImg, { keypoints });
      } else {
        await api.saveDatasetLabel(name, split, currentImg, { text: "" });
      }
      setDirty(false);
      show(`Saved pose keypoints for ${currentImg}`);
    } catch (e: any) {
      show(e instanceof Error ? e.message : "Save failed", true);
    } finally {
      setSaving(false);
    }
  }, [name, split, currentImg, keypoints, show]);

  const placeDefaultQuad = useCallback(() => {
    setKeypoints([
      { x: 0.22, y: 0.22, v: 2 },
      { x: 0.78, y: 0.22, v: 2 },
      { x: 0.78, y: 0.78, v: 2 },
      { x: 0.22, y: 0.78, v: 2 },
    ]);
    setDirty(true);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return;
      if (e.key === "ArrowLeft") {
        setIdx((i) => Math.max(0, i - 1));
      } else if (e.key === "ArrowRight") {
        setIdx((i) => Math.min(Math.max(0, images.length - 1), i + 1));
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
  }, [images.length, handleSave, placeDefaultQuad]);

  const handlePointerMove = (e: React.PointerEvent) => {
    if (dragIdx === null || !stageRef.current) return;
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
            disabled={saving || !currentImg}
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
                onClick={() => setSplit("train")}
              >
                Train Split
              </button>
              <button
                type="button"
                className={`tab-btn ${split === "val" ? "active" : ""}`}
                onClick={() => setSplit("val")}
              >
                Val Split
              </button>
            </div>

            <div className="row" style={{ gap: 8 }}>
              <button
                type="button"
                className="secondary sm"
                disabled={idx <= 0}
                onClick={() => setIdx((i) => Math.max(0, i - 1))}
              >
                <ArrowLeft size={14} /> Prev
              </button>
              <span className="mono small">
                {images.length ? `${idx + 1} / ${images.length}` : "0 / 0"}
              </span>
              <button
                type="button"
                className="secondary sm"
                disabled={idx >= images.length - 1}
                onClick={() => setIdx((i) => Math.min(images.length - 1, i + 1))}
              >
                Next <ArrowRight size={14} />
              </button>
            </div>
          </div>

          {currentImg ? (
            <div
              style={{
                display: "grid",
                placeItems: "center",
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
                onPointerLeave={() => setDragIdx(null)}
                style={{
                  position: "relative",
                  display: "inline-block",
                  userSelect: "none",
                  touchAction: "none",
                }}
              >
                <img
                  src={api.datasetImageUrl(name, split, currentImg)}
                  alt={currentImg}
                  draggable={false}
                  style={{
                    display: "block",
                    maxWidth: "100%",
                    maxHeight: 540,
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

                {hasQuad &&
                  keypoints.map((kp, ki) => (
                    <div
                      key={ki}
                      onPointerDown={(e) => {
                        e.stopPropagation();
                        (e.currentTarget as HTMLElement).setPointerCapture(
                          e.pointerId
                        );
                        setDragIdx(ki);
                      }}
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
                onClick={placeDefaultQuad}
              >
                <Plus size={14} /> Place Quad
              </button>
            ) : (
              <button
                type="button"
                className="ghost sm"
                onClick={() => {
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

          {!hasQuad ? (
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

          <div className="callout mt" style={{ fontSize: 12 }}>
            <strong>Orientation Convention:</strong> The amber edge connects{" "}
            <code>0:TL → 1:TR</code> (top edge of the optical symbol frame) so homography rectification stays upright.
          </div>
        </div>
      </div>
    </div>
  );
}
