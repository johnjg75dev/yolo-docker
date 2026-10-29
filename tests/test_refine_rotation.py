"""Regression tests for the refine_rotation hoist (audit M-8).

The original implementation re-ran ``warpAffine`` + ``cv2.integral`` for every
``(ox, oy)`` offset even though ``theta`` -- and therefore the rotated image and
its summed-area table -- is constant across the whole inner loop. Measured on an
864x864x3 canonical image: 295 ms -> 65 ms per attempt, and 4.13x end to end
over four seeds with bit-identical results.

These tests pin both halves of that claim: the *cost* (the integral must be built
once per theta, not once per offset) and the *result* (the optimum must not move).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402

from optical_modem.config import ModemConfig  # noqa: E402
from optical_modem.phy import classical  # noqa: E402
from optical_modem.phy.channel import ChannelConfig, simulate  # noqa: E402
from optical_modem.phy.classical import (  # noqa: E402
    ClassicalReceiver,
    canon_cell_colours,
    integral_of,
    rectify,
)
from optical_modem.phy.demod import PaletteCalibrator  # noqa: E402
from optical_modem.protocol.packet import FRAME_DATA, FrameHeader  # noqa: E402
from optical_modem.sim import prepare_frame  # noqa: E402

ROT_STAGES = ((1.2, 5), (0.4, 3))
# 1 initial evaluation + sum(n) thetas across the stages
EXPECTED_THETAS = 1 + sum(n for _span, n in ROT_STAGES)
OFFSETS_PER_THETA = 5


@pytest.fixture(scope="module")
def scene():
    """One rendered frame pushed through the camera channel, then rectified."""
    cfg = ModemConfig.for_resolution("1280x720", modulation="rgb8", ecc="rs32",
                                     cells=32, symbol_px=16).fitted()
    qd = ClassicalReceiver(cfg).decoder
    mod = cfg.mod()
    layout, rr, cc, _exp = qd._ring_refs(mod)
    _cells_r, rcol = qd._ring_expected_cells(mod, layout)

    hdr = FrameHeader(mod_id=mod.mod_id, ecc_id=cfg.ecc_profile().ecc_id,
                      frame_type=FRAME_DATA, seq=0, length=64)
    frame = prepare_frame(cfg, hdr, bytes(range(64)))
    rng = np.random.default_rng(0)
    ch = ChannelConfig.handheld(rng, 0.25)
    ch.out_size = 720
    cam, quad = simulate(frame, ch, rng)

    canon, K, margin = rectify(cam, quad, layout.grid, K=24)
    cal = PaletteCalibrator(mod)
    # fit the calibrator on the un-rotated ring, as the decoder does
    body0 = canon_cell_colours(integral_of(canon), layout.grid, K, margin, inset=0.06)
    cal.fit(body0[rr, cc], rcol, iters=2)
    return dict(qd=qd, canon=canon, cells=layout.grid, K=K, margin=margin,
                mod=mod, rr=rr, cc=cc, rcol=rcol, cal=cal, quad=quad)


def test_integral_built_once_per_theta(scene, monkeypatch):
    """The whole point of the change: no redundant warps.

    Before the fix this counted ``EXPECTED_THETAS * OFFSETS_PER_THETA`` calls
    (41); it must now count one per distinct theta (9).
    """
    calls = {"integral": 0, "warp": 0}
    real_integral, real_warp = cv2.integral, cv2.warpAffine

    def counting_integral(src, *a, **kw):
        calls["integral"] += 1
        return real_integral(src, *a, **kw)

    def counting_warp(src, *a, **kw):
        calls["warp"] += 1
        return real_warp(src, *a, **kw)

    monkeypatch.setattr(cv2, "integral", counting_integral)
    monkeypatch.setattr(cv2, "warpAffine", counting_warp)

    scene["qd"].refine_rotation(scene["canon"], scene["cells"], scene["K"],
                                scene["margin"], scene["mod"], scene["rr"],
                                scene["cc"], scene["rcol"], scene["cal"],
                                0.0, 0.0, 1.0)

    assert calls["integral"] <= EXPECTED_THETAS, (
        f"built {calls['integral']} summed-area tables for {EXPECTED_THETAS} thetas "
        f"({OFFSETS_PER_THETA} offsets each) -- the hoist has regressed")
    assert calls["warp"] <= EXPECTED_THETAS, (
        f"warped {calls['warp']} times for {EXPECTED_THETAS} thetas")


def test_matches_reference_arithmetic(scene):
    """The optimisation must reach the same optimum, only faster.

    This re-implements the original (unhoisted) loop and asserts agreement, so a
    future refactor that changes the search order or the offset accumulation will
    fail here rather than silently degrading decode accuracy.
    """
    qd, canon = scene["qd"], scene["canon"]
    cells, K, margin = scene["cells"], scene["K"], scene["margin"]
    mod, rr, cc, rcol, cal = (scene["mod"], scene["rr"], scene["cc"],
                              scene["rcol"], scene["cal"])
    h, w = canon.shape[:2]
    ctr = (w / 2.0, h / 2.0)

    def reference(dx, dy, scale, rot_stages=ROT_STAGES):
        def evaluate(theta, ox, oy, sc):
            if abs(theta) < 1e-9:
                integ = integral_of(canon)
            else:
                M = cv2.getRotationMatrix2D(ctr, float(theta), 1.0)
                rot = cv2.warpAffine(canon, M, (w, h), flags=cv2.INTER_LINEAR,
                                     borderMode=cv2.BORDER_REPLICATE)
                integ = integral_of(rot)
            obj, _ = qd._ring_cost(integ, cells, K, margin, mod, rr, cc, rcol,
                                   cal, ox, oy, sc)
            return obj, integ

        best_obj, _ = evaluate(0.0, dx, dy, scale)
        best = (dx, dy, scale, 0.0, best_obj)
        for span, n in rot_stages:
            base_theta = best[3]
            for theta in np.linspace(base_theta - span, base_theta + span, n):
                for ox, oy in ((0.0, 0.0), (0.06, 0.0), (-0.06, 0.0),
                               (0.0, 0.06), (0.0, -0.06)):
                    obj, _integ = evaluate(theta, best[0] + ox, best[1] + oy, best[2])
                    if obj < best[4]:
                        best = (best[0] + ox, best[1] + oy, best[2], float(theta), obj)
        return tuple(float(v) for v in best)

    for start in ((0.0, 0.0, 1.0), (0.04, -0.03, 1.0), (-0.06, 0.05, 0.99)):
        got = qd.refine_rotation(canon, cells, K, margin, mod, rr, cc, rcol, cal,
                                 *start)
        want = reference(*start)
        assert np.allclose(got, want, atol=1e-12, rtol=0), (
            f"refine_rotation({start}) = {got}, reference = {want}")


def test_returns_finite_pose(scene):
    """Guard against the search returning NaN/inf on a real frame."""
    out = scene["qd"].refine_rotation(scene["canon"], scene["cells"], scene["K"],
                                      scene["margin"], scene["mod"], scene["rr"],
                                      scene["cc"], scene["rcol"], scene["cal"],
                                      0.0, 0.0, 1.0)
    assert len(out) == 5
    assert all(np.isfinite(float(v)) for v in out), out
    dx, dy, scale, theta, obj = out
    assert -2.0 <= dx <= 2.0 and -2.0 <= dy <= 2.0, "translation stayed sane"
    assert 0.5 <= scale <= 2.0, "scale stayed sane"
    assert abs(theta) <= 3.0, "rotation stayed within the search span"
