"""Dataset building blocks: synthetic, captures, merge, preview.

Three sources feed one YOLO-pose dataset:

* **synthetic** — :mod:`optical_modem.data.synth` renders frames and pushes them
  through the camera channel.  Unlimited, free, and covers the palette/grid
  space far better than a phone can.
* **captures** — the capture station's photos, labelled by the classical
  localiser (or by the currently active detector).  Real lens, real screen,
  real room light: the part synthetic data gets wrong.
* **merge** — put any number of the above in one directory with one
  ``data.yaml``, so training sees a single dataset and a single val split.

Everything is plain files in YOLO-pose format, which means the result can be
trained with ultralytics directly and inspected with any tool.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
CLASS_NAME = "optical_frame"
N_KEYPOINTS = 4

YAML = """# Optical-modem frame detector (YOLO-pose)
# written by optical_modem.suite.datasets — sources: {sources}
path: {path}
train: images/train
val: images/val
kpt_shape: [{n_kpt}, 3]
# horizontal flip swaps TL <-> TR and BL <-> BR
flip_idx: [1, 0, 3, 2]

names:
  0: {cls}
"""


def write_yaml(out: str | Path, sources: list[str] | None = None) -> Path:
    out = Path(out)
    (out).mkdir(parents=True, exist_ok=True)
    p = out / "data.yaml"
    p.write_text(YAML.format(path=str(out.resolve()),
                             sources=", ".join(sources or []),
                             n_kpt=N_KEYPOINTS, cls=CLASS_NAME))
    return p


# --------------------------------------------------------------------------- #
# sources
# --------------------------------------------------------------------------- #
def synth_dataset(out: str | Path, n_train: int = 150, n_val: int = 30,
                  seed: int = 1, level: float = 1.0, workers: int | None = None) -> dict:
    """Render + degrade ``n_train``/``n_val`` frames into ``out``."""
    from ..data.synth import build_dataset

    out = Path(out)
    summary = build_dataset(out, n_train=int(n_train), n_val=int(n_val),
                            level_range=(0.15, max(0.2, 1.15 * float(level))),
                            seed0=int(seed), workers=workers)
    # synth writes optical_frames.yaml; the suite standardises on data.yaml
    write_yaml(out, sources=[f"synthetic(seed={seed})"])
    try:
        (out / "summary.json").write_text(json.dumps(summary, indent=1))
    except OSError:
        pass
    return summary


def captures_dataset(data_dir: str | Path, out: str | Path,
                     val_frac: float = 0.15, verified_only: bool = False,
                     min_score: float = 0.0) -> dict:
    """Convert capture-station photos into ``out`` (see scripts/captures_to_dataset)."""
    from scripts.captures_to_dataset import build

    out = Path(out)
    summary = build(Path(data_dir), out, val_frac=val_frac,
                    verified_only=verified_only, min_score=min_score, copy=True)
    if summary.get("written"):
        write_yaml(out, sources=["captures"])
        try:
            (out / "summary.json").write_text(json.dumps(summary, indent=1))
        except OSError:
            pass
    return summary


def _copy_split(src: Path, dst: Path, split: str, prefix: str) -> dict:
    """Copy one split of ``src`` into ``dst`` with a filename prefix."""
    images = src / "images" / split
    labels = src / "labels" / split
    if not images.is_dir():
        return {"images": 0, "labels": 0, "missing_labels": 0}
    (dst / "images" / split).mkdir(parents=True, exist_ok=True)
    (dst / "labels" / split).mkdir(parents=True, exist_ok=True)
    n_img = n_lbl = 0
    for img in sorted(images.iterdir()):
        if img.suffix.lower() not in IMAGE_EXT:
            continue
        label = labels / (img.stem + ".txt")
        if not label.exists():
            continue                      # an image without a label teaches nothing
        target = dst / "images" / split / f"{prefix}_{img.name}"
        shutil.copy2(img, target)
        shutil.copy2(label, dst / "labels" / split / f"{prefix}_{label.name}")
        n_img += 1
        n_lbl += 1
    return {"images": n_img, "labels": n_lbl,
            "missing_labels": len(list(images.iterdir())) - n_img}


def merge_datasets(sources: list[str | Path], out: str | Path,
                   move: bool = False) -> dict:
    """Merge YOLO-pose datasets into ``out`` (same split layout, one data.yaml).

    Filenames are prefixed with the source directory name so two datasets that
    both contain ``frame_00001.jpg`` do not collide.  Labels travel with their
    image or the pair is dropped: a half-copied dataset silently trains the
    detector to ignore frames.
    """
    out = Path(out)
    if out.exists() and (out / "images").exists() and any((out / "images").iterdir()):
        raise FileExistsError(f"{out} already holds a dataset; pick a new name")
    write_yaml(out, sources=[Path(s).name for s in sources])
    summary: dict = {"sources": {}, "total": {"images": 0, "labels": 0}}
    for src in map(Path, sources):
        if not (src / "images").is_dir():
            summary["sources"][src.name] = {"error": "no images/ directory"}
            continue
        prefix = src.name.replace(" ", "_")[:40]
        counts: dict = {}
        for split in ("train", "val", "test"):
            got = _copy_split(src, out, split, prefix)
            if got["images"]:
                counts[split] = got
                summary["total"]["images"] += got["images"]
                summary["total"]["labels"] += got["labels"]
        meta = {}
        for name in ("manifest_captures.json", "manifest_train.json"):
            p = src / name
            if p.exists():
                try:
                    meta[name] = len(json.loads(p.read_text()))
                except (OSError, json.JSONDecodeError):
                    pass
        summary["sources"][src.name] = {**counts, "meta": meta}
        if move:
            shutil.rmtree(src, ignore_errors=True)
    if summary["total"]["images"] == 0:
        raise ValueError("no labelled images found in the given sources")
    summary["yaml"] = str(out / "data.yaml")
    summary["out"] = str(out)
    summary["bytes"] = sum(f.stat().st_size for f in (out / "images").rglob("*")
                           if f.is_file())
    try:
        (out / "summary.json").write_text(json.dumps(summary, indent=1))
    except OSError:
        pass
    return summary


# --------------------------------------------------------------------------- #
# inspection
# --------------------------------------------------------------------------- #
def preview(dataset: str | Path, split: str = "train", index: int = 0,
            annotated: bool = True, max_px: int = 900) -> bytes | None:
    """Return one dataset image as JPEG, with its label drawn on it.

    Drawing the label is the whole point: a mislabelled sample is obvious in a
    montage of five of them and invisible in a JSON file.
    """
    import cv2

    ds = Path(dataset)
    images = ds / "images" / split
    if not images.is_dir():
        return None
    names = sorted(f for f in images.iterdir() if f.suffix.lower() in IMAGE_EXT)
    if not names:
        return None
    img_path = names[int(index) % len(names)]
    img = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
    if img is None:
        return None
    if annotated:
        label = ds / "labels" / split / (img_path.stem + ".txt")
        if label.exists():
            h, w = img.shape[:2]
            try:
                parts = label.read_text().split()
            except OSError:
                parts = []
            if len(parts) >= 5 + 4 * 3:
                cx, cy, bw, bh = (float(v) for v in parts[1:5])
                x0, y0 = int((cx - bw / 2) * w), int((cy - bh / 2) * h)
                x1, y1 = int((cx + bw / 2) * w), int((cy + bh / 2) * h)
                cv2.rectangle(img, (x0, y0), (x1, y1), (0, 255, 0), 2)
                pts = np.array([[float(parts[5 + 3 * i]) * w,
                                 float(parts[6 + 3 * i]) * h] for i in range(4)],
                               np.int32)
                for i in range(4):
                    cv2.line(img, tuple(pts[i]), tuple(pts[(i + 1) % 4]), (0, 220, 255), 2)
                    cv2.circle(img, tuple(pts[i]), 7, (0, 0, 255), -1)
                    cv2.putText(img, str(i), (int(pts[i][0]) + 9, int(pts[i][1]) - 9),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                cv2.putText(img, img_path.name, (10, 26),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    h, w = img.shape[:2]
    if max(h, w) > max_px:
        k = max_px / max(h, w)
        img = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    return buf.tobytes() if ok else None


def image_count(dataset: str | Path, split: str = "train") -> int:
    images = Path(dataset) / "images" / split
    if not images.is_dir():
        return 0
    return sum(1 for f in images.iterdir() if f.suffix.lower() in IMAGE_EXT)
