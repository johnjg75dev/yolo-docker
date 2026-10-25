"""Train (or fine-tune) the YOLO26 frame detector.

Examples
--------
Quick CPU run used for the bundled model::

    python -m optical_modem.data.train_yolo --epochs 20 --imgsz 416 --batch 8

Production run on a GPU (what the spec's "YOLO26s / YOLO26m" refers to)::

    python -m optical_modem.data.train_yolo \
        --model yolo26s-pose.pt --epochs 120 --imgsz 640 --batch 32 --device 0

The same script works for ``yolo26n/m/l/x-pose.pt``; only the checkpoint name
changes.  Everything the receiver needs afterwards is written next to the
weights as ``model_card.json`` (keypoint order, grid geometry, training config).
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

KEYPOINT_NAMES = ["grid_tl", "grid_tr", "grid_br", "grid_bl"]


def train(args) -> Path:
    from ultralytics import YOLO

    t0 = time.time()
    patience = args.patience if getattr(args, "patience", 0) else max(5, args.epochs // 3)
    early_stop_start = getattr(args, "early_stop_start", 0) or 0

    def _delay_early_stop(trainer):
        if early_stop_start and (trainer.epoch + 1) < early_stop_start:
            trainer.args.patience = args.epochs
        else:
            trainer.args.patience = patience

    if getattr(args, "resume", False):
        model = YOLO(str(Path(args.project) / args.name / "weights" / "last.pt"))
        res = model.train(resume=True)
        best = Path(args.project) / args.name / "weights" / "best.pt"
        print(f"resumed; best weights: {best}")
        return best
    model = YOLO(args.model)
    model.add_callback("on_train_epoch_end", _delay_early_stop)
    res = model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        workers=args.workers,
        project=args.project,
        name=args.name,
        exist_ok=True,
        seed=args.seed,
        val=True,
        plots=True,
        verbose=True,
        patience=patience,
        # augmentation tuned for a rigid, square target: keep rotation/scale,
        # avoid extreme perspective shears that cannot occur on a screen
        degrees=25.0,
        translate=0.10,
        scale=0.35,
        shear=2.0,
        perspective=0.0005,
        fliplr=0.5,  # flip_idx in the dataset yaml keeps keypoint semantics
        flipud=0.0,
        mosaic=args.mosaic,
        close_mosaic=max(0, args.epochs // 4),
        mixup=0.0,
        copy_paste=0.0,
        hsv_h=0.02,
        hsv_s=0.4,
        hsv_v=0.5,
    )
    best = Path(args.project) / args.name / "weights" / "best.pt"
    card = {
        "model": args.model,
        "weights": str(best),
        "keypoints": KEYPOINT_NAMES,
        "kpt_shape": [len(KEYPOINT_NAMES), 3],
        "task": "pose",
        "classes": {"0": "optical_frame"},
        "train_args": {
            "epochs": args.epochs,
            "imgsz": args.imgsz,
            "batch": args.batch,
            "device": args.device,
            "data": args.data,
        },
        "wall_seconds": round(time.time() - t0, 1),
        "results": {},
    }
    try:  # attach final validation metrics when available
        metrics = model.val(data=args.data, imgsz=args.imgsz, device=args.device,
                            plots=False, verbose=False)
        card["results"] = {
            "box_map50_95": float(metrics.box.map),
            "pose_map50_95": float(metrics.pose.map),
        }
    except Exception as exc:  # pragma: no cover - metrics are best effort
        card["results"] = {"error": str(exc)}
    (best.parent / "model_card.json").write_text(json.dumps(card, indent=1))
    return best


def main(argv=None):
    here = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="yolo26n-pose.pt",
                    help="yolo26{n,s,m,l,x}-pose.pt")
    ap.add_argument("--data", default=str(here / "datasets/optical/optical_frames.yaml"))
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--imgsz", type=int, default=512)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--mosaic", type=float, default=0.3)
    ap.add_argument("--project", default=str(here / "models"))
    ap.add_argument("--name", default="yolo26_optical")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--patience", type=int, default=0,
                    help="early-stopping patience (epochs); 0 = auto")
    ap.add_argument("--early-stop-start", type=int, default=0, dest="early_stop_start",
                    help="do not apply early stopping before this epoch")
    ap.add_argument("--resume", action="store_true", default=False,
                    help="resume training from last.pt")
    args = ap.parse_args(argv)
    os.environ.setdefault("YOLO_OFFLINE", "1")
    best = train(args)
    print(f"best weights: {best}")


if __name__ == "__main__":
    main()
