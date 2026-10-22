"""Independent evaluation of the trained detector + receiver.

Measures the things that actually matter for this project:

* **corner error** — mean distance between predicted and true grid corners,
  as a fraction of the frame side (the metric OKS mAP hides)
* **detection rate** — how often a frame is found at all
* **end-to-end decode rate** — frame decoded and payload byte-identical
* **latency** — detector and full receiver, ms/frame

    python scripts/evaluate.py --n 25 --level 0.6
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from optical_modem.config import ModemConfig
from optical_modem.phy.channel import ChannelConfig, grid_quad_from_frame_quad, simulate
from optical_modem.phy.render import render_frame
from optical_modem.protocol.packet import FrameHeader

WEIGHTS = "models/yolo26n_optical/weights/best.pt"
OUT = Path("docs/model_eval.json")


def run(n=25, levels=(0.2, 0.6, 1.0), imgsz=448, out=OUT, weights=WEIGHTS):
    """Measure corner error / detection / decode rate for one checkpoint.

    ``weights`` defaults to the bundled model; the suite passes the run it is
    evaluating (``--weights /data/runs/<name>/weights/best.pt``).
    """
    from optical_modem.receiver import HybridReceiver

    weights = str(weights)
    if not Path(weights).exists():
        raise FileNotFoundError(f"no such weights: {weights}")
    cfg = ModemConfig(modulation="rgb8", ecc="rs16")
    rng = np.random.default_rng(1234)
    report: dict = {"weights": weights, "imgsz": imgsz, "levels": {}}
    report["levels"] = {}

    payload = bytes(rng.integers(0, 256, 200, dtype=np.uint8))
    hdr = FrameHeader(2, 2, 0, 1, len(payload) + 2)
    frame = render_frame(cfg, hdr, payload)

    for level in levels:
        captures = []
        for _ in range(n):
            cam, quad = simulate(frame, ChannelConfig.random(rng, level), rng)
            gt = grid_quad_from_frame_quad(quad, cfg.frame_px, cfg.cell_px,
                                           cfg.quiet_zone_cells, cfg.layout.grid)
            captures.append((cam, gt))
        entry = {"n": n}
        for mode in ("classical", "yolo", "auto"):
            rx = HybridReceiver(cfg, mode=mode, yolo_weights=weights, yolo_imgsz=imgsz)
            errs, det, ok, times = [], 0, 0, []
            for cam, gt in captures:
                rep = rx.decode(cam)
                times.append(rep.timings.get("total", 0.0) * 1000)
                if rep.detections:
                    det += 1
                    side = float(np.linalg.norm(gt[0] - gt[1]))
                    errs.append(
                        float(np.linalg.norm(rep.detections[0].quad.pts - gt, axis=1).mean()) / side
                    )
                if rep.ok and rep.payload == payload:
                    ok += 1
            entry[mode] = {
                "decode_rate": ok / n,
                "detected": det / n,
                "detection_rate": det / n if mode != "classical" else None,
                "corner_err_median": float(np.median(errs)) if errs else None,
                "corner_err_p90": float(np.percentile(errs, 90)) if errs else None,
                "corner_err_pct_median": (100 * float(np.median(errs))) if errs else None,
                "corner_err_pct_p90": (100 * float(np.percentile(errs, 90))) if errs else None,
                "ms_per_frame": float(np.mean(times)),
                "ms_mean": float(np.mean(times)),
            }
        report["levels"][f"{level:.2f}"] = entry
        print(f"level {level:.2f}: " + "  ".join(
            f"{m} {entry[m]['decode_rate']*100:.0f}%" for m in ("classical", "yolo", "auto")
        ))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1))
    print("written", out)
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=25)
    ap.add_argument("--imgsz", type=int, default=448)
    ap.add_argument("--levels", default="0.2,0.6,1.0")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--weights", default=WEIGHTS,
                    help="checkpoint to evaluate (default: the bundled model)")
    ap.add_argument("--dataset", default=None,
                    help="unused by the receiver (kept for command compatibility)")
    a = ap.parse_args()
    run(a.n, tuple(float(x) for x in a.levels.split(",")), a.imgsz, Path(a.out),
        weights=a.weights)
