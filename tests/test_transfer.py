"""End-to-end transfer regressions: one-frame completion and capture aspect.

Both were found by pointing the suite at itself:

* a message short enough to fit in a single frame **never completed** — the only
  frame of the transfer was typed ``FRAME_DATA``, so the receiver never learned
  ``total`` and the reassembler waited forever (the phone showed "receiving"
  for a 24-byte text);
* ``simulate()`` mapped every frame onto a *square* quad, so a 1280x720 display
  canvas was squeezed 2.4:1 by the channel — impossible to decode, and invisible
  in the benchmarks because those used square renders.
"""
from __future__ import annotations

import numpy as np
import pytest

from optical_modem.config import ModemConfig
from optical_modem.phy.channel import ChannelConfig, simulate
from optical_modem.phy.render import render_frame
from optical_modem.protocol.packet import FRAME_START, DecodedFrame, Packetizer
from optical_modem.receiver import HybridReceiver


def _roundtrip(cfg: ModemConfig, data: bytes, mode: str = "classical"):
    """Pack, render, decode with no channel, and reassemble."""
    pkt_tx = Packetizer(cfg)
    frames = list(pkt_tx.frames_for(data))
    pkt_rx = Packetizer(cfg)
    rx = HybridReceiver(cfg, mode=mode)
    message = None
    for hdr, body in frames:
        frame = render_frame(cfg, hdr, body)
        report = rx.decode(frame)
        assert report.ok, f"perfect render failed to decode: {report.best().reason}"
        best = report.best()
        message = pkt_rx.push(DecodedFrame(header=best.header, payload=best.payload,
                                           crc_ok=True, fec_ok=True,
                                           n_corrected=best.n_corrected)) or message
    return frames, message


@pytest.mark.parametrize("size", [1, 12, 200])
def test_single_frame_transfer_completes(size: int) -> None:
    cfg = ModemConfig(modulation="rgb8", ecc="rs32")
    data = bytes((i * 37 + 11) % 256 for i in range(size))
    frames, message = _roundtrip(cfg, data)
    assert len(frames) == 1
    assert frames[0][0].frame_type == FRAME_START   # its own start *and* end
    assert message == data


def test_multi_frame_transfer_reassembles_out_of_order() -> None:
    cfg = ModemConfig(modulation="rgb8", ecc="rs32")
    data = bytes((i * 53 + 7) % 256 for i in range(1200))
    frames, message = _roundtrip(cfg, data)
    assert len(frames) > 1
    assert message == data
    # drop the tail, replay the head: the reassembler stays incomplete but the
    # duplicate accounting (what the UI calls "duplicates ignored") still works
    pkt = Packetizer(cfg)
    rx = HybridReceiver(cfg, mode="classical")
    hdr, body = frames[0]
    report = rx.decode(render_frame(cfg, hdr, body))
    best = report.best()
    first = DecodedFrame(header=best.header, payload=best.payload, crc_ok=True,
                         fec_ok=True, n_corrected=best.n_corrected)
    assert pkt.push(first) is None and pkt.buffered == 1
    assert pkt.push(first) is None and pkt.buffered == 1     # duplicate, no growth


def test_simulated_capture_keeps_the_display_aspect_ratio() -> None:
    from optical_modem.suite.s2c import make_config
    from optical_modem.sim import prepare_frame

    cfg = make_config("rgb8", "rs32", "1280x720")
    pkt = Packetizer(cfg)
    hdr, body = next(pkt.frames_for(b"aspect check"))
    screen = prepare_frame(cfg, hdr, body)
    assert screen.shape[:2] == (720, 1280)
    rng = np.random.default_rng(0)
    ch = ChannelConfig.handheld(rng, 0.1)
    ch.out_size = 1080
    _, quad = simulate(screen, ch, rng)
    w = float(np.linalg.norm(quad[0] - quad[1]))
    h = float(np.linalg.norm(quad[0] - quad[3]))
    assert w / h == pytest.approx(1280 / 720, rel=0.08), (w, h)


def test_simulated_display_frame_decodes_at_moderate_noise() -> None:
    """The suite's own loopback path: 720p canvas -> phone photo -> payload."""
    from optical_modem.suite.s2c import make_config
    from optical_modem.sim import prepare_frame

    cfg = make_config("rgb8", "rs32", "1280x720", symbol_px=12)
    pkt = Packetizer(cfg)
    hdr, body = next(pkt.frames_for(b"loopback! " * 8))
    screen = prepare_frame(cfg, hdr, body)
    rng = np.random.default_rng(2)
    ch = ChannelConfig.handheld(rng, 0.3)
    ch.out_size = 1080
    cam, _ = simulate(screen, ch, rng)
    report = HybridReceiver(cfg, mode="classical").decode(cam)
    assert report.ok, report.best().reason if report.best() else "no detection"
    assert report.best().payload == body
