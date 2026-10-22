"""End-to-end receiver tests: decode real captures, adaptive ECC, resume."""
import numpy as np
import pytest

from optical_modem.config import ModemConfig, choose_ecc
from optical_modem.phy.channel import ChannelConfig
from optical_modem.phy.classical import ClassicalReceiver
from optical_modem.protocol.packet import FrameHeader, Packetizer
from optical_modem.receiver import HybridReceiver
from optical_modem.sim import capture, prepare_frame


def _tx_cfg(mod="rgb8", ecc="rs16"):
    return ModemConfig.for_resolution("1280x720", modulation=mod, ecc=ecc)


def _transmit(cfg, payload):
    hdr = FrameHeader(cfg.mod().mod_id, cfg.ecc_profile().ecc_id, 0, 1,
                      len(payload) + 2)
    return prepare_frame(cfg, hdr, payload)


def _capture(cfg, screen, level, seed):
    rng = np.random.default_rng(seed)
    return capture(cfg, screen, ChannelConfig.handheld(rng, level), rng)[0]


@pytest.mark.parametrize("level", [0.0, 0.2])
def test_classical_receiver_decodes(level):
    cfg = _tx_cfg()
    payload = b"optical modem payload"
    frame = _transmit(cfg, payload)
    rx = ClassicalReceiver(cfg)
    hits = 0
    for i in range(4):
        img = _capture(cfg, frame, level, 10 + i)
        rep = rx.decode(img)
        if rep and rep[0].ok and rep[0].payload == payload:
            hits += 1
    assert hits >= 3, f"only {hits}/4 decodes at level {level}"


def test_classical_receiver_survives_heavy_noise():
    """At level 0.5 decode is unreliable, but it must degrade, not blow up."""
    cfg = _tx_cfg()
    payload = b"optical modem payload"
    frame = _transmit(cfg, payload)
    rx = ClassicalReceiver(cfg)
    reasons = set()
    for i in range(3):
        img = _capture(cfg, frame, 0.6, 40 + i)
        rep = rx.decode(img)
        assert isinstance(rep, list)
        assert rep and rep[0].payload in (b"", payload)
        reasons.add(rep[0].reason)
    assert reasons <= {"", "crc/fec", "header:ValueError", "no-attempt"}


def test_receiver_reports_ring_score_and_timing():
    cfg = _tx_cfg()
    frame = _transmit(cfg, b"timing")
    img = _capture(cfg, frame, 0.1, 4)
    rep = ClassicalReceiver(cfg).decode(img)
    assert rep and rep[0].ok
    res = rep[0]
    assert 0.5 <= res.ring_score <= 1.0
    assert res.modulation == "rgb8"
    assert res.quality > 0
    assert res.as_dict()["ok"] is True


def test_auto_modulation_detection():
    """A gray4 frame must decode even when the receiver guesses rgb8 first."""
    cfg_tx = _tx_cfg("gray4")
    payload = b"modulation detection"
    frame = _transmit(cfg_tx, payload)
    cfg_rx = _tx_cfg("rgb8")
    img = _capture(cfg_tx, frame, 0.2, 8)
    rep = ClassicalReceiver(cfg_rx).decode(img)
    assert rep and rep[0].ok
    assert rep[0].modulation == "gray4"


def test_hybrid_receiver_classical_mode():
    cfg = _tx_cfg()
    payload = b"hybrid receiver"
    frame = _transmit(cfg, payload)
    rx = HybridReceiver(cfg, mode="classical")
    # single-shot decode is ~80% reliable at this channel level, so the test
    # asks for a majority over three independent captures rather than a
    # specific lucky seed
    hits, last = 0, None
    for i in range(3):
        rep = rx.decode(_capture(cfg, frame, 0.2, 6 + i))
        last = rep
        hits += bool(rep.ok and rep.payload == payload)
    assert hits >= 2, f"{hits}/3 hybrid decodes"
    assert last.timings["total"] > 0


def test_adaptive_ecc_choice_reacts_to_quality():
    assert choose_ecc(0.95) != choose_ecc(0.2)


def test_file_transfer_resume_over_simulated_link():
    """Send a multi-frame file, drop frames, retransmit, verify bytes."""
    cfg = _tx_cfg()
    data = bytes(np.random.default_rng(9).integers(0, 256, 4200, dtype=np.uint8))
    pkt = Packetizer(cfg)
    frames = list(pkt.frames_for(data))
    assert len(frames) >= 3
    rx = Packetizer(cfg)
    done = None
    pending = list(frames)
    # first pass: every second frame is "lost" (decode failure)
    for i, (hdr, body) in enumerate(pending):
        if i % 2:
            continue
        img = prepare_frame(cfg, hdr, body)
        cap = _capture(cfg, img, 0.15, i)
        res = ClassicalReceiver(cfg).decode(cap)
        if res and res[0].ok:
            from optical_modem.protocol.packet import DecodedFrame

            done = rx.push(DecodedFrame(res[0].header, res[0].payload, True, 0,
                                        True)) or done
    assert done is None  # incomplete
    # second pass: retransmit everything
    for hdr, body in frames:
        from optical_modem.protocol.packet import DecodedFrame

        done = rx.push(DecodedFrame(hdr, body, True, 0, True)) or done
    assert done == data


# --------------------------------------------------------------------------- #
# pose / orientation robustness of QuadDecoder
# --------------------------------------------------------------------------- #
def _clean_quad_decoder():
    """A decoder image + honest quad for a clean render (no camera channel)."""
    cfg = _tx_cfg()
    payload = b"quad oracle"
    screen = _transmit(cfg, payload)
    # crop the code out of the display canvas so the grid fills the image
    from optical_modem.protocol.frames import effective_layout
    from optical_modem.phy.render import screen_geometry

    geo = screen_geometry(cfg)
    x0, y0, x1, y1 = [float(v) for v in geo["grid"]]     # coded cells only
    cells = effective_layout(cfg).cells
    quad = np.float32([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
    return cfg, payload, screen, quad, cells


def test_align_by_ring_bound_keeps_a_refined_quad_on_its_cell(monkeypatch):
    """``wide`` must bound the recentre so a good quad cannot slip a cell."""
    from optical_modem.phy.classical import QuadDecoder
    from optical_modem.phy.locate import Quad

    cfg, payload, screen, quad, cells = _clean_quad_decoder()
    dec = QuadDecoder(cfg)
    dec._img = screen

    from optical_modem.phy.classical import rectify, integral_of
    from optical_modem.protocol.frames import FrameCodec

    codec = FrameCodec(cfg)
    layout = codec.layout
    mod = cfg.mod()
    ring_cells, ring_colors = dec._ring_expected_cells(mod, layout)
    rr = np.array([r for r, _ in ring_cells])
    cc = np.array([c for _, c in ring_cells])
    canon, K, margin = rectify(screen, quad, cells, K=16, margin=2)
    integ = integral_of(canon)
    _, _, dx, dy, _ = dec.align_by_ring(integ, cells, K, margin, mod, rr, cc,
                                        ring_colors, wide=0.6)
    assert abs(dx) <= 0.6 + 1e-6 and abs(dy) <= 0.6 + 1e-6, (dx, dy)


def test_decode_tries_both_illumination_variants(monkeypatch):
    """If the variant the ring chose is the wrong one, the other must be tried.

    A shadow over the *middle* of the frame is invisible to the calibration
    ring on the border, so the ring votes for the wrong normalisation variant;
    the header has to be able to overrule it.
    """
    from optical_modem.phy.classical import QuadDecoder, Attempt
    from optical_modem.phy.locate import Quad

    cfg, payload, screen, quad, cells = _clean_quad_decoder()
    dec = QuadDecoder(cfg)
    dec._img = screen
    seen = []
    real = QuadDecoder._demod

    def spy(self, integ, cells_, K, margin, mod, dx, dy, scale, k, cal,
            use_norm, pair_map, rows=None):
        seen.append(bool(use_norm))
        values, llr = real(self, integ, cells_, K, margin, mod, dx, dy, scale,
                           k, cal, use_norm, pair_map, rows)
        if not use_norm:
            # the "un-normalised" variant is corrupted, as if shaded
            values = ((values.astype(np.int32) + 1) % mod.n_values).astype(np.uint16)
        return values, llr

    monkeypatch.setattr(QuadDecoder, "_demod", spy)
    att = Attempt(Quad(quad, 1.0, "refined"), 1.0, 0, np.zeros((3, 3), np.float32))
    res = dec.decode_attempt(att, cfg.mod())
    assert res.ok, res.reason
    assert res.payload == payload
    assert True in seen and False in seen, "both illumination variants must be tried"
