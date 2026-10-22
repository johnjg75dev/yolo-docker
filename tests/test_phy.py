"""Physical layer tests: tile renderer, calibration ring, demodulation, display fitting."""
import dataclasses

import numpy as np
import pytest

from optical_modem.config import (
    MODULATIONS,
    DisplayProfile,
    ModemConfig,
    get_modulation,
    min_palette_distance,
    pattern_min_distance,
)
from optical_modem.phy.align import quad_from_alignment, sharpness
from optical_modem.phy.channel import ChannelConfig, simulate, simulate_screen
from optical_modem.phy.classical import (
    ClassicalReceiver,
    canon_cell_colours,
    canon_tiles,
    cell_homography,
    integral_of,
    rectify,
    sample_at,
)
from optical_modem.phy.demod import TileCalibrator, illum_normalize
from optical_modem.phy.locate import Quad, detect_candidates, order_quad, quad_from_border
from optical_modem.phy.render import render_frame, render_screen, screen_geometry
from optical_modem.protocol.frames import FrameCodec, effective_layout
from optical_modem.protocol.packet import FrameHeader
from optical_modem.sim import capture, prepare_frame


def _frame(cfg, payload=b"hello optical modem"):
    hdr = FrameHeader(cfg.mod().mod_id, cfg.ecc_profile().ecc_id, 0, 1,
                      len(payload) + 2)
    return render_frame(cfg, hdr, payload), hdr, payload


def _gt_quad(cfg) -> np.ndarray:
    layout = effective_layout(cfg)
    qz = cfg.quiet_zone_cells
    g = layout.cells
    return np.array([[qz, qz], [qz + g, qz], [qz + g, qz + g], [qz, qz + g]],
                    np.float32) * cfg.cell_px


def _decode_with_quad(cfg, img, quad, payload):
    rx = ClassicalReceiver(cfg)
    rx.decoder._img = img
    att = rx.decoder.score_hypotheses(img, [Quad(quad, 1.0, "gt")], cfg.mod())[0]
    return rx.decoder.decode_attempt(att, cfg.mod())


# --------------------------------------------------------------------------- #
# modulation catalogue
# --------------------------------------------------------------------------- #
def test_palette_is_closed_under_hue_180():
    """Differential pair modes need every colour's hue twin inside the palette."""
    for name in ("rgb16dp", "rgb32dp", "rgb16t4dp"):
        mod = get_modulation(name)
        assert mod.pair
        for i in range(mod.n_colors):
            j = mod.pair_partner(i)
            assert 0 <= j < mod.n_colors
            assert mod.pair_partner(j) == i
            # same value/saturation, opposite hue: the RGB vectors are far apart
            assert np.linalg.norm(np.subtract(mod.palette[i], mod.palette[j])) > 60


def test_palette_separation_and_lightness():
    """Every palette must be decodable *and* never near the tile background."""
    from optical_modem.phy.render import DARK

    for name, mod in MODULATIONS.items():
        assert min_palette_distance(mod.palette) > 40, name
        darkest = min(max(c) for c in mod.palette)
        # patterned tiles read their holes against the colour in the *max*
        # channel, so a colour must stay well above the background (DARK = 16)
        # even after a camera squashes the tone curve
        assert darkest >= (75 if mod.pattern_bits else 45), (name, darkest)


def test_pattern_set_distance_and_solid_first():
    from optical_modem.phy.demod import TileCalibrator

    for name, mod in MODULATIONS.items():
        if not mod.pattern_bits:
            assert mod.n_patterns == 1
            continue
        masks = np.asarray(mod.pattern_mask)
        assert masks[0].all(), "pattern 0 must be solid (used by the ring)"
        # the centre block is always on: colour is read from there
        P = masks.shape[-1]
        assert masks[:, P // 4:P - P // 4, P // 4:P - P // 4].all()
        assert pattern_min_distance(mod.pattern_mask) >= 4, name
        assert TileCalibrator(mod).npat == mod.n_patterns


def test_cells_per_symbol_and_bits():
    for name, mod in MODULATIONS.items():
        assert mod.bits == mod.color_bits + mod.pattern_bits
        expected_cps = 2 if mod.pair else 1
        assert mod.cells_per_symbol == expected_cps
        assert mod.n_colors == 1 << mod.color_bits
        assert mod.n_patterns == 1 << mod.pattern_bits


# --------------------------------------------------------------------------- #
# display fitting
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("res,expect", [("1920x1080", (1920, 1080)),
                                        ("720p", (1280, 720)),
                                        ("4k", (3840, 2160))])
def test_display_parse_and_fit(res, expect):
    disp = DisplayProfile.parse(res)
    assert (disp.width, disp.height) == expect
    cells, px = disp.fit()
    assert px >= 1
    # the *drawn* code is (cells + 2 quiet-zone cells) wide — that is what has
    # to fit, and it must nearly fill the short side
    total = cells + 4
    assert total * px <= disp.usable
    assert disp.usable - total * px < px


def test_config_for_resolution_shrinks_with_bigger_grids():
    small = ModemConfig.for_resolution("1920x1080", modulation="rgb32", symbol_px=12)
    dense = ModemConfig.for_resolution("1920x1080", modulation="rgb32", cells=84)
    assert small.cell_px >= dense.cell_px
    assert small.describe()["cells"] >= 32
    assert dense.describe()["cells"] == 84
    # a bigger screen holds more
    big = ModemConfig.for_resolution("3840x2160", modulation="rgb32", symbol_px=12)
    assert big.describe()["max_user_payload"] > small.describe()["max_user_payload"]


def test_screen_geometry_places_code_centred():
    cfg = ModemConfig.for_resolution("1920x1080", modulation="rgb32t16")
    geo = screen_geometry(cfg)
    assert geo["code_px"] == (geo["cells"] + 2 * cfg.quiet_zone_cells) * cfg.cell_px
    assert abs((geo["x0"] + geo["x1"]) / 2 - geo["width"] / 2) <= 1
    assert geo["y0"] >= 0 and geo["y1"] <= geo["height"]


# --------------------------------------------------------------------------- #
# rendering + sampling
# --------------------------------------------------------------------------- #
def test_render_geometry():
    cfg = ModemConfig(modulation="rgb8", cell_px=10)
    img, _, _ = _frame(cfg)
    assert img.shape == (cfg.frame_px, cfg.frame_px, 3)
    assert img.dtype == np.uint8
    assert (img[0, 0] > 240).all()          # quiet zone is white
    qz = cfg.quiet_zone_cells
    assert (img[(qz - 1) * 10 + 5, (qz + 2) * 10] < 40).all()


def test_tile_patterns_are_actually_drawn():
    """A patterned mode must put the pattern in the pixels, not just the bookkeeping."""
    cfg = ModemConfig(modulation="rgb8t4", cell_px=16)
    img, hdr, payload = _frame(cfg)
    codec = FrameCodec(cfg)
    tf = codec.encode(hdr, payload)
    cells, patterns = tf.to_cells(codec.layout, cfg.mod())
    P = int(cfg.mod().pattern_mask.shape[-1])
    # find a cell whose pattern is not solid and check for holes
    idx = np.argwhere(patterns.reshape(-1) != 0)
    assert len(idx), "payload should contain non-trivial patterns"
    flat = idx[0][0]
    i, j = divmod(int(flat), patterns.shape[1])
    qz = cfg.quiet_zone_cells
    y0 = (qz + i) * cfg.cell_px
    x0 = (qz + j) * cfg.cell_px
    tile = img[y0:y0 + cfg.cell_px, x0:x0 + cfg.cell_px]
    gray = tile.max(axis=-1)
    mask = np.asarray(cfg.mod().pattern_mask[patterns[i, j]])
    assert mask.min() == 0 and mask.max() == 1
    # holes must be much darker than the lit sub-cells
    assert gray.min() < 0.6 * gray.max()


def test_canon_sampling_recovers_known_tiles():
    """The canonical sampler must return each pattern sub-cell, not a ramp."""
    K, margin, cells, P = 16, 2, 6, 4
    T = (cells + 2 * margin) * K
    canon = np.zeros((T, T, 3), np.uint8)
    truth = {}
    for i in range(cells):
        for j in range(cells):
            pat = (i * cells + j) % 16
            bits = np.array(cfg_bits(pat, P * P)).reshape(P, P)
            truth[(i, j)] = bits
            for si in range(P):
                for sj in range(P):
                    y0 = margin * K + i * K + si * K // P
                    x0 = margin * K + j * K + sj * K // P
                    canon[y0:y0 + K // P, x0:x0 + K // P] = (
                        200 if bits[si, sj] else 16)
    integ = integral_of(canon)
    tiles = canon_tiles(integ, cells, K, margin, P, m=3)
    for (i, j), bits in truth.items():
        got = tiles[i, j].max(axis=-1).reshape(P, 3, P, 3).mean(axis=(1, 3))
        lit = got > 100
        assert (lit == bits.astype(bool)).all(), (i, j, got, bits)


def cfg_bits(value: int, n: int = 4) -> list:
    return [(value >> (n - 1 - b)) & 1 for b in range(n)]


def test_perfect_image_roundtrips_every_modulation():
    """Render -> exact quad -> receiver must return the payload, for all modes."""
    for name in MODULATIONS:
        cfg = ModemConfig(modulation=name, ecc="rs16", cell_px=10)
        codec = FrameCodec(cfg)
        n = max(1, codec.body_capacity - 2)
        payload = (b"round trip through the whole receiver " * 8)[:n]
        hdr = FrameHeader(cfg.mod().mod_id, cfg.ecc_profile().ecc_id, 0, 1,
                          len(payload) + 2)
        img = render_frame(cfg, hdr, payload)
        res = _decode_with_quad(cfg, img, _gt_quad(cfg), payload)
        assert res.ok and res.payload == payload, (name, res.reason)


def test_calibration_ring_decodes_with_hue_gain():
    """A per-channel colour gain must be absorbed by the ring calibration."""
    from optical_modem.phy.demod import PaletteCalibrator

    mod = get_modulation("rgb32")
    rng = np.random.default_rng(2)
    idx = rng.integers(0, mod.n_colors, (16, 16))
    pal = np.array(mod.palette, np.float32)
    samples = pal[idx] * np.array([1.15, 0.85, 1.05], np.float32)
    cal = PaletteCalibrator(mod)
    score = cal.fit(samples.reshape(-1, 3), idx.reshape(-1))
    assert score > 0.95
    fitted = cal.apply(samples)
    d = ((fitted[..., None, :] - pal[None, None]) ** 2).sum(-1)
    pred = d.argmin(axis=-1)
    assert (pred == idx).mean() > 0.95


def test_illum_normalize_removes_shading():
    mod = get_modulation("rgb8")
    rng = np.random.default_rng(2)
    idx = rng.integers(0, 8, (12, 12))
    pal = np.array(mod.palette, np.float32)
    samples = pal[idx].copy()
    shadow = np.linspace(1.0, 0.35, 12, dtype=np.float32)[:, None, None]
    dark = samples * shadow
    norm_bright = illum_normalize(samples, win=3.0)
    norm_dark = illum_normalize(dark, win=3.0)
    a, b = norm_bright[0, 0], norm_dark[-1, -1]
    assert np.linalg.norm(a - b) < 0.25 * np.linalg.norm(samples[0, 0] - dark[-1, -1]) + 0.05


# --------------------------------------------------------------------------- #
# channel + detection
# --------------------------------------------------------------------------- #
def test_channel_returns_requested_size_and_quad():
    cfg = ModemConfig(modulation="rgb8")
    img, _, _ = _frame(cfg)
    ch = ChannelConfig.random(np.random.default_rng(0), 0.4)
    ch.out_size = 640
    cam, quad = simulate(img, ch, np.random.default_rng(0))
    assert cam.shape == (640, 640, 3)
    assert quad.shape == (4, 2)


def test_simulate_screen_frames_the_code():
    cfg = ModemConfig.for_resolution("1920x1080", modulation="rgb32", cells=48)
    hdr = FrameHeader(cfg.mod().mod_id, cfg.ecc_profile().ecc_id, 0, 1, 40)
    screen = render_screen(cfg, hdr, bytes(range(38)))
    assert screen.shape[:2] == (1080, 1920)
    ch = ChannelConfig.random(np.random.default_rng(1), 0.3)
    ch.perspective = 0.0
    ch.rotation = 0.0
    ch.out_size = 1080
    cam, gq = simulate_screen(screen, geometry := screen_geometry(cfg)["grid"], ch,
                              np.random.default_rng(1))
    assert cam.shape[0] == 1080
    side = np.linalg.norm(gq[0] - gq[1])
    assert side > 0.5 * 1080, "the camera must frame the code, not the desktop"


def test_alignment_sharpness_minimum_at_truth():
    cfg = ModemConfig(modulation="rgb8", ecc="rs16")
    img, _, _ = _frame(cfg)
    g = effective_layout(cfg).cells
    quad = _gt_quad(cfg)
    H = cell_homography(quad, g)
    good = sharpness(img, H, g)
    bad = sharpness(img, H, g, 1.0, 0.35, 0.0)
    assert good < bad


def test_quad_from_alignment_identity_and_shift():
    g = 32
    quad = np.array([[0, 0], [g, 0], [g, g], [0, g]], np.float32)
    same = quad_from_alignment(quad, g, 1.0, 0.0, 0.0)
    assert np.allclose(same, quad)
    shifted = quad_from_alignment(quad, g, 1.0, 0.5, 0.0)
    assert np.allclose(shifted - quad, [[0.5, 0]] * 4)


def test_detect_candidates_finds_frame_on_clean_capture():
    cfg = ModemConfig(modulation="rgb8", ecc="rs16")
    img, _, _ = _frame(cfg)
    rng = np.random.default_rng(11)
    ch = ChannelConfig.random(rng, 0.05)
    ch.perspective = 0.01
    ch.rotation = 5
    ch.out_size = 640
    cam, quad = simulate(img, ch, rng)
    g = effective_layout(cfg).cells
    cands = detect_candidates(cam, g, cfg.quiet_zone_cells)
    assert cands, "no contour candidates on a clean capture"


def test_order_quad_is_stable():
    pts = np.array([[10, 10], [90, 12], [88, 92], [8, 90]], np.float32)
    q = order_quad(pts)
    assert np.allclose(q, pts)


def test_ring_alignment_recovers_a_shifted_grid():
    """A quad that is off by a sixth of a cell must be re-aligned by ring scoring.

    (A third of a cell is beyond what the ring can resolve: a 30%-mixed colour
    still rounds to the right palette entry, so the pose search has to fall back
    on the CRC.  Six tenths of a cell is handled by the pose search; beyond that
    the *quad* is wrong and has to be re-fitted from the border edges.)
    """
    cfg = ModemConfig.for_resolution("1920x1080", modulation="rgb32", cells=40)
    codec = FrameCodec(cfg)
    payload = bytes(range(120))
    hdr = FrameHeader(cfg.mod().mod_id, cfg.ecc_profile().ecc_id, 0, 1,
                      len(payload) + 2)
    screen = prepare_frame(cfg, hdr, payload)
    ch = ChannelConfig(background=40, out_size=1080, scale_min=0.9, scale_max=0.9)
    cam, gq = capture(cfg, screen, ch, np.random.default_rng(0))
    rx = ClassicalReceiver(cfg)
    rx.decoder._img = cam
    cells = effective_layout(cfg).cells
    off = (gq[1] - gq[0]) / cells * 0.15
    bad = np.array([gq[0] + off, gq[1] + off, gq[2] + off, gq[3] + off], np.float32)
    att = rx.decoder.score_hypotheses(cam, [Quad(bad, 1.0, "gt")], cfg.mod())[0]
    res = rx.decoder.decode_attempt(att, cfg.mod(), deep=True)
    assert res.ok and res.payload == payload


# --------------------------------------------------------------------------- #
# quad refinement on cell-lattice gradient energy
# --------------------------------------------------------------------------- #
def _energy_trial(cells_side=32, cell_px=14, seed=3):
    """Render a frame and return ``(image, true_grid_quad, cells)``."""
    from optical_modem.phy.render import render_frame
    from optical_modem.protocol.packet import FrameHeader

    cfg = ModemConfig(modulation="rgb32", ecc="rs16")
    cfg = dataclasses.replace(cfg, layout=dataclasses.replace(
        cfg.layout, cells=cells_side), cell_px=cell_px)
    hdr = FrameHeader(cfg.mod().mod_id, cfg.ecc_profile().ecc_id, 0, 1, 8)
    img = render_frame(cfg, hdr, b"energy!!")
    from optical_modem.protocol.frames import effective_layout

    cells = effective_layout(cfg).cells
    qz = cfg.quiet_zone_cells
    # the coded grid sits ``qz`` cells inside the rendered image
    a = float(qz * cell_px)
    b = float((qz + cells) * cell_px)
    quad = np.float32([[a, a], [b, a], [b, b], [a, b]])
    return img, quad, cells


def test_align_energy_prefers_the_true_lattice():
    """A half-cell offset must score *worse* than the true grid."""
    from optical_modem.phy.locate import align_energy

    img, quad, cells = _energy_trial()
    cell = float(np.linalg.norm(quad[1] - quad[0])) / cells
    good = align_energy(img, quad, cells)
    for dx, dy in ((0.5, 0.0), (0.0, 0.5), (0.35, 0.35)):
        bad = quad + np.float32([dx * cell, dy * cell])
        assert align_energy(img, bad, cells) < good


def test_refine_quad_energy_recovers_a_translated_quad():
    from optical_modem.phy.locate import refine_quad_energy

    img, quad, cells = _energy_trial()
    cell = float(np.linalg.norm(quad[1] - quad[0])) / cells
    # shrink by 3% and shift by a third of a cell
    centre = quad.mean(axis=0)
    start = (quad - centre) * 0.97 + centre + np.float32([0.33 * cell, -0.3 * cell])
    err0 = np.linalg.norm(start - quad, axis=1).mean() / cell
    fit = refine_quad_energy(img, start, cells)
    err1 = np.linalg.norm(fit - quad, axis=1).mean() / cell
    assert fit.shape == (4, 2)
    assert err1 < err0
    assert err1 < 0.25


def test_refine_quad_energy_is_finite_on_a_bad_quad():
    from optical_modem.phy.locate import refine_quad_energy

    img, quad, cells = _energy_trial()
    junk = quad + np.float32([[5, -3], [-4, 6], [7, 2], [-2, -7]])
    out = refine_quad_energy(img, junk, cells, steps=(0.25,), passes=1)
    assert out.shape == (4, 2) and np.all(np.isfinite(out))
