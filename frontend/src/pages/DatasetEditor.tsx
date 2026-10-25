import { useEffect, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../api/client";
import { useToast } from "../components/Toast";

const NUM_KPS = 4;

export function DatasetEditor() {
  const { name } = useParams();
  const dsName = name || "";
  const [datasets, setDatasets] = useState<any[]>([]);
  const [selected, setSelected] = useState(dsName);
  const [split, setSplit] = useState("train");
  const [images, setImages] = useState<string[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState<number>(20);
  const [active, setActive] = useState<string | null>(null);
  const [label, setLabel] = useState<any>(null);
  const [kps, setKps] = useState<{ x: number; y: number; v: number }[]>([]);
  const [zoom, setZoom] = useState(1);
  const [saving, setSaving] = useState(false);
  const imgRef = useRef<HTMLImageElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ idx: number } | null>(null);
  const { show } = useToast();

  useEffect(() => {
    api.listDatasets().then((ds) => {
      setDatasets(ds);
      if (!selected && ds.length) setSelected(ds[0].name || ds[0].id);
    }).catch((e) => show(e.message, true));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setPage(0);
  }, [selected, split]);

  useEffect(() => {
    if (!selected) return;
    const limit = pageSize === 0 ? 100000 : pageSize;
    const offset = pageSize === 0 ? 0 : page * pageSize;
    api.datasetImages(selected, split, limit, offset).then((r) => {
      setImages(r.images || []);
      setTotal(r.total || 0);
    }).catch((e) => show(e.message, true));
  }, [selected, split, page, pageSize]);

  useEffect(() => {
    if (!selected || !active) { setLabel(null); setKps([]); return; }
    api.datasetLabel(selected, split, active).then((r) => {
      setLabel(r);
      setKps(r.keypoints || []);
    }).catch((e) => show(e.message, true));
  }, [selected, split, active]);

  const onImgClick = (e: React.MouseEvent<HTMLImageElement>) => {
    if (kps.length >= NUM_KPS) return;
    const img = imgRef.current;
    if (!img) return;
    const rect = img.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    setKps([...kps, { x, y, v: 2 }]);
  };

  const onKpMouseDown = (idx: number) => (e: React.MouseEvent) => {
    e.stopPropagation();
    dragRef.current = { idx };
  };

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!dragRef.current) return;
      const img = imgRef.current;
      if (!img) return;
      const rect = img.getBoundingClientRect();
      const idx = dragRef.current.idx;
      const x = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
      const y = Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height));
      setKps((prev) => prev.map((k, i) => (i === idx ? { ...k, x, y } : k)));
    };
    const onUp = () => { dragRef.current = null; };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  const removeKp = (idx: number) => (e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    setKps((prev) => prev.filter((_, i) => i !== idx));
  };

  const save = async () => {
    if (!selected || !active) return;
    setSaving(true);
    try {
      await api.saveDatasetLabel(selected, split, active, { keypoints: kps });
      show("saved");
    } catch (e: any) {
      show(e instanceof Error ? e.message : "save failed", true);
    } finally {
      setSaving(false);
    }
  };

  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    setZoom((z) => Math.min(4, Math.max(1, z + (e.deltaY < 0 ? 0.25 : -0.25))));
  };

  return (
    <div className="wrap">
      <h1>Dataset editor</h1>
      <p className="small">Browse images, view/fix ground-truth keypoints. Click image to add a keypoint (max {NUM_KPS}), drag to move, right-click to remove. Scroll to zoom.</p>

      <div className="card">
        <div className="row">
          <label className="field">
            <span>dataset</span>
            <select value={selected} onChange={(e) => { setSelected(e.target.value); setActive(null); }}>
              {datasets.map((d: any) => (
                <option key={d.name || d.id} value={d.name || d.id}>{d.name || d.id}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>split</span>
            <select value={split} onChange={(e) => { setSplit(e.target.value); setActive(null); }}>
              <option value="train">train</option>
              <option value="val">val</option>
              <option value="test">test</option>
            </select>
          </label>
          <span className="small" style={{ alignSelf: "flex-end" }}>{total} images</span>
        </div>
      </div>

      {active && (
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2 style={{ margin: 0 }}>{active}</h2>
            <div className="row">
              <button className="secondary" onClick={() => setZoom(1)}>reset zoom</button>
              <button onClick={save} disabled={saving}>{saving ? "saving…" : "save"}</button>
              <button className="secondary" onClick={() => setActive(null)}>close</button>
            </div>
          </div>
          <div
            ref={containerRef}
            onWheel={onWheel}
            style={{ position: "relative", overflow: "hidden", marginTop: 8, cursor: "crosshair", maxHeight: "70vh" }}
          >
            <div style={{ transform: `scale(${zoom})`, transformOrigin: "top left", display: "inline-block", position: "relative" }}>
              <img
                ref={imgRef}
                src={selected ? api.datasetImageUrl(selected, split, active) : ""}
                alt={active}
                onClick={onImgClick}
                style={{ maxWidth: "100%", display: "block", userSelect: "none" }}
                draggable={false}
              />
              {kps.map((k, i) => (
                <div
                  key={i}
                  onMouseDown={onKpMouseDown(i)}
                  onContextMenu={removeKp(i)}
                  title={`keypoint ${i + 1} (right-click to remove)`}
                  style={{
                    position: "absolute",
                    left: `${k.x * 100}%`,
                    top: `${k.y * 100}%`,
                    width: 14,
                    height: 14,
                    marginLeft: -7,
                    marginTop: -7,
                    borderRadius: "50%",
                    background: ["#ff3b30", "#ffcc00", "#34c759", "#007aff"][i % 4],
                    border: "2px solid #fff",
                    cursor: "grab",
                    boxShadow: "0 0 4px rgba(0,0,0,0.6)",
                    zIndex: 2,
                  }}
                />
              ))}
            </div>
          </div>
          <p className="small mt">Keypoints: {kps.length}/{NUM_KPS}. {label?.text ? "existing label loaded." : "no label yet."}</p>
        </div>
      )}

      <div className="card">
        <h2>Gallery</h2>
        {images.length === 0 ? (
          <p className="small">No images in this split.</p>
        ) : (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))", gap: 8 }}>
              {images.map((img) => (
                <div
                  key={img}
                  onClick={() => setActive(img)}
                  style={{
                    cursor: "pointer",
                    border: active === img ? "2px solid #4f8cff" : "1px solid #ddd",
                    borderRadius: 4,
                    overflow: "hidden",
                    background: "#f5f5f5",
                  }}
                >
                  <img
                    src={selected ? api.datasetImageUrl(selected, split, img, 128) : ""}
                    alt={img}
                    style={{ width: "100%", height: 100, objectFit: "cover", display: "block" }}
                    loading="lazy"
                  />
                  <div className="small" style={{ padding: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {img}
                  </div>
                </div>
              ))}
            </div>
            <div className="row mt" style={{ justifyContent: "space-between", alignItems: "center" }}>
              <div className="row" style={{ gap: 8 }}>
                <button className="secondary" disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}>prev</button>
                <span className="small">
                  page {page + 1} of {Math.max(1, Math.ceil(total / (pageSize === 0 ? total || 1 : pageSize)))} ({total} images)
                </span>
                <button className="secondary" disabled={pageSize === 0 || (page + 1) * pageSize >= total} onClick={() => setPage((p) => p + 1)}>next</button>
              </div>
              <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
                <span className="small">per page</span>
                <select value={pageSize} onChange={(e) => { const v = Number(e.target.value); setPageSize(v); setPage(0); }}>
                  <option value={20}>20</option>
                  <option value={50}>50</option>
                  <option value={100}>100</option>
                  <option value={0}>all</option>
                </select>
              </label>
            </div>
          </>
        )}
      </div>

      <p className="small mt"><Link to="/datasets">← back to datasets</Link></p>
    </div>
  );
}
