# Benchmarks

All numbers were measured in this workspace: **CPU only, 2 cores, no
GPU**. `success` means the frame decoded **and** the recovered payload
was byte-identical to what the transmitter sent.

Measured 2026-10-22 00:10.

Unless stated otherwise: **rgb8, RS(16), a 1280x720 display**, a payload
that fills one frame, payload whitening (mask family 1) **on**, and the
receiver told which modulation to expect (`auto_detect_modulation=False`;
detection scores all 13 modulations against every candidate quad and is
~13x slower).

## 1. Robustness vs impairment level

Impairment level scales the camera-channel simulator: 0.0 = clean;
0.2 = mild blur, noise, vignetting, perspective and gamma; 0.5 = the same
with roughly double the blur/noise plus JPEG artefacts and a good chance of
a specular highlight or shadow.

rgb8, RS(16) on a 1280x720 display, 6 captures per cell.

| channel level | classical | yolo | auto | classical ms | yolo ms | auto ms |
|---|---|---|---|---|---|---|
| 0.00 | **100%** | **100%** | **100%** | 807 | 1184 | 1218 |
| 0.20 | **100%** | **83%** | **100%** | 783 | 553 | 738 |
| 0.50 | **67%** | **33%** | **50%** | 782 | 723 | 841 |

`yolo26` = YOLO26-pose localisation (bundled `yolo26n_optical`
weights) plus geometric refinement; `auto` = YOLO26 region seed **plus**
an unrestricted contour search, best hypothesis wins; `classical` =
contours only. Payload whitening is on.

## 2. Corner accuracy of the detector

Mean distance between predicted and true grid corners, as a percentage
of the frame side (the metric an OKS-based pose mAP hides: a 40x40 grid
needs corners to ~0.25% of the side *after* refinement, or ~2.5%
before it).

| channel level | detection rate | median corner error | p90 |
|---|---|---|---|
| 0.20 | 100% | 5.7% | 6.6% |
| 0.60 | 100% | 6.6% | 11.3% |
| 1.00 | 100% | 9.2% | 16.2% |

Validation metrics of the shipped checkpoint: box mAP50-95 = 0.758, pose mAP50-95 = 0.974 (see [MODEL.md](MODEL.md)).

The detector deliberately works at the *region* level; the exact
lattice comes from the calibration ring and `locate.refine_quad*`.

## 3. Modulation trade-off

Capacity and decode rate at two impairment levels, 1280x720
display, RS(16), classical path, 4 captures per cell.
`bytes/frame` is the user payload that fits one frame after FEC and
CRC.

| modulation | bits/cell | bytes/frame | level 0.10 | level 0.25 |
|---|---|---|---|---|
| gray2 | 1 | 237 | 100% | 75% |
| gray4 | 2 | 478 | 75% | 100% |
| rgb8 | 3 | 726 | 75% | 75% |
| rgb8x2 | 4 | 974 | 75% | 0% |
| rgb16 | 4 | 974 | 75% | 0% |
| rgb16dp | 2 | 472 | 100% | 100% |
| rgb32 | 5 | 1222 | 25% | 50% |
| rgb32dp | 2 | 582 | 50% | 50% |
| rgb8t4 | 5 | 587 | 75% | 75% |
| rgb16t4 | 6 | 715 | 75% | 75% |
| rgb32t4 | 7 | 826 | 50% | 75% |
| rgb32t16 | 9 | 1065 | 25% | 75% |
| rgb16t4dp | 3 | 326 | 50% | 50% |

Read this table as *capacity* first and *reliability* second: at 4 captures per cell
the sampling error is large, and the residual failures are localisation
or a clipped highlight, not symbol errors — with a perfect quad every one
of these modes round-trips exactly. The patterned modes (`*t4`, `*t16`)
carry more bits per cell at the same grid size, and the
differential-pair modes (`*dp`) halve the payload per cell but survive a
strong colour cast.

## 4. ECC strength vs noise

rgb8, 1280x720, classical path,
4 captures per cell, payload that fills one frame. Heavier FEC costs
payload bytes and buys noise margin; `none` shows what the CRC alone
achieves.

| ecc | max payload (B) | level 0.25 | level 0.45 |
|---|---|---|---|
| rs8 | 758 | 50% | 50% |
| rs16 | 726 | 75% | 75% |
| rs32 | 667 | 75% | 25% |
| rs64 | 571 | 100% | 75% |
| ldpc12 | 362 | 100% | 50% |
| ldpc23 | 526 | 100% | 75% |
| none | 790 | 75% | 0% |

This is why `receiver.choose_ecc(quality)` exists: the profile should
follow the measured quality rather than being fixed at design time.

## 5. Lossy multi-frame transfer (resume)

4.0 kB file over rgb8/RS(16) at channel level 0.35,
with 35% of frames dropped on top of any decode failure.
The receiver retransmits only the frames it missed.

| metric | value |
|---|---|
| frames | 6 |
| rounds needed | 5 |
| frames delivered | 9 |
| wall time | 21.3 s |
| effective goodput | 187.6 B/s |
| reassembly verified | yes |

## How to reproduce

```bash
python scripts/collect_bench_data.py              # measure everything
python scripts/collect_bench_data.py --quick      # 3 levels, n=3
python scripts/render_benchmarks_md.py            # this document
python -m optical_modem bench --levels 0.0,0.2,0.5 --n 4
```

## Honest caveats

* Everything here is **simulated**. The channel model is deliberately
  harsh (blur, noise, gamma, vignetting, JPEG, perspective, specular
  highlight, shadow) but it cannot reproduce rolling shutter,
  auto-exposure pumping or screen moire.
* Sample sizes are small (see the `n` of each section). The receiver's
  deep pose search is the cost, so a full run with `n=10` takes tens of
  minutes; the numbers here were measured with `n` as stated and carry
  the matching binomial error.
* Decode latency is 0.5-1.3 s per frame on two CPU cores when the first
  candidate works, and up to ~4 s when the full pose search runs before a
  frame is declared lost (`QuadDecoder.time_budget`).
* The shipped detector is `yolo26n-pose` trained on ~600 synthetic
  captures in ~18 minutes of CPU time; `yolo26s/m` at 640 px with 8k
  samples is the intended production configuration, and retraining on
  **v0.3 frames** (sub-cell patterns, display geometry) is pending.
* Captures that draw a **specular highlight** are the dominant residual
  failure mode: the highlight clips 20-25% of the cells to 255, and
  clipped data is gone regardless of ECC. See the README limitations.
* Rates are for a single exposure; what matters at the system level is
  frames/second x success rate (see section 5).

