"""Render ``docs/BENCHMARKS.md`` from ``docs/bench_data.json``.

Kept separate from :mod:`scripts.collect_bench_data` so the document can be
re-rendered (or the prose edited) without re-running a 20 minute measurement.

    python scripts/collect_bench_data.py     # measure
    python scripts/render_benchmarks_md.py   # render
"""
from __future__ import annotations

from pathlib import Path

DATA = Path("docs/bench_data.json")
OUT = Path("docs/BENCHMARKS.md")

MODES = ("classical", "yolo", "auto")


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _robustness(data: dict) -> list[str]:
    sec = data.get("robustness")
    if not sec:
        return []
    n = sec["n"]
    L = ["## 1. Robustness vs impairment level", ""]
    L += ["Impairment level scales the camera-channel simulator: 0.0 = clean;",
          "0.2 = mild blur, noise, vignetting, perspective and gamma; 0.5 = the same",
          "with roughly double the blur/noise plus JPEG artefacts and a good chance of",
          "a specular highlight or shadow.", ""]
    L.append(f"{sec['modulation']}, RS({sec['ecc'][2:]}) on a {sec['resolution']} "
             f"display, {n} captures per cell.")
    L.append("")
    L.append("| channel level | " + " | ".join(MODES) + " | "
             + " | ".join(f"{m} ms" for m in MODES) + " |")
    L.append("|---" * (1 + 2 * len(MODES)) + "|")
    by = {(r["level"], r["mode"]): r for r in sec["rows"]}
    for level in sec["level_s"]:
        cells = [f"{level:.2f}"]
        for m in MODES:
            r = by.get((level, m))
            cells.append(f"**{_pct(r['success'])}**" if r else "-")
        for m in MODES:
            r = by.get((level, m))
            cells.append(f"{r['ms']:.0f}" if r else "-")
        L.append("| " + " | ".join(cells) + " |")
    L += ["", "`yolo26` = YOLO26-pose localisation (bundled `yolo26n_optical`",
          "weights) plus geometric refinement; `auto` = YOLO26 region seed **plus**",
          "an unrestricted contour search, best hypothesis wins; `classical` =",
          "contours only. Payload whitening is on.", ""]
    return L


def _detector(data: dict) -> list[str]:
    ev = data.get("model_eval") or {}
    card = data.get("model_card") or {}
    if not ev and not card:
        return []
    L = ["## 2. Corner accuracy of the detector", "",
         "Mean distance between predicted and true grid corners, as a percentage",
         "of the frame side (the metric an OKS-based pose mAP hides: a 40x40 grid",
         "needs corners to ~0.25% of the side *after* refinement, or ~2.5%",
         "before it).", "",
         "| channel level | detection rate | median corner error | p90 |",
         "|---|---|---|---|"]
    for lvl, e in (ev.get("levels") or {}).items():
        y = e.get("yolo", {})
        L.append(f"| {lvl} | {y.get('detection_rate', 0) * 100:.0f}% | "
                 f"{y.get('corner_err_pct_median') or 0:.1f}% | "
                 f"{y.get('corner_err_pct_p90') or 0:.1f}% |")
    L.append("")
    if card:
        res = card.get("results", {})
        L += [f"Validation metrics of the shipped checkpoint: box mAP50-95 = "
              f"{res.get('box_map50_95', 0):.3f}, pose mAP50-95 = "
              f"{res.get('pose_map50_95', 0):.3f} (see [MODEL.md](MODEL.md)).",
              ""]
    L += ["The detector deliberately works at the *region* level; the exact",
          "lattice comes from the calibration ring and `locate.refine_quad*`.", ""]
    return L


def _modulations(data: dict) -> list[str]:
    sec = data.get("modulations")
    if not sec:
        return []
    n = sec["n"]
    levels = sec["levels_s"]
    L = ["## 3. Modulation trade-off", "",
         f"Capacity and decode rate at two impairment levels, {sec['resolution']}",
         f"display, RS({sec['ecc'][2:]}), classical path, {n} captures per cell.",
         "`bytes/frame` is the user payload that fits one frame after FEC and",
         "CRC.", ""]
    L.append("| modulation | bits/cell | bytes/frame | "
             + " | ".join(f"level {l:.2f}" for l in levels) + " |")
    L.append("|---" * (3 + len(levels)) + "|")
    for r in sec["rows"]:
        cells = [r["modulation"], f"{r['bits_per_cell']:.0f}", str(r["n_bytes"])]
        cells += [_pct(r["rates"].get(f"{l:.2f}", 0.0)) for l in levels]
        L.append("| " + " | ".join(cells) + " |")
    L += ["", f"Read this table as *capacity* first and *reliability* second: at "
          f"{n} captures per cell",
          "the sampling error is large, and the residual failures are localisation",
          "or a clipped highlight, not symbol errors — with a perfect quad every one",
          "of these modes round-trips exactly. The patterned modes (`*t4`, `*t16`)",
          "carry more bits per cell at the same grid size, and the",
          "differential-pair modes (`*dp`) halve the payload per cell but survive a",
          "strong colour cast.", ""]
    return L


def _eccs(data: dict) -> list[str]:
    sec = data.get("eccs")
    if not sec:
        return []
    n = sec["n"]
    levels = sec["levels_s"]
    L = ["## 4. ECC strength vs noise", "",
         f"{sec.get('modulation', 'rgb8')}, {sec['resolution']}, classical path,",
         f"{n} captures per cell, payload that fills one frame. Heavier FEC costs",
         "payload bytes and buys noise margin; `none` shows what the CRC alone",
         "achieves.", ""]
    L.append("| ecc | max payload (B) | "
             + " | ".join(f"level {l:.2f}" for l in levels) + " |")
    L.append("|---" * (2 + len(levels)) + "|")
    for r in sec["rows"]:
        cells = [r["ecc"], str(r["max_payload"])]
        cells += [_pct(r["rates"].get(f"{l:.2f}", 0.0)) for l in levels]
        L.append("| " + " | ".join(cells) + " |")
    L += ["", "This is why `receiver.choose_ecc(quality)` exists: the profile should",
          "follow the measured quality rather than being fixed at design time.", ""]
    return L


def _transfer(data: dict) -> list[str]:
    tr = data.get("transfer")
    if not tr:
        return []
    L = ["## 5. Lossy multi-frame transfer (resume)", "",
         f"{tr['bytes'] / 1000:.1f} kB file over {tr.get('modulation', 'rgb8')}"
         f"/RS({tr.get('ecc', 'rs16')[2:]}) at channel level {tr.get('level', 0.35)},",
         f"with {tr.get('drop', 0.35) * 100:.0f}% of frames dropped on top of any "
         "decode failure.", "The receiver retransmits only the frames it missed.", "",
         "| metric | value |", "|---|---|",
         f"| frames | {tr['frames']} |",
         f"| rounds needed | {tr['rounds']} |",
         f"| frames delivered | {tr['delivered']} |",
         f"| wall time | {tr['seconds']} s |",
         f"| effective goodput | {tr['bytes_per_s']} B/s |",
         f"| reassembly verified | {'yes' if tr['complete'] else 'no'} |",
         ""]
    return L


def render(data: dict) -> str:
    meta = data.get("meta", {})
    L = ["# Benchmarks", "",
         "All numbers were measured in this workspace: **CPU only, 2 cores, no",
         "GPU**. `success` means the frame decoded **and** the recovered payload",
         "was byte-identical to what the transmitter sent.", ""]
    if meta.get("generated"):
        L += [f"Measured {meta['generated']}.", ""]
    L += ["Unless stated otherwise: **rgb8, RS(16), a 1280x720 display**, a payload",
          "that fills one frame, payload whitening (mask family 1) **on**, and the",
          "receiver told which modulation to expect (`auto_detect_modulation=False`;",
          "detection scores all 13 modulations against every candidate quad and is",
          "~13x slower).", ""]
    for fn in (_robustness, _detector, _modulations, _eccs, _transfer):
        L += fn(data)
    L += ["## How to reproduce", "", "```bash",
          "python scripts/collect_bench_data.py              # measure everything",
          "python scripts/collect_bench_data.py --quick      # 3 levels, n=3",
          "python scripts/render_benchmarks_md.py            # this document",
          "python -m optical_modem bench --levels 0.0,0.2,0.5 --n 4",
          "```", "",
          "## Honest caveats", "",
          "* Everything here is **simulated**. The channel model is deliberately",
          "  harsh (blur, noise, gamma, vignetting, JPEG, perspective, specular",
          "  highlight, shadow) but it cannot reproduce rolling shutter,",
          "  auto-exposure pumping or screen moire.",
          "* Sample sizes are small (see the `n` of each section). The receiver's",
          "  deep pose search is the cost, so a full run with `n=10` takes tens of",
          "  minutes; the numbers here were measured with `n` as stated and carry",
          "  the matching binomial error.",
          "* Decode latency is 0.5-1.3 s per frame on two CPU cores when the first",
          "  candidate works, and up to ~4 s when the full pose search runs before a",
          "  frame is declared lost (`QuadDecoder.time_budget`).",
          "* The shipped detector is `yolo26n-pose` trained on ~600 synthetic",
          "  captures in ~18 minutes of CPU time; `yolo26s/m` at 640 px with 8k",
          "  samples is the intended production configuration, and retraining on",
          "  **v0.3 frames** (sub-cell patterns, display geometry) is pending.",
          "* Captures that draw a **specular highlight** are the dominant residual",
          "  failure mode: the highlight clips 20-25% of the cells to 255, and",
          "  clipped data is gone regardless of ECC. See the README limitations.",
          "* Rates are for a single exposure; what matters at the system level is",
          "  frames/second x success rate (see section 5).",
          ""]
    return "\n".join(L) + "\n"


def main() -> None:
    import json

    data = json.loads(DATA.read_text())
    for extra in ("model_eval", "model_card"):
        p = {"model_eval": Path("docs/model_eval.json"),
             "model_card": Path("models/yolo26n_optical/weights/model_card.json")}[extra]
        if p.exists() and extra not in data:
            data[extra] = json.loads(p.read_text())
    OUT.write_text(render(data))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
