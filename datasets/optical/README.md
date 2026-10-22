# Optical frame dataset (sample)

A **sample** of the synthetic captures used to train `models/yolo26n_optical`:
200 train / 60 val images with 4-keypoint pose labels (grid TL, TR, BR, BL of
the coded grid). The full run used 1200/200; the rest was deleted to keep this
workspace small, and the generator is deterministic, so it is one command away:

```bash
python -m optical_modem dataset --out datasets/optical --train 1200 --val 200 --seed 101
```

`manifest_train.json` / `manifest_val.json` record the modulation, ECC, grid
size and channel level used for every original sample.

Label format (one line per image):

```
<class> <cx> <cy> <w> <h> <x1> <y1> <v1> <x2> <y2> <v2> <x3> <y3> <v3> <x4> <y4> <v4>
```
