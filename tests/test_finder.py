"""Finder patterns: reserved (never overwrite data), rendered, decodable, scored.

The contract that matters is *no data loss*: a finder must be carved out of the
header/payload symbol lists, not painted on top of them.  These tests pin that
down structurally (disjoint regions, exact capacity accounting) and functionally
(perfect renders still round-trip, and the receiver's finder score separates a
real frame from noise).
"""
from __future__ import annotations

import numpy as np
import pytest

from optical_modem.config import HEADER_DATA_LEN, FrameLayout, ModemConfig
from optical_modem.phy.channel import ChannelConfig
from optical_modem.sim import capture
from optical_modem.phy.render import render_frame
from optical_modem.protocol import finder as fnd
from optical_modem.protocol.frames import FrameCodec, effective_layout
from optical_modem.protocol.packet import HEADER_RS_PARITY, Packetizer
from optical_modem.receiver import HybridReceiver

SHAPES = ["ring", "dice", "bullseye", "checker", "diamond", "cross", "x",
          "bracket", "triangle", "solid"]
PAYLOAD = b"finder round trip payload"


def _cfg(shape: str, cells: int = 32, modulation: str = "rgb8",
         ecc: str = "rs16", cell_px: int = 8) -> ModemConfig:
    return ModemConfig(modulation=modulation, ecc=ecc, cell_px=cell_px,
                       layout=FrameLayout(cells=cells, finder=shape))


def _first_frame(cfg: ModemConfig):
    return next(Packetizer(cfg).frames_for(PAYLOAD))


def _decode(cfg: ModemConfig, image: np.ndarray):
    """Best attempt of a classical decode (``ReceiveReport`` or a plain list)."""
    rep = HybridReceiver(cfg, mode="classical").decode(image)
    results = getattr(rep, "results", rep)
    return results[0] if results else None


# --------------------------------------------------------------------------- #
# catalogue
# --------------------------------------------------------------------------- #
def test_catalogue_is_well_formed() -> None:
    assert len(fnd.SHAPES) == 10
    for name, shape in fnd.SHAPES.items():
        m = shape.array
        assert m.ndim == 2 and m.shape[0] == m.shape[1] >= 3, name
        assert set(np.unique(m)) <= {0, 1}, name
        # "solid" is deliberately one tone (a pure bright block); everything else
        # must carry contrast or it is just a bright square with extra cost.
        if name == "solid":
            assert int(m.sum()) == m.size
        else:
            assert 0 < int(m.sum()) < m.size, f"{name}: degenerate (all one tone)"
        assert shape.as_dict()["size"] == shape.size


def test_spans_are_documented() -> None:
    assert set(fnd.SPANS) == {"corners", "three", "center", "edges"}
    assert fnd.get_shape(None) is None
    assert fnd.get_shape("none") is None
    assert fnd.get_shape("RING").name == "ring"
    with pytest.raises(KeyError):
        fnd.get_shape("hexagram")


# --------------------------------------------------------------------------- #
# the "does not overwrite data" guarantee
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("shape", SHAPES)
def test_regions_are_disjoint_and_cover_the_grid(shape: str) -> None:
    codec = FrameCodec(_cfg(shape))          # __init__ raises on any overlap
    regions = {"ring": set(codec.ring_xy), "finder": set(codec.finder),
               "header": set(codec.header_xy), "payload": set(codec.payload_xy)}
    names = list(regions)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            shared = regions[names[i]] & regions[names[j]]
            assert not shared, f"{shape}: {names[i]}/{names[j]} share {len(shared)}"
    assert sum(len(r) for r in regions.values()) == codec.layout.n_symbols
    assert len(codec.finder) > 0


@pytest.mark.parametrize("shape", SHAPES)
def test_encoded_grid_still_carries_the_finder(shape: str) -> None:
    """After a real encode, every finder tile holds its tone — data never wins."""
    cfg = _cfg(shape)
    codec = FrameCodec(cfg)
    hdr, body = _first_frame(cfg)
    tf = codec.encode(hdr, body)
    bright, dark = codec.finder_colors()
    for (i, j), level in codec.finder.items():
        assert tf.colors[i, j] == (bright if level else dark), f"{shape}@{(i, j)}"
        assert tf.patterns[i, j] == 0, "finder tiles must be solid (blur-safe)"


def test_finder_costs_capacity_and_reports_it() -> None:
    plain, ring = _cfg("none").describe(), _cfg("ring").describe()
    assert plain["finder"] is None
    assert ring["finder"]["shape"] == "ring"
    assert ring["max_user_payload"] < plain["max_user_payload"]
    assert ring["finder"]["symbols"] == 4 * 25
    assert ring["finder"]["blocks"] == 4
    share = ring["finder"]["share"]
    assert 0.0 < share < 0.2, share
    # capacity lost, but the frame is still worth sending: >200 bytes at 32 cells
    assert ring["max_user_payload"] >= 200


def test_header_band_grows_instead_of_truncating() -> None:
    """A finder in the top corners must not starve the fixed-rate header."""
    cfg = _cfg("ring", modulation="gray2")      # 1 bit/symbol: tightest case
    eff = effective_layout(cfg)
    need_bits = (HEADER_DATA_LEN + HEADER_RS_PARITY) * 8
    codec = FrameCodec(cfg)
    assert codec.layout == eff
    assert len(codec.header_xy) * cfg.mod().bits >= need_bits
    assert eff.header_rows >= cfg.layout.header_rows


def test_small_grids_shrink_the_finder() -> None:
    big = fnd.auto_size(fnd.SHAPES["ring"], 32, 32)
    small = fnd.auto_size(fnd.SHAPES["ring"], 24, 24)
    assert small < big
    codec = FrameCodec(_cfg("ring", cells=24))  # must fit without raising
    assert len(codec.finder) == 4 * small ** 2


@pytest.mark.parametrize("span", list(fnd.SPANS))
def test_spans_place_blocks_inside_the_ring(span: str) -> None:
    cfg = ModemConfig(layout=FrameLayout(cells=32, finder="ring", finder_span=span))
    layout = cfg.layout
    size = layout.finder_size or fnd.auto_size(fnd.SHAPES["ring"],
                                               layout.rows, layout.cols)
    blocks = fnd.block_origins(span, size, layout.rows, layout.cols, layout.ring)
    assert blocks, span
    for (r, c) in blocks:
        assert layout.ring <= r <= layout.rows - layout.ring - size
        assert layout.ring <= c <= layout.cols - layout.ring - size
    if span == "corners":
        assert len(blocks) == 4
    if span == "center":
        assert len(blocks) == 1


# --------------------------------------------------------------------------- #
# end to end
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("shape", ["ring", "checker", "bracket", "bullseye"])
def test_finder_frames_round_trip(shape: str) -> None:
    cfg = _cfg(shape)
    hdr, body = _first_frame(cfg)
    res = _decode(cfg, render_frame(cfg, hdr, body))
    assert res is not None and res.ok, (res.reason if res else "no attempt")
    assert res.payload == body
    assert res.finder_score is not None and res.finder_score > 0.85


def test_no_finder_means_no_score() -> None:
    cfg = _cfg("none")
    hdr, body = _first_frame(cfg)
    res = _decode(cfg, render_frame(cfg, hdr, body))
    assert res.ok and res.payload == body
    assert res.finder_score is None


def test_finder_score_falls_on_a_bad_capture() -> None:
    cfg = _cfg("ring")
    hdr, body = _first_frame(cfg)
    clean = _decode(cfg, render_frame(cfg, hdr, body))
    assert clean.ok and clean.finder_score > 0.85
    rng = np.random.default_rng(3)
    ch = ChannelConfig.handheld(rng, 0.9)
    cam = capture(cfg, render_frame(cfg, hdr, body), ch, rng)[0]
    bad = _decode(cfg, cam)
    assert bad is None or not bad.ok or bad.finder_score is None \
        or bad.finder_score < clean.finder_score


def test_pair_mode_keeps_the_finder_aligned() -> None:
    """A hue-differential symbol spans two cells; the finder must still be square."""
    cfg = _cfg("ring", modulation="rgb16t4dp")
    codec = FrameCodec(cfg)
    g = codec.layout.cells
    hdr, body = _first_frame(cfg)
    tf = codec.encode(hdr, body)
    colors, patterns = tf.to_cells(codec.layout, codec.mod)
    assert colors.shape == (g, g) and patterns.shape == (g, g)
    res = _decode(cfg, render_frame(cfg, hdr, body))
    assert res is not None and res.ok, (res.reason if res else "no attempt")
    assert res.payload == body


def test_unknown_shape_never_reaches_encoding() -> None:
    with pytest.raises(KeyError):
        fnd.get_shape("hexagram")
    with pytest.raises(KeyError):
        fnd.block_origins("diagonals", 5, 32, 32, 1)
