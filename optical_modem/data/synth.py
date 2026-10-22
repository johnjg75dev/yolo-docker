"""Synthetic training-data generator for the YOLO26 frame detector.

Each sample is a simulated camera capture of one optical-modem frame with the
four coded-grid corners as keypoints (class ``0 = optical_frame``)::

    kpt 0 = grid TL, 1 = grid TR, 2 = grid BR, 3 = grid BL

The orientation is *semantic*: the corners are always assigned in the
transmitter's own orientation, so the network learns the frame's handedness
rather than just its geometry.  A horizontal flip is accompanied by
``flip_idx: [1, 0, 3, 2]`` in the dataset yaml so augmentation stays consistent.

Usage::

    python -m optical_modem.data.synth --out datasets/optical --train 1500 --val 300
"""
from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

from ..config import ECC_PROFILES, MODULATIONS, DisplayProfile, ModemConfig
from ..phy.channel import (ChannelConfig, grid_quad_from_frame_quad, rect_quad,
                           simulate)
from ..phy.render import render_frame, screen_geometry
from ..protocol.packet import FrameHeader
from ..sim import prepare_frame

CLASS_NAME = "optical_frame"
N_KEYPOINTS = 4  # TL, TR, BR, BL of the coded grid


@dataclass
class SampleSpec:
    seed: int
    modulation: str
    ecc: str
    cell_px: int
    grid: int
    quiet_zone_cells: int
    level: float
    out_size: int
    #: ``"1280x720"`` / ``"1920x1080"`` to render the frame *on a display canvas*
    #: (code centred in a wide, mostly black screen) — that is what the S2C
    #: display page shows, and a detector trained only on square renders does not
    #: recognise it worth a damn.  ``None`` keeps the classic square render.
    display: str | None = None
    #: finder pattern for this sample: ``None`` = pick at random (a mix of none
    #: and shapes), ``"none"`` = definitely none, a shape name = that shape
    finder: str | None = None


def feasible_eccs(modulation: str, grid: int) -> list[str]:
    """ECC profiles whose payload block still fits a ``grid x grid`` frame."""
    from ..protocol.frames import FrameCodec

    out = []
    for name in ECC_PROFILES:
        try:
            cfg = ModemConfig(modulation=modulation, ecc=name)
            cfg.layout = type(cfg.layout)(cells=grid, ring=1, header_rows=4)
            if FrameCodec(cfg).body_capacity >= 8:
                out.append(name)
        except Exception:
            continue
    return out


def _payload(rng: np.random.Generator, n: int) -> bytes:
    return bytes(rng.integers(0, 256, n, dtype=np.uint8))


def make_sample(spec: SampleSpec) -> tuple[np.ndarray, np.ndarray, dict]:
    """Render + simulate one capture; returns ``(image, keypoints(4,3), meta)``."""
    rng = np.random.default_rng(spec.seed)
    cfg = ModemConfig(
        modulation=spec.modulation,
        ecc=spec.ecc,
        cell_px=spec.cell_px,
        quiet_zone_cells=spec.quiet_zone_cells,
    )
    if spec.grid < 24 or spec.grid > 64:
        raise ValueError(f"grid {spec.grid} out of range")
    # the layout is expressed in *cells*; pair modes put a symbol in two cells,
    # so `cps` (cells per symbol) has to come from the modulation
    mod = cfg.mod()
    # a share of samples carry a finder pattern: it is reserved space, drawn from
    # the palette, so a detector must learn not to mistake it for a frame edge
    finder = "none"
    if spec.finder is None:
        finder = str(rng.choice(["none", "none", "ring", "dice", "bullseye",
                                 "checker", "bracket"]))
    elif spec.finder:
        finder = str(spec.finder)
    cfg.layout = type(cfg.layout)(cells=spec.grid, cps=mod.cells_per_symbol,
                                  ring=1, header_rows=4, finder=finder)
    # payload length must fit the frame's actual capacity for this mod/ECC/grid
    from ..protocol.frames import FrameCodec

    capacity = FrameCodec(cfg).body_capacity - 2
    n_payload = max(1, int(rng.integers(8, max(9, capacity + 1))))
    hdr = FrameHeader(
        mod_id=mod.mod_id,
        ecc_id=cfg.ecc_profile().ecc_id,
        frame_type=int(rng.integers(0, 3)),
        seq=int(rng.integers(0, 65535)),
        length=n_payload + 2,
    )
    disp = None
    if spec.display:
        w, h = (int(v) for v in str(spec.display).lower().split("x"))
        disp = DisplayProfile(width=w, height=h, symbol_px=spec.cell_px,
                              margin_px=max(8, spec.cell_px // 2))
        cfg.display = disp
        cfg = cfg.fitted()
        # `fit` may grow the cell size on a big screen; keep the label geometry
        # consistent with what is actually drawn
        cfg.layout = type(cfg.layout)(cells=spec.grid, cps=cfg.mod().cells_per_symbol,
                                      ring=1, header_rows=4)
    frame = prepare_frame(cfg, hdr, _payload(rng, n_payload))

    # A frame that lands almost entirely off-canvas is a useless label (and the
    # box hull goes negative, which Ultralytics rejects at load time), so give
    # the channel up to two more tries before falling back to an easy view.
    levels = (spec.level, spec.level * 0.55, 0.10)
    for attempt, level in enumerate(levels):
        ch = ChannelConfig.random(rng, level)
        ch.out_size = spec.out_size
        if disp is not None:
            # the whole screen is in shot: the code is a square in the middle of
            # a wide, mostly black canvas (plus the room around the monitor)
            img, quad = simulate(frame, ch, rng)
            kpts = rect_quad(quad, frame.shape[:2], screen_geometry(cfg)["grid"])
        else:
            img, quad = simulate(frame, ch, rng)
            kpts = grid_quad_from_frame_quad(
                quad, cfg.frame_px, cfg.cell_px, spec.quiet_zone_cells, spec.grid
            )
        h, w = img.shape[:2]
        cx0, cx1 = np.clip(kpts[:, 0], 0, w - 1).min(), np.clip(kpts[:, 0], 0, w - 1).max()
        cy0, cy1 = np.clip(kpts[:, 1], 0, h - 1).min(), np.clip(kpts[:, 1], 0, h - 1).max()
        if attempt == len(levels) - 1 or ((cx1 - cx0) >= 0.20 * w
                                          and (cy1 - cy0) >= 0.20 * h):
            break
    vis = np.ones(4, np.float32) * 2
    for i, (x, y) in enumerate(kpts):
        if not (0 <= x < w and 0 <= y < h):
            vis[i] = 0
    meta = {
        "modulation": spec.modulation,
        "ecc": spec.ecc,
        "level": float(level),
        "frame_px": cfg.frame_px,
        "grid": spec.grid,
        "quiet_zone_cells": spec.quiet_zone_cells,
        "cell_px": spec.cell_px,
        "display": spec.display,
        "finder": finder,
    }
    return img, np.column_stack([kpts, vis]).astype(np.float32), meta


def _random_spec(seed: int, split: str, level_range: tuple[float, float]) -> SampleSpec:
    rng = np.random.default_rng(seed * 7919 + (0 if split == "train" else 1))
    # a quarter of the samples are easy/clean, the rest are progressively harder
    if rng.random() < 0.25:
        level = float(rng.uniform(0.0, 0.25))
    else:
        level = float(rng.uniform(*level_range))
    grid = int(rng.choice([28, 32, 32, 32, 36]))
    # the S2C display page shows the code on a wide screen canvas; without these
    # samples a detector trained here cannot find a 16:9-displayed frame at all
    display = None if rng.random() < 0.6 else str(rng.choice(["1280x720", "1920x1080"]))
    modulation = str(rng.choice(list(MODULATIONS)))
    eccs = feasible_eccs(modulation, grid) or ["rs8"]
    return SampleSpec(
        seed=int(seed),
        modulation=modulation,
        ecc=str(rng.choice(eccs)),
        cell_px=int(rng.choice([8, 9, 10, 10, 12])),
        grid=grid,
        quiet_zone_cells=int(rng.choice([2, 2, 2, 3])),
        level=level,
        out_size=int(rng.choice([560, 600, 640, 640, 720])),
        display=display,
    )


def _write_one(args) -> dict | None:
    img_path, lbl_path, spec = args
    try:
        img, kpts, meta = make_sample(spec)
    except Exception as exc:  # keep the generator going on odd geometries
        return {"error": f"{type(exc).__name__}: {exc}", "seed": spec.seed}
    h, w = img.shape[:2]
    if not cv2.imwrite(img_path, img, [int(cv2.IMWRITE_JPEG_QUALITY), 88]):
        return {"error": "imwrite failed", "seed": spec.seed}
    # box = axis aligned hull of the *visible* keypoints (padded a little);
    # clamp first, or a corner outside the canvas yields a negative cx/cy and
    # Ultralytics silently drops the whole image as corrupt
    xs = np.clip(kpts[:, 0], 0, w - 1)
    ys = np.clip(kpts[:, 1], 0, h - 1)
    x0, x1 = xs.min(), xs.max()
    y0, y1 = ys.min(), ys.max()
    pad = 0.02 * max(x1 - x0, y1 - y0)
    x0, x1 = max(0, x0 - pad), min(w - 1, x1 + pad)
    y0, y1 = max(0, y0 - pad), min(h - 1, y1 + pad)
    cx, cy = (x0 + x1) / 2 / w, (y0 + y1) / 2 / h
    bw, bh = (x1 - x0) / w, (y1 - y0) / h
    if min(bw, bh) < 0.05 or not (0 <= cx <= 1 and 0 <= cy <= 1):
        Path(lbl_path).unlink(missing_ok=True)
        Path(img_path).unlink(missing_ok=True)
        return {"skipped": "degenerate view", "seed": spec.seed}
    parts = [f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"]
    for (x, y, v) in kpts:
        parts.append(f"{np.clip(x / w, 0, 1):.6f} {np.clip(y / h, 0, 1):.6f} {int(v)}")
    Path(lbl_path).write_text(" ".join(parts) + "\n")
    meta = dict(meta)
    meta["seed"] = spec.seed
    return meta


def build_dataset(out_dir: str | Path, n_train: int = 1500, n_val: int = 300,
                  level_range: tuple[float, float] = (0.15, 1.15),
                  workers: int | None = None, seed0: int = 1) -> dict:
    out = Path(out_dir)
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    workers = workers or max(1, (os.cpu_count() or 2))
    summary = {}
    for split, n in (("train", n_train), ("val", n_val)):
        jobs = []
        for i in range(n):
            sd = seed0 + i + (1_000_000 if split == "val" else 0)
            spec = _random_spec(sd, split, level_range)
            jobs.append((
                str(out / "images" / split / f"frame_{i:05d}.jpg"),
                str(out / "labels" / split / f"frame_{i:05d}.txt"),
                spec,
            ))
        metas, errors, skipped = [], 0, 0
        results = (_write_one(job) for job in jobs)
        if workers > 1:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                results = ex.map(_write_one, jobs, chunksize=8)
                for res in results:
                    if res is None or "error" in res:
                        errors += 1
                    elif "skipped" in res:
                        skipped += 1
                    else:
                        metas.append(res)
        else:
            for res in results:
                if res is None or "error" in res:
                    errors += 1
                elif "skipped" in res:
                    skipped += 1
                else:
                    metas.append(res)
        (out / f"manifest_{split}.json").write_text(json.dumps(metas, indent=1))
        summary[split] = {"written": len(metas), "errors": errors, "skipped": skipped}
    yaml_path = write_yaml(out)
    summary["yaml"] = str(yaml_path)
    return summary


def write_yaml(out: Path) -> Path:
    txt = f"""# Optical modem frame detection (YOLO26-pose)
# generated by optical_modem.data.synth
path: {out.resolve()}
train: images/train
val: images/val

kpt_shape: [{N_KEYPOINTS}, 3]
# horizontal flip swaps TL <-> TR and BL <-> BR
flip_idx: [1, 0, 3, 2]

names:
  0: {CLASS_NAME}
"""
    p = out / "optical_frames.yaml"
    p.write_text(txt)
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description="generate the optical-frame pose dataset")
    ap.add_argument("--out", default="datasets/optical")
    ap.add_argument("--train", type=int, default=1500)
    ap.add_argument("--val", type=int, default=300)
    ap.add_argument("--min-level", type=float, default=0.15)
    ap.add_argument("--max-level", type=float, default=1.15)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)
    out = build_dataset(a.out, a.train, a.val, (a.min_level, a.max_level),
                        a.workers, a.seed)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
