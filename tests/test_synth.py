"""Dataset generator + job-runner regressions.

Both bugs covered here were found by training for real, not by unit tests:

* a frame that shouldered partly off the canvas produced ``cx < 0`` in the YOLO
  label, and Ultralytics quietly dropped the image ("ignoring corrupt
  image/label: negative label values") *after* the dataloader had already
  cached the split — so the run simply trained on fewer images;
* a training job that the kernel killed (``-9``) was reported with an empty
  message, which reads like "nothing happened" in the UI.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from optical_modem.data.synth import SampleSpec, build_dataset, make_sample, write_yaml
from optical_modem.suite.jobs import FAILED, JobManager, _failure_reason


def _assert_valid_label(text: str) -> None:
    vals = [float(v) for v in text.split()]
    assert len(vals) == 17, "1 box (5) + 4 keypoints (12) values"
    assert vals[0] == 0, "single class: optical_frame"
    cx, cy, bw, bh = vals[1:5]
    for v in (cx, cy, bw, bh):
        assert 0.0 <= v <= 1.0, f"box value out of range: {text}"
    assert bw > 0 and bh > 0
    for i in range(5, 17, 3):
        x, y, vis = vals[i], vals[i + 1], vals[i + 2]
        assert 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0, f"keypoint out of range: {text}"
        assert vis in (0.0, 1.0, 2.0), f"visibility must be 0/1/2: {text}"


@pytest.mark.parametrize("seed", [240, 1, 2, 3])
def test_hard_samples_still_write_valid_labels(tmp_path: Path, seed: int) -> None:
    """A hard channel must never yield negative *written* coordinates.

    Keypoints may legitimately sit a little outside the canvas (vis=0); the
    loader, however, rejects a negative box centre, so the writer clamps.
    """
    from optical_modem.data.synth import _write_one

    spec = SampleSpec(seed=seed, modulation="rgb8", ecc="rs16", cell_px=10,
                      grid=32, quiet_zone_cells=2, level=1.1, out_size=560)
    img_p, lbl_p = tmp_path / "a.jpg", tmp_path / "a.txt"
    res = _write_one((str(img_p), str(lbl_p), spec))
    assert res is not None
    if "skipped" in res:                     # degenerate view: nothing written
        assert not lbl_p.exists() and not img_p.exists()
    else:
        _assert_valid_label(lbl_p.read_text())
        img, _, meta = make_sample(spec)
        assert img.ndim == 3 and img.shape[2] == 3
        assert meta["grid"] == 32 and meta["cell_px"] > 0


def test_build_dataset_writes_loadable_labels(tmp_path: Path) -> None:
    out = tmp_path / "ds"
    summary = build_dataset(out, n_train=6, n_val=2, level_range=(0.4, 1.1),
                            workers=1, seed0=7)
    assert summary["train"]["written"] + summary["train"]["skipped"] == 6
    assert summary["train"]["errors"] == 0
    labels = sorted((out / "labels" / "train").glob("*.txt"))
    assert labels, "no labels written"
    for lbl in labels:
        _assert_valid_label(lbl.read_text())
        assert (out / "images" / "train" / (lbl.stem + ".jpg")).exists()
    yaml_path = write_yaml(out)
    assert "kpt_shape: [4, 3]" in yaml_path.read_text()


# --------------------------------------------------------------------------- #
# job failure reporting
# --------------------------------------------------------------------------- #
def test_failure_reason_explains_sigkill() -> None:
    msg = _failure_reason(-9, "")
    assert "SIGKILL" in msg and "memory" in msg


def test_failure_reason_includes_last_log_line() -> None:
    text = "$ python -m x\n\n  1/10  ...\nValueError: bad frame header   \n"
    assert "ValueError: bad frame header" in _failure_reason(3, text)


def test_failed_job_surfaces_a_message(tmp_path: Path) -> None:
    mgr = JobManager(tmp_path, python="python3", env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
                     project_root=tmp_path)
    job = mgr.start("train", "boom", ["python3", "-c", "raise SystemExit(7)"])
    import time
    deadline = time.time() + 30
    while job.live and time.time() < deadline:
        time.sleep(0.05)
    assert job.status == FAILED
    snap = mgr.snapshot(job.id)
    assert snap is not None
    assert "exit code 7" in snap["message"], snap["message"]
    on_disk = json.loads((tmp_path / "jobs" / job.id / "job.json").read_text())
    assert on_disk["status"] == FAILED
