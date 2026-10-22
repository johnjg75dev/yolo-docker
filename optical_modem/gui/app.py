"""FastAPI backend for the optical-modem GUI.

Endpoints
---------
GET  /                     single-page UI
GET  /api/health           capability probe (yolo available?, model, versions)
GET  /api/config           configuration catalogue (modulations, ecc, capacity)
POST /api/frames           text/file -> rendered frame images (base64 PNG)
POST /api/decode           image (base64 or multipart) -> decoded frame/result
POST /api/simulate         TX -> simulated camera -> RX (demo without a webcam)
POST /api/bench            short robustness benchmark
GET  /api/download/{name}  rendered frame PNGs

The UI is a single self-contained HTML document (no CDN assets) so it also
works when embedded in sandboxed previews.
"""
from __future__ import annotations

import base64
import io
import json
import time
from pathlib import Path

import numpy as np

try:
    from fastapi import FastAPI, File, HTTPException, UploadFile
    from fastapi.responses import HTMLResponse, JSONResponse, Response
except ImportError as exc:  # pragma: no cover
    raise ImportError("pip install fastapi uvicorn python-multipart") from exc

from ..config import ECC_PROFILES, MODULATIONS, DisplayProfile, FrameLayout, ModemConfig
from ..phy.channel import ChannelConfig
from ..protocol.packet import Packetizer
from ..sim import capture, prepare_frame

HERE = Path(__file__).resolve().parent
_STATE: dict = {"receiver": None, "key": None, "frames": {}}


DISPLAYS = ["frame", "1280x720", "1920x1080", "2560x1440", "3840x2160"]


def make_config(modulation: str, ecc: str, grid: int | None = None,
                cell_px: int | None = None, resolution: str = "1920x1080",
                mode: str = "auto", weights: str | None = None,
                imgsz: int = 448) -> ModemConfig:
    """Build the TX/RX config the GUI is driving.

    ``resolution`` is the display the code is drawn on (``frame`` = standalone
    square image).  ``cell_px`` is the **symbol scale factor**, i.e. pixels per
    cell; ``grid`` overrides how many cells fit.
    """
    if resolution and resolution.lower() not in ("", "none", "frame"):
        cfg = ModemConfig.for_resolution(
            resolution, modulation=modulation, ecc=ecc,
            symbol_px=int(cell_px or 12), cells=int(grid) if grid else None,
        )
    else:
        cfg = ModemConfig(modulation=modulation, ecc=ecc,
                          cell_px=int(cell_px or 10))
        m = cfg.mod()
        cfg.layout = FrameLayout(cells=int(grid or 32), cps=m.cells_per_symbol,
                                 ring=1, header_rows=4)
    cfg._mode = mode  # type: ignore[attr-defined]
    cfg._weights = weights  # type: ignore[attr-defined]
    cfg._imgsz = imgsz  # type: ignore[attr-defined]
    return cfg


def get_receiver(cfg: ModemConfig, mode: str = "auto"):
    from ..receiver import HybridReceiver

    mode = getattr(cfg, "_mode", mode)
    weights = getattr(cfg, "_weights", None)
    imgsz = getattr(cfg, "_imgsz", 448)
    key = (cfg.modulation, cfg.ecc, cfg.layout.cells, cfg.cell_px,
           cfg.display.label if cfg.display else "frame", mode, weights, imgsz)
    if _STATE["key"] != key:
        _STATE["receiver"] = HybridReceiver(
            cfg, mode=mode, yolo_weights=weights, yolo_imgsz=imgsz
        )
        _STATE["key"] = key
    return _STATE["receiver"]


app = FastAPI(title="Optical Modem", version="0.1.0")


# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def index():
    return (HERE / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health():
    from ..config import __name__ as _  # noqa: F401
    import cv2
    import numpy as np  # noqa: F401

    info = {
        "ok": True,
        "opencv": cv2.__version__,
        "time": time.time(),
        "yolo": False,
        "model": None,
        "model_card": None,
    }
    try:
        import torch  # noqa: F401
        import ultralytics

        info["torch"] = torch.__version__
        info["ultralytics"] = ultralytics.__version__
    except Exception:
        pass
    card = Path("models/yolo26n_optical/weights/model_card.json")
    weights = Path("models/yolo26n_optical/weights/best.pt")
    if weights.exists():
        info["yolo"] = True
        info["model"] = str(weights)
        if card.exists():
            info["model_card"] = json.loads(card.read_text())
    return info


@app.get("/api/config")
def config_catalogue():
    cat = {}
    for mod in MODULATIONS:
        for ecc in ECC_PROFILES:
            cfg = ModemConfig(modulation=mod, ecc=ecc)
            cat[f"{mod}|{ecc}"] = cfg.describe()
    display_matrix = {}
    for res in DISPLAYS[1:]:
        for mod in MODULATIONS:
            display_matrix[f"{res}|{mod}"] = ModemConfig.for_resolution(
                res, modulation=mod).describe()
    mods = {}
    for name, m in MODULATIONS.items():
        mods[name] = {
            "colours": m.n_colors,
            "patterns": m.n_patterns,
            "bits": m.bits,
            "bits_per_cell": round(m.bits_per_cell, 2),
            "pair": m.pair,
        }
    return {
        "modulations": list(MODULATIONS),
        "modulation_info": mods,
        "displays": DISPLAYS,
        "display_matrix": display_matrix,
        "ecc": {k: {"description": v.description, "overhead": round(v.overhead, 2)}
                for k, v in ECC_PROFILES.items()},
        "matrix": cat,
        "grids": [24, 32, 40, 48, 54, 64, 84, 96, 114, 128, 174],
        "symbol_px": [6, 8, 10, 12, 14, 16, 20, 24, 32],
        "frame_structure": ["preamble: calibration ring (known symbols)",
                            "header: 10 B + RS(16), fixed rate",
                            "payload: user data + CRC16 + FEC, interleaved"],
    }


def _b64_png(img: np.ndarray) -> str:
    import cv2

    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("png encode failed")
    return base64.b64encode(buf.tobytes()).decode()


@app.post("/api/frames")
async def frames(payload: dict):
    """Render a message/file as a frame sequence for the transmitter pane."""
    text = payload.get("text", "")
    diversity = 2
    cfg = make_config(
        payload.get("modulation", "rgb8"), payload.get("ecc", "rs32"),
        payload.get("grid"), payload.get("cell_px"),
        resolution=payload.get("resolution", "1920x1080"),
    )
    data = text.encode("utf-8")
    pkt = Packetizer(cfg)
    out = []
    t0 = time.perf_counter()
    for hdr, body in pkt.frames_for(data):
        img = prepare_frame(cfg, hdr, body)
        out.append({
            "header": str(hdr),
            "png": _b64_png(img),
            "bytes": len(body),
        })
        if len(out) >= 40:
            break
    d = cfg.describe()
    return {
        "frames": out * diversity,
        "count": len(out),
        "capacity": d,
        "warmup_ms": round((time.perf_counter() - t0) * 1000, 1),
        "frame_px": cfg.frame_px,
        "frame_ms_at_10fps": len(out) * diversity * 100,
        "bits_total": len(data) * 8,
    }


def _decode_image(img: np.ndarray, cfg: ModemConfig) -> dict:
    rx = get_receiver(cfg)
    rep = rx.decode(img)
    best = rep.best()
    out = {
        "ok": rep.ok,
        "payload_b64": base64.b64encode(rep.payload).decode() if rep.ok else "",
        "payload_text": "",
        "results": [r.as_dict() for r in rep.results[:3]],
        "detections": [
            {"confidence": round(d.confidence, 3),
             "quad": [[float(x), float(y)] for x, y in d.quad.pts],
             "bbox": [float(v) for v in d.bbox]}
            for d in rep.detections
        ],
        "timings_ms": {k: round(v * 1000, 1) for k, v in rep.timings.items()},
        "overlay_png": "",
    }
    if rep.ok:
        try:
            out["payload_text"] = rep.payload.decode("utf-8")
        except UnicodeDecodeError:
            out["payload_text"] = rep.payload[:64].hex() + "…"
    # overlay
    if best is not None and best.quad is not None:
        import cv2

        vis = img.copy()
        q = np.asarray(best.quad, np.int32)
        cv2.polylines(vis, [q.reshape(-1, 1, 2)], True, (0, 255, 0), 2)
        for d in rep.detections:
            cv2.polylines(
                vis, [d.quad.pts.astype(np.int32).reshape(-1, 1, 2)], True,
                (255, 160, 0), 1,
            )
        cv2.putText(vis, f"{best.modulation}/{best.ecc} ring={best.ring_score:.2f}",
                    (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1,
                    cv2.LINE_AA)
        out["overlay_png"] = _b64_png(vis)
    return out


@app.post("/api/decode")
async def decode_endpoint(payload: dict):
    cfg = make_config(
        payload.get("modulation", "rgb8"), payload.get("ecc", "rs32"),
        payload.get("grid"), payload.get("cell_px"),
        resolution=payload.get("resolution", "1920x1080"),
        mode=payload.get("mode", "auto"),
        weights=payload.get("weights"), imgsz=int(payload.get("imgsz", 448)),
    )
    data = payload.get("image", "")
    if "," in data:
        data = data.split(",", 1)[1]
    import cv2

    arr = np.frombuffer(base64.b64decode(data), np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "cannot decode image")
    return _decode_image(img, cfg)


@app.post("/api/decode_file")
async def decode_file(file: UploadFile = File(...), mode: str = "auto",
                      modulation: str = "rgb8", ecc: str = "rs32",
                      grid: int | None = None, resolution: str = "1920x1080"):
    import cv2

    raw = await file.read()
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "cannot decode image")
    return _decode_image(img, make_config(modulation, ecc, grid,
                                          resolution=resolution, mode=mode))


@app.post("/api/simulate")
async def simulate_endpoint(payload: dict):
    """Demo path: transmit -> simulated camera -> receive, no webcam needed."""
    cfg = make_config(
        payload.get("modulation", "rgb8"), payload.get("ecc", "rs32"),
        payload.get("grid"), payload.get("cell_px"),
        resolution=payload.get("resolution", "1920x1080"),
        mode=payload.get("mode", "auto"),
        weights=payload.get("weights"), imgsz=int(payload.get("imgsz", 448)),
    )
    text = payload.get("text", "hello optical modem")
    level = float(payload.get("level", 0.6))
    cap = cfg.describe()["max_user_payload"]
    data = text.encode()[:cap]
    rng = np.random.default_rng(int(payload.get("seed", 0)))
    pkt = Packetizer(cfg)
    hdr, body = next(pkt.frames_for(data))
    frame = prepare_frame(cfg, hdr, body)
    # a screen shows the frame repeatedly and a camera grabs several shots; like
    # a real link, retry a few exposures before calling it a failure
    attempts = int(payload.get("attempts", 3))
    res = None
    img = None
    for i in range(max(1, attempts)):
        ch = ChannelConfig.random(rng, level)
        if cfg.display is not None:
            ch.out_size = 1080
        img, _ = capture(cfg, frame, ch, rng)
        res = _decode_image(img, cfg)
        match = bool(res["ok"] and base64.b64decode(res["payload_b64"]) == data)
        if match:
            break
    res["capture_png"] = _b64_png(img)
    res["level"] = level
    res["attempts_used"] = i + 1
    res["payload_matches"] = match
    return res


@app.post("/api/bench")
async def bench_endpoint(payload: dict):
    from ..bench import bench_robustness

    cfg = make_config(
        payload.get("modulation", "rgb8"), payload.get("ecc", "rs32"),
        payload.get("grid"), payload.get("cell_px"),
        resolution=payload.get("resolution", "1920x1080"),
    )
    modes = [m for m in payload.get("modes", ["classical"]) if m]
    rows = bench_robustness(
        [float(x) for x in payload.get("levels", [0.2, 0.6, 1.0])],
        n=int(payload.get("n", 5)), modes=tuple(modes), cfg=cfg,
        weights=payload.get("weights"), imgsz=int(payload.get("imgsz", 448)),
    )
    return {"rows": [r.__dict__ for r in rows]}


@app.get("/api/download/{name}")
def download(name: str):
    p = Path(name)
    if not p.exists() or p.suffix.lower() not in (".png", ".jpg"):
        raise HTTPException(404, "not found")
    return Response(p.read_bytes(), media_type="image/png")


def main(port: int = 8000, host: str = "0.0.0.0"):
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
