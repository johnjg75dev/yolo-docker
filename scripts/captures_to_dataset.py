"""Turn captured photos into a YOLO-pose detector dataset.

The capture station stores, for every accepted shot, the photo plus a JSON
sidecar with the frame's full description and (when the pure-OpenCV labeller
found it) the grid quad in *photo* pixels.  This script walks a data directory
and emits the same layout :mod:`optical_modem.data.synth` produces, so the
existing trainer can consume real captures — or a mix of real and synthetic.

Label convention (identical to the synthetic generator):

    <class> <cx> <cy> <w> <h>  kpt0x kpt0y v  kpt1x kpt1y v  kpt2x kpt2y v  kpt3x kpt3y v

all normalised to the image size, keypoints in the transmitter's own
orientation: 0 = grid TL, 1 = TR, 2 = BR, 3 = BL.  The quad stored by the
labeller is the *grid* boundary (the coded cells), not the bright plate, so the
two sources agree.

    python scripts/captures_to_dataset.py --data-dir /srv/capture-data \
        --out datasets/captures

    # only labels the labeller was confident about, and 20 % to val
    python scripts/captures_to_dataset.py --verified-only --val-frac 0.2
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

import numpy as np

CLASS_NAME = "optical_frame"
N_KEYPOINTS = 4

YAML = f"""# Optical-modem frame detector, built from capture-station photos.
#   python scripts/captures_to_dataset.py --data-dir ... --out ...
path: {{{{OUT}}}}
train: images/train
val: images/val
kpt_shape: [{N_KEYPOINTS}, 3]
# horizontal flip swaps TL <-> TR and BL <-> BR
flip_idx: [1, 0, 3, 2]

names:
  0: {CLASS_NAME}
"""


def _label_line(quad: np.ndarray, size: tuple[int, int], pad: float = 0.02) -> str:
    """One YOLO-pose line from a grid quad in pixel coordinates."""
    w, h = size
    pts = np.asarray(quad, np.float32).reshape(4, 2)
    xs = np.clip(pts[:, 0], 0, w - 1)
    ys = np.clip(pts[:, 1], 0, h - 1)
    pad_px = pad * max(xs.max() - xs.min(), ys.max() - ys.min())
    x0 = max(0.0, xs.min() - pad_px)
    x1 = min(float(w - 1), xs.max() + pad_px)
    y0 = max(0.0, ys.min() - pad_px)
    y1 = min(float(h - 1), ys.max() + pad_px)
    cx, cy = (x0 + x1) / 2 / w, (y0 + y1) / 2 / h
    bw, bh = (x1 - x0) / w, (y1 - y0) / h
    parts = [f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"]
    for x, y in pts:
        inside = 0 <= x < w and 0 <= y < h
        vis = 2 if inside else 0
        parts.append(f"{np.clip(x / w, 0, 1):.6f} {np.clip(y / h, 0, 1):.6f} {vis}")
    return " ".join(parts) + "\n"


def _split_of(session_id: str, val_frac: float) -> str:
    """Deterministic split *by session*, so one screen/scene never straddles."""
    if val_frac <= 0:
        return "train"
    h = int(hashlib.sha1(session_id.encode()).hexdigest()[:8], 16) % 10_000
    return "val" if h < val_frac * 10_000 else "train"


def iter_shots(data_dir: Path, only: str | None = None):
    """Yield ``(session_json, shot_json, image_path)`` for every stored shot."""
    for sess_dir in sorted((data_dir / "sessions").glob("*")):
        shot_dir = sess_dir / "shots"
        if not shot_dir.is_dir():
            continue
        if only and only not in sess_dir.name:
            continue
        for sidecar in sorted(shot_dir.glob("*.json")):
            if sidecar.name.endswith("_label.json"):
                continue
            try:
                meta = json.loads(sidecar.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            # the sidecar's ``file`` is relative to the *session* directory
            img = sess_dir / meta.get("file", "") if meta.get("file") else \
                sidecar.with_suffix(".jpg")
            if not img.exists():
                img = shot_dir / sidecar.with_suffix(".jpg").name
            if img.exists():
                yield sess_dir.name, meta, img


def build(data_dir: Path, out: Path, val_frac: float = 0.15,
          verified_only: bool = False, min_score: float = 0.0,
          copy: bool = True) -> dict:
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    manifest: list[dict] = []
    skipped = {"no_label": 0, "unverified": 0, "low_score": 0, "duplicate": 0}
    seen: set[str] = set()

    for session_id, meta, img in iter_shots(data_dir):
        label = meta.get("label") or {}
        if not label.get("found") or not label.get("quad"):
            skipped["no_label"] += 1
            continue
        if verified_only and not label.get("verified"):
            skipped["unverified"] += 1
            continue
        if float(label.get("score") or 0.0) < min_score:
            skipped["low_score"] += 1
            continue
        size = label.get("image_size")
        if not size or len(size) != 2:
            skipped["no_label"] += 1
            continue

        split = _split_of(session_id, val_frac)
        stem = f"{session_id.split('-')[-1]}_{int(meta['index']):04d}_{meta['attempt']}"
        if stem in seen:
            skipped["duplicate"] += 1
            continue
        seen.add(stem)

        dst_img = out / "images" / split / f"{stem}.jpg"
        if copy:
            shutil.copy2(img, dst_img)
        else:
            if dst_img.exists() or dst_img.is_symlink():
                dst_img.unlink()
            dst_img.symlink_to(img.resolve())
        (out / "labels" / split / f"{stem}.txt").write_text(
            _label_line(label["quad"], (int(size[0]), int(size[1]))))

        spec = meta.get("spec") or {}
        manifest.append({
            "file": f"images/{split}/{stem}.jpg",
            "split": split,
            "session": session_id,
            "index": meta.get("index"),
            "attempt": meta.get("attempt"),
            "score": label.get("score"),
            "verified": bool(label.get("verified")),
            "modulation": spec.get("modulation"),
            "ecc": spec.get("ecc"),
            "resolution": spec.get("resolution"),
            "cells": spec.get("cells"),
            "cell_px": spec.get("cell_px"),
            "device": meta.get("device"),
        })

    yaml_path = out / "data.yaml"
    yaml_path.write_text(YAML.replace("{{OUT}}", str(out.resolve())))
    (out / "manifest_captures.json").write_text(json.dumps(manifest, indent=1))
    summary = {
        "written": len(manifest),
        "train": sum(1 for m in manifest if m["split"] == "train"),
        "val": sum(1 for m in manifest if m["split"] == "val"),
        "verified": sum(1 for m in manifest if m["verified"]),
        "skipped": skipped,
        "yaml": str(yaml_path),
        "out": str(out),
    }
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", default="/data",
                    help="capture-station data directory (contains sessions/)")
    ap.add_argument("--out", default="datasets/captures",
                    help="dataset directory to write")
    ap.add_argument("--val-frac", type=float, default=0.15,
                    help="fraction of *sessions* sent to val (0 disables)")
    ap.add_argument("--verified-only", action="store_true",
                    help="keep only labels the labeller was confident about")
    ap.add_argument("--min-score", type=float, default=0.0,
                    help="drop labels whose ring fit is below this (0..1)")
    ap.add_argument("--link", action="store_true",
                    help="symlink photos instead of copying them")
    ap.add_argument("--session", default=None,
                    help="only this session id (or a unique substring of it)")
    args = ap.parse_args(argv)

    data_dir = Path(args.data_dir)
    if not (data_dir / "sessions").is_dir():
        print(f"no sessions under {data_dir} — pass --data-dir", file=sys.stderr)
        return 2
    summary = build(data_dir, Path(args.out), val_frac=args.val_frac,
                    verified_only=args.verified_only,
                    min_score=args.min_score, copy=not args.link)
    print(json.dumps(summary, indent=2))
    if summary["written"] == 0:
        print("\nnothing usable: photos with no label are still on disk and can "
              "be labelled later with the YOLO detector "
              "(scripts/… or optical_modem.gui) — auto-labelling only verifies "
              "~70-80 % of handheld shots.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
