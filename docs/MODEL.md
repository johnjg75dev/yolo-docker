# YOLO26 frame detector: data, training, results

The receiver has exactly one hard perception problem: *where is the coded grid,
and how is it oriented?* YOLO26 answers it in one forward pass.

## 1. Task definition

| item | choice | why |
|---|---|---|
| Ultralytics task | **pose** (`yolo26*-pose.pt`) | a frame is one object with four corner keypoints; pose gives per-keypoint coordinates *and* a box |
| classes | `0: optical_frame` | one class, no appearance ambiguity |
| keypoints | 4 (`kpt_shape: [4, 3]`) | TL, TR, BR, BL of the **coded grid** |
| keypoint semantics | fixed to the *transmitter's* orientation | the network learns the frame's handedness, so the receiver does not have to search 4 rotations (the ring check still verifies it) |
| `flip_idx` | `[1, 0, 3, 2]` | a horizontal flip swaps TL↔TR and BL↔BR, keeping augmentation labels consistent |
| input size | 448 px (CPU build) / 640 px (GPU) | 448 keeps a 360 px frame at ≥ 1 sample per cell |

Why keypoints instead of a plain bounding box: a rotated/perspective-distorted
frame is not axis-aligned, and a box corner tells you nothing about which way is
up. Four corner keypoints give the full homography needed to sample the grid.

## 2. Synthetic dataset

`optical_modem/data/synth.py`

Each sample:

1. picks a random **modulation** (`gray2/gray4/rgb8/rgb8x2`), **ECC profile**,
   **grid** (28–36), **cell size** (8–12 px), quiet-zone width, and a payload
   length that fits that frame's real capacity;
2. renders the frame (`phy/render.py`) with a real header (random type/seq) and
   random payload bytes;
3. pushes it through the **camera-channel simulator** (`phy/channel.py`):
   random perspective (corner jitter up to 16% of the side), rotation ±28°,
   scale 45–98%, background 12–110, gamma 0.6–1.5, brightness/contrast, per-channel
   white balance, vignetting, Gaussian blur 0–2.2 px, sensor noise 0–9 σ, JPEG
   35–95, optional specular highlight, optional shadow blob;
4. computes the four grid corners **analytically** through the same homography
   that produced the image — the labels are exact, not hand-annotated.

25% of the samples are deliberately easy (low impairment) so the model also
learns sub-cell-precise corners; the rest ramp up to severe.

```bash
python -m optical_modem dataset --out datasets/optical --train 1200 --val 200
# -> datasets/optical/{images,labels}/{train,val}, optical_frames.yaml
```

The bundled model used 600 training images (a subset list is written next to the
yaml) and 200 validation images.

## 3. Training recipe (the run shipped here)

CPU-only box (2 cores, 1.5 GB RAM), which is why the small model and 384 px:

```bash
python -m optical_modem.data.train_yolo \
    --model yolo26n-pose.pt \
    --data datasets/optical/optical_frames_subset.yaml \
    --epochs 12 --imgsz 384 --batch 4 --workers 0 --device cpu \
    --mosaic 0.2 --project models --name yolo26n_optical
```

Augmentation tuned for a *rigid, planar, square* target: rotation ±25°, scale
0.35, translation 0.10, shear 2°, mild perspective, horizontal flip (semantics
preserved by `flip_idx`), low mosaic, no mixup/copy-paste. Ultralytics
re-initialises the pose head for 4 keypoints while transferring the detection
backbone — which is why the first epoch already has a high box mAP but a poor
pose mAP.

Everything needed downstream is written to `weights/model_card.json`: keypoint
order, `kpt_shape`, classes, training config, final metrics.

## 4. Results

Training finished in **1054 s** (12 epochs, 600 images, 384 px, batch 4, 2 CPU
cores). Validation split = 200 held-out synthetic captures:

| metric | value |
|---|---|
| box mAP50 | 0.985 |
| box mAP50-95 | 0.758 |
| pose mAP50 | 0.981 |
| pose mAP50-95 | 0.974 |
| detection rate (this workspace, 24 captures/level) | 100% |

### The metric that matters is not pose mAP

OKS-based pose mAP is very forgiving at this scale (with four keypoints the
default sigma is 1/N of the object size), so a model can score 0.97 while being
two cells out. Measuring corner error directly
(`scripts/evaluate.py`, `docs/model_eval.json`):

| channel level | detection rate | median corner error | p90 |
|---|---|---|---|
| 0.20 | 100% | 5.7% of side (≈1.8 cells) | 6.6% |
| 0.60 | 100% | 6.6% (≈2.1 cells) | 11.3% |
| 1.00 | 100% | 9.2% (≈2.9 cells) | 16.2% |

Sampling a 32×32 grid needs ≈0.1 cell accuracy, so *no* detector of this size
is precise enough on its own. That is why the architecture uses the prediction
as a **region seed**: contours inside the seeded region generate a small set of
geometrically plausible grids, and the known calibration ring picks the right
one (median ring score > 0.95, asserted in `tests/test_detector.py`).

### End-to-end effect (24 captures per level)

| channel level | classical only | YOLO seeded | auto (seed + search) |
|---|---|---|---|
| 0.20 | 100% | 100% | 100% |
| 0.60 | 67% | 75% | 75% |
| 1.00 | 25% | 17% | 17% |

At severe impairment the detector is the most reliable way to *find* the frame;
at extreme levels the bottleneck moves to signal quality (blur/noise), which no
localiser can fix. A larger model at 640 px would push the corner error down
roughly by half and remove the seeding step's error budget — see below.

## 5. Scaling to YOLO26s / YOLO26m

Same script, bigger checkpoint and a GPU:

```bash
python -m optical_modem dataset --train 8000 --val 800          # data scales too
python -m optical_modem.data.train_yolo \
    --model yolo26s-pose.pt --epochs 120 --imgsz 640 --batch 32 --device 0
# or --model yolo26m-pose.pt
```

Expected effect: corner error roughly halves at 640 px (more pixels per cell),
and the model tolerates heavier blur/motion — the two situations where the
448 px CPU model starts to lose frames. Runtime on the receiver side scales
roughly linearly with parameter count: n ≈ 2.5 M, s ≈ 10 M, m ≈ 21 M params.
Nothing else in the stack changes: `--weights path/to/best.pt` selects the model
(`models/yolo26_optical/weights/best.pt` is the default).

## 6. Fine-tuning on real captures

The synthetic pipeline is a bootstrap, not the end state. To adapt:

1. Capture ~500–1000 photos of your own screen with the target phone/WebCam,
   at different angles, distances, brightness levels and content.
2. Label with any 4-keypoint pose tool (CVAT, Label Studio, Roboflow) using the
   same keypoint order and the same `optical_frames.yaml` schema
   (`kpt_shape: [4, 3]`, `flip_idx: [1, 0, 3, 2]`).
3. Fine-tune from the bundled weights for 20–40 epochs with `--mosaic 0`.

Tip: you can bootstrap labelling by running the current model, then correcting
the predicted corners — corner predictions are usually close enough to be
dragged into place in seconds.

## 7. Why not let the network decode as well?

A tempting idea is to feed the rectified grid into a CNN and read out bits
("most of the detection *and* decoding"). In practice that is a bad trade:

* the demodulator already has an *exact* model (known palette, known calibration
  ring), and it outputs LLRs that feed soft-decision FEC — a CNN would have to
  relearn arithmetic it can compute perfectly;
* ECC gain dominates: Reed-Solomon with erasures beats any learned bit reader at
  this symbol count, and it has a provable error bound;
* a learned decoder would silently fail (confidently wrong bits) in exactly the
  lighting conditions where you need the FEC the most.

So YOLO26 handles the part it is uniquely good at — geometric perception under
nasty photometry — and hands four numbers to a deterministic pipeline.
