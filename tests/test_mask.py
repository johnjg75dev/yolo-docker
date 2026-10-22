"""Whitening mask tests: the zero-padded tail must not become a dark slab."""
import numpy as np
import pytest

from optical_modem.config import ModemConfig
from optical_modem.protocol.frames import FrameCodec
from optical_modem.protocol.mask import (
    HEADER_MASK_BYTE,
    MASK_ALT,
    MASK_NONE,
    MASK_PRNG,
    MASK_RAMP,
    mask_info,
    mask_values,
    splitmix64,
)
from optical_modem.protocol.packet import FrameHeader, Packetizer, check_crc

ALL_FAMILIES = (MASK_NONE, MASK_PRNG, MASK_ALT, MASK_RAMP)


def _cfg(mod="rgb32", ecc="rs16"):
    return ModemConfig.for_resolution("1920x1080", modulation=mod, ecc=ecc)


def _hdr(cfg, payload, mask_byte):
    return FrameHeader(cfg.mod().mod_id, cfg.ecc_profile().ecc_id, 0, 1,
                       len(payload) + 2, mask_byte)


def _stats(values):
    """Modal share of the grid and the longest run of identical symbols."""
    flat = values.reshape(-1)
    share = float(np.bincount(flat).max()) / flat.size
    best = 1
    for row in values:
        run = 1
        for a, b in zip(row, row[1:]):
            run = run + 1 if a == b else 1
            best = max(best, run)
    return share, best


def test_splitmix64_is_deterministic_and_well_spread():
    a = splitmix64(0x1234, 4096)
    b = splitmix64(0x1234, 4096)
    assert np.array_equal(a, b)
    assert not np.array_equal(a, splitmix64(0x1235, 4096))
    # the low bits must not be degenerate (they feed the modulo)
    assert 900 < len(set((a & 0x3FF).tolist())) <= 1024


@pytest.mark.parametrize("family", ALL_FAMILIES)
def test_mask_values_are_in_range_and_self_inverse(family):
    pos = [[i // 40, i % 40] for i in range(1600)]
    for n_values in (2, 8, 32, 128, 512):
        m = mask_values((family << 6) | 7, pos, n_values)
        assert m.shape == (1600,)
        assert m.dtype == np.uint16
        assert m.max() < n_values
        vals = np.arange(1600, dtype=np.uint16) % n_values
        assert np.array_equal((vals ^ m) ^ m, vals)


def test_header_block_is_masked_with_the_public_mask():
    """The header must be readable before its own mask byte is known."""
    cfg = _cfg()
    fc = FrameCodec(cfg)
    hdr = _hdr(cfg, b"", MASK_NONE << 6)          # payload unmasked
    tf = fc.encode(hdr, b"")
    hdr_cells = np.array([tf.values[i, j] for i, j in fc.header_xy])
    # the header block still has a zero tail, but it is scrambled
    raw = np.array([0] * len(hdr_cells), np.uint16)
    m = mask_values(HEADER_MASK_BYTE, fc.header_xy, fc.mod.n_values)
    assert int((m != 0).sum()) > len(m) // 2
    assert not np.array_equal(hdr_cells[-(len(m) // 4):], raw[-(len(m) // 4):])


@pytest.mark.parametrize("family", ALL_FAMILIES)
def test_sparse_payload_roundtrips_and_is_whitened(family):
    cfg = _cfg()
    fc = FrameCodec(cfg)
    payload = b"hi"
    hdr = _hdr(cfg, payload, (family << 6) | 5)
    tf = fc.encode(hdr, payload)
    got_hdr, body, _, fec_ok = fc.decode_grid(tf.values)
    assert fec_ok and check_crc(body) == (payload, True)
    assert got_hdr.mask_byte == hdr.mask_byte
    assert got_hdr.seq == hdr.seq


def test_mask_removes_the_flat_slab():
    """The whole point: a 2-byte payload must not paint a giant flat block."""
    cfg = _cfg()
    fc = FrameCodec(cfg)
    payload = b"hi"
    plain = fc.encode(_hdr(cfg, payload, MASK_NONE << 6), payload).values
    masked = fc.encode(_hdr(cfg, payload, (MASK_PRNG << 6) | 5), payload).values
    share_plain, run_plain = _stats(plain)
    share_masked, run_masked = _stats(masked)
    assert share_plain > 0.5          # almost the whole frame is one symbol
    assert share_masked < 0.10        # ... and now it looks like data
    assert run_masked * 5 < run_plain


@pytest.mark.parametrize("mod", ["rgb8", "rgb32", "rgb32t16", "rgb16t4dp"])
def test_mask_roundtrips_on_pattern_and_pair_modes(mod):
    cfg = _cfg(mod)
    fc = FrameCodec(cfg)
    payload = bytes(range(40))
    hdr = _hdr(cfg, payload, (MASK_PRNG << 6) | 11)
    tf = fc.encode(hdr, payload)
    _, body, _, fec_ok = fc.decode_grid(tf.values)
    assert fec_ok and check_crc(body) == (payload, True)


def test_wrong_mask_is_detected_by_the_crc():
    """A receiver that ignores the header's mask must fail, not silently pass."""
    cfg = _cfg()
    fc = FrameCodec(cfg)
    payload = b"masked payload"
    tf = fc.encode(_hdr(cfg, payload, (MASK_PRNG << 6) | 3), payload)
    hdr, body, _, fec_ok = fc.decode_grid(tf.values)
    # re-decode forcing the no-mask family
    hdr2 = FrameHeader(hdr.mod_id, hdr.ecc_id, hdr.frame_type, hdr.seq,
                       hdr.length, MASK_NONE << 6)
    _, body2, _, fec2 = fc.decode_grid(tf.values, header=hdr2)
    assert not (fec2 and check_crc(body2) == (payload, True))


def test_packetizer_varies_the_seed_per_frame():
    cfg = _cfg()
    pkt = Packetizer(cfg)
    frames = list(pkt.frames_for(bytes(range(256)) * 80))
    assert len(frames) >= 3
    seeds = [h.mask_seed for h, _ in frames]
    assert len(set(seeds)) == len(seeds)        # no two frames share a mask
    assert all(h.mask_family == cfg.mask_family for h, _ in frames)


def test_config_can_disable_whitening():
    cfg = ModemConfig(mask_family=MASK_NONE)
    assert cfg.mask_byte(7) == 0x07            # family 0, seed 7
    assert "prng" in mask_info((MASK_PRNG << 6))
    assert "alternating" in mask_info((MASK_ALT << 6))
    assert "ramp" in mask_info((MASK_RAMP << 6))
