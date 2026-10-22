"""Benchmark harness: robustness, display capacity and ablations.

Produces the tables in ``docs/BENCHMARKS.md``:

1. **Display matrix** – for every display resolution x modulation: cells that
   fit, payload bytes per frame, decode success on simulated captures and the
   resulting goodput at 10 frames/s.
2. **Robustness vs channel level** – success rate and latency for the classical
   and the YOLO26 detector at several impairment severities.
3. **Protocol matrix** – modulation x ECC at one display profile.
4. **Ablations** – what each receiver stage buys (ring alignment, illumination
   normalisation, soft erasures).

All trials go through :mod:`optical_modem.sim`, so the transmitter renders a
real screen canvas and the simulated camera frames the code — the same path the
GUI and the CLI use.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .config import ECC_PROFILES, MODULATIONS, ModemConfig
from .phy.channel import ChannelConfig
from .protocol.packet import FrameHeader
from .sim import capture, prepare_frame


@dataclass
class BenchRow:
    mode: str
    level: float
    modulation: str
    ecc: str
    n: int
    success: float
    ms_mean: float
    ms_p50: float
    ring_mean: float
    payload_bytes: int
    goodput_bps_at_10fps: float
    resolution: str = ""
    cells: int = 0
    cell_px: int = 0
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# trial construction
# --------------------------------------------------------------------------- #
def make_trial(cfg: ModemConfig, n_bytes: int | None, rng):
    """Return ``(payload, screen_image, header)`` for one benchmark trial."""
    cap = cfg.describe()["max_user_payload"]
    n = cap if n_bytes is None else max(1, min(n_bytes, cap))
    payload = bytes(rng.integers(0, 256, n, dtype=np.uint8))
    mod = cfg.mod()
    hdr = FrameHeader(mod.mod_id, cfg.ecc_profile().ecc_id, 0, 1, len(payload) + 2)
    return payload, prepare_frame(cfg, hdr, payload), hdr


def _row_meta(cfg: ModemConfig, payload: bytes, ok: int, n: int,
              times: list[float], rings: list[float]) -> dict:
    return {
        "payload_bytes": len(payload),
        "goodput_bps_at_10fps": len(payload) * 8 * 10 * (ok / max(1, n)),
        "ms_mean": float(np.mean(times) * 1000) if times else 0.0,
        "ms_p50": float(np.median(times) * 1000) if times else 0.0,
        "ring_mean": float(np.mean(rings)) if rings else 0.0,
        "resolution": cfg.display.label if cfg.display else "frame",
        "cells": cfg.describe()["cells"],
        "cell_px": cfg.cell_px,
    }


# --------------------------------------------------------------------------- #
# benchmarks
# --------------------------------------------------------------------------- #
def bench_display(resolutions=("1280x720", "1920x1080", "2560x1440", "3840x2160"),
                  modulations=None, ecc="rs32", level=0.35, n=4, seed=0,
                  mode="classical", symbol_px=12, camera_px=1080):
    """Resolution x modulation: capacity and measured success."""
    from .receiver import HybridReceiver

    modulations = modulations or ["rgb8", "rgb16", "rgb32", "rgb16t4",
                                  "rgb32t4", "rgb32t16"]
    rng = np.random.default_rng(seed)
    rows: list[BenchRow] = []
    for res in resolutions:
        for mod in modulations:
            cfg = ModemConfig.for_resolution(res, modulation=mod, ecc=ecc,
                                             symbol_px=symbol_px)
            payload, screen, _ = make_trial(cfg, None, rng)
            rx = HybridReceiver(cfg, mode=mode)
            ok, times, rings = 0, [], []
            for _ in range(n):
                ch = ChannelConfig.random(rng, level)
                ch.out_size = camera_px
                cam, _quad = capture(cfg, screen, ch, rng)
                rep = rx.decode(cam)
                times.append(rep.timings.get("total", 0.0))
                if rep.ok and rep.payload == payload:
                    ok += 1
                best = rep.best()
                if best is not None:
                    rings.append(best.ring_score)
            rows.append(BenchRow(
                mode=mode, level=level, modulation=mod, ecc=ecc, n=n,
                success=ok / n,
                **_row_meta(cfg, payload, ok, n, times, rings),
            ))
    return rows


def bench_robustness(levels, n=10, modes=("classical", "yolo"), cfg=None,
                     weights=None, imgsz=448, seed=0, n_bytes=None,
                     resolution="1920x1080", modulation="rgb32"):
    """Success vs impairment level for each detector."""
    from .receiver import HybridReceiver

    if cfg is None:
        cfg = (ModemConfig.for_resolution(resolution, modulation=modulation)
               if resolution and resolution != "frame"
               else ModemConfig(modulation=modulation, ecc="rs32"))
    rng = np.random.default_rng(seed)
    payload, screen, _ = make_trial(cfg, n_bytes, rng)
    receivers = {}
    for mode in modes:
        try:
            receivers[mode] = HybridReceiver(cfg, mode=mode, yolo_weights=weights,
                                             yolo_imgsz=imgsz)
        except Exception as exc:
            print(f"[bench] mode {mode} unavailable: {exc}")
    rows = []
    for level in levels:
        print(f"[bench] level {level:.2f}: capturing {n} frames", flush=True)
        captures = []
        for _ in range(n):
            ch = ChannelConfig.random(rng, level)
            if cfg.display is not None:
                ch.out_size = 1080
            captures.append(capture(cfg, screen, ch, rng)[0])
        for mode, rx in receivers.items():
            ok, times, rings = 0, [], []
            print(f"[bench] level {level:.2f} mode {mode}", flush=True)
            for img in captures:
                rep = rx.decode(img)
                times.append(rep.timings.get("total", 0.0))
                if rep.ok and rep.payload == payload:
                    ok += 1
                best = rep.best()
                if best is not None:
                    rings.append(best.ring_score)
            rows.append(BenchRow(
                mode=mode, level=level, modulation=cfg.modulation, ecc=cfg.ecc,
                n=n, success=ok / n,
                **_row_meta(cfg, payload, ok, n, times, rings),
            ))
    return rows


def bench_protocol_matrix(modulations=None, eccs=None, level=0.35, n=4,
                          seed=3, resolution="1920x1080", symbol_px=12):
    """modulation x ECC: how much fits in one frame and how reliably it decodes."""
    from .receiver import HybridReceiver

    modulations = modulations or list(MODULATIONS)
    eccs = eccs or [e for e in ECC_PROFILES if e != "none"]
    rng = np.random.default_rng(seed)
    rows = []
    for mod in modulations:
        for ecc in eccs:
            cfg = ModemConfig.for_resolution(resolution, modulation=mod,
                                             ecc=ecc, symbol_px=symbol_px)
            rx = HybridReceiver(cfg, mode="classical")
            payload, screen, _ = make_trial(cfg, None, rng)
            ok, times, rings = 0, [], []
            for _ in range(n):
                ch = ChannelConfig.random(rng, level)
                ch.out_size = 1080
                cam, _ = capture(cfg, screen, ch, rng)
                rep = rx.decode(cam)
                times.append(rep.timings.get("total", 0.0))
                if rep.ok and rep.payload == payload:
                    ok += 1
                best = rep.best()
                if best is not None:
                    rings.append(best.ring_score)
            row = BenchRow(mode="matrix", level=level, modulation=mod, ecc=ecc,
                           n=n, success=ok / n,
                           **_row_meta(cfg, payload, ok, n, times, rings))
            row.extra["matrix"] = True
            rows.append(row)
    return rows


def bench_ablation(levels=(0.2, 0.5, 0.8), n=6, seed=5,
                   resolution="1920x1080", modulation="rgb32", cfg=None):
    """Turn receiver features off one by one on the classical path."""
    import optical_modem.phy.classical as cls
    from .receiver import HybridReceiver

    if cfg is None:
        cfg = ModemConfig.for_resolution(resolution, modulation=modulation,
                                         ecc="rs32")
    rng = np.random.default_rng(seed)
    payload, screen, _ = make_trial(cfg, None, rng)

    orig_norm = cls.illum_normalize
    orig_align = cls.QuadDecoder.align_by_ring

    def no_align(self, integ, cells, K, margin, mod, rr, cc, rcol, **kw):
        return 0, 0.0, 0.0, 0.0, 1.0

    variants = {
        "full (ring align + norm + soft)": (orig_align, orig_norm, True),
        "no soft erasures": (orig_align, orig_norm, False),
        "no illum normalisation": (orig_align, lambda x, *a, **k: x, True),
        "no ring alignment": (no_align, orig_norm, True),
    }
    rows = []
    for level in levels:
        print(f"[bench] level {level:.2f}: capturing {n} frames", flush=True)
        captures = []
        for _ in range(n):
            ch = ChannelConfig.random(rng, level)
            ch.out_size = 1080
            captures.append(capture(cfg, screen, ch, rng)[0])
        for name, (align, norm, soft) in variants.items():
            cls.QuadDecoder.align_by_ring = align
            cls.illum_normalize = norm
            rx = HybridReceiver(cfg, mode="classical")
            ok = 0
            for img in captures:
                rep = _decode_hard(rx, img) if not soft else rx.decode(img)
                if rep.ok and rep.payload == payload:
                    ok += 1
            rows.append(BenchRow("ablation", level, cfg.modulation, cfg.ecc, n,
                                 ok / n, 0.0, 0.0, 0.0, len(payload),
                                 len(payload) * 8 * 10 * ok / n,
                                 resolution=resolution,
                                 cells=cfg.describe()["cells"],
                                 cell_px=cfg.cell_px,
                                 extra={"variant": name}))
    cls.QuadDecoder.align_by_ring = orig_align
    cls.illum_normalize = orig_norm
    return rows


def _decode_hard(rx, img):
    """Same path but with soft (erasure) information removed."""
    import optical_modem.protocol.frames as frames

    orig = frames.FrameCodec.decode_grid

    def hard(self, values, header=None, soft=None):
        return orig(self, values, header, None)

    frames.FrameCodec.decode_grid = hard
    try:
        return rx.decode(img)
    finally:
        frames.FrameCodec.decode_grid = orig


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
def _fmt_md_table(header: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join(["---"] * len(header)) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def write_report(rows: list[BenchRow], path="docs/BENCHMARKS.md",
                 extra: dict | None = None):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    display = [r for r in rows if r.mode in ("classical", "yolo")
               and r.resolution not in ("", "frame")]
    rob = [r for r in rows if r.mode in ("classical", "yolo")
           and r.resolution in ("", "frame")]
    matrix = [r for r in rows if r.mode == "matrix"]
    abl = [r for r in rows if r.mode == "ablation"]

    lines = ["# Benchmark results", "",
             "Generated by `python -m optical_modem bench`. Simulated "
             "screen \u2192 camera captures on CPU only; a capture counts as a "
             "success only when the payload is byte-identical to what was sent.",
             ""]
    if display:
        lines += ["## Display matrix", "",
                  "The transmitter fills a real display with `cell_px` pixels per "
                  "cell; the simulated camera frames the code and captures at "
                  "1080p. Impairment level 0.35 (handheld-ish: blur, noise, "
                  "gamma/white-balance drift, JPEG, slight perspective).", ""]
        hdr = ["display", "modulation", "cells", "px/cell", "payload B",
               "success", "ms/frame", "kB/s @10fps"]
        body = []
        for r in sorted(display, key=lambda r: (r.resolution, r.modulation)):
            body.append([r.resolution, r.modulation, str(r.cells),
                         str(r.cell_px), str(r.payload_bytes),
                         f"{r.success * 100:.0f}%", f"{r.ms_mean:.0f}",
                         f"{r.payload_bytes * r.success * 10 / 1024:.1f}"])
        lines += _fmt_md_table(hdr, body) + [""]
    if rob:
        modes = sorted({r.mode for r in rob})
        lines += ["## Robustness vs impairment level", ""]
        hdr = ["level"] + [f"{m} success" for m in modes] + \
              [f"{m} ms" for m in modes]
        body = []
        for level in sorted({r.level for r in rob}):
            row = [f"{level:.2f}"]
            for m in modes:
                rr = [r for r in rob if r.level == level and r.mode == m]
                row.append(f"{rr[0].success * 100:.0f}%" if rr else "-")
            for m in modes:
                rr = [r for r in rob if r.level == level and r.mode == m]
                row.append(f"{rr[0].ms_mean:.0f}" if rr else "-")
            body.append(row)
        lines += _fmt_md_table(hdr, body) + [""]
    if matrix:
        lines += ["## Protocol matrix (modulation x ECC)", ""]
        hdr = ["modulation", "ecc", "bits/cell", "payload B", "kB/s @10fps",
               "success"]
        body = []
        for r in matrix:
            body.append([r.modulation, r.ecc,
                         f"{MODULATIONS[r.modulation].bits:.1f}",
                         str(r.payload_bytes),
                         f"{r.payload_bytes * r.success * 10 / 1024:.1f}",
                         f"{r.success * 100:.0f}%"])
        lines += _fmt_md_table(hdr, body) + [""]
    if abl:
        lines += ["## Ablation (classical receiver)", ""]
        hdr = ["level", "variant", "success"]
        body = [[f"{r.level:.2f}", str(r.extra.get("variant")),
                 f"{r.success * 100:.0f}%"] for r in abl]
        lines += _fmt_md_table(hdr, body) + [""]
    if extra:
        lines += ["## Notes", ""]
        for k, v in extra.items():
            lines.append(f"* **{k}**: {v}")
    p.write_text("\n".join(lines) + "\n")
    return p


def run_benchmark(levels=(0.0, 0.3, 0.6, 0.9), n=8,
                  modes=("classical",), weights=None, imgsz=448, seed=0,
                  with_display=True, with_matrix=False, with_ablation=False,
                  resolution="1920x1080", modulation="rgb32"):
    rows: list[BenchRow] = []
    if with_display:
        rows += bench_display(level=0.35, n=max(2, n // 2), seed=seed)
    rows += bench_robustness(levels, n=n, modes=modes, weights=weights,
                             imgsz=imgsz, seed=seed, resolution=resolution,
                             modulation=modulation)
    if with_ablation:
        rows += bench_ablation(seed=seed, n=max(2, n // 2))
    if with_matrix:
        rows += bench_protocol_matrix(seed=seed, n=max(2, n // 2))
    return rows
