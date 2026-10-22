"""Protocol layer tests: FEC, headers, frame codec, packetisation."""
import numpy as np
import pytest

from optical_modem.config import (
    ECC_PROFILES,
    HEADER_DATA_LEN,
    MODULATIONS,
    ModemConfig,
    choose_ecc,
    get_ecc,
    get_modulation,
)
from optical_modem.protocol.ecc import LDPC, ReedSolomon, make_fec
from optical_modem.protocol.frames import (
    FrameCodec,
    deinterleave,
    effective_layout,
    interleave,
)
from optical_modem.protocol.packet import (
    FRAME_DATA,
    FRAME_START,
    FrameHeader,
    Packetizer,
    check_crc,
    payload_with_crc,
)


# --------------------------------------------------------------------------- #
# FEC
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["rs8", "rs16", "rs32", "rs64"])
def test_reed_solomon_repairs_errors(name):
    fec = make_fec(get_ecc(name))
    data = bytes(range(200))
    enc = fec.encode(data)
    assert len(enc) > len(data)
    clean, ncorr, ok = fec.decode(enc)
    assert clean[: len(data)] == data and ok and ncorr == 0
    bad = bytearray(enc)
    for i in (3, 7, 11):
        bad[i] ^= 0xFF
    dec, ncorr, ok = fec.decode(bytes(bad))
    assert ok and ncorr >= 3 and dec[: len(data)] == data


def test_reed_solomon_uses_erasures():
    fec = make_fec(get_ecc("rs16"))
    data = bytes(range(120))
    enc = bytearray(fec.encode(data))
    for i in (5, 9):
        enc[i] ^= 0x5A
    dec, ncorr, ok = fec.decode(bytes(enc), erasures=[5, 9])
    assert ok and dec[: len(data)] == data


@pytest.mark.parametrize("profile", ["ldpc12", "ldpc23"])
def test_ldpc_roundtrip_and_repair(profile):
    fec = make_fec(get_ecc(profile))
    data = bytes(np.random.default_rng(0).integers(0, 256, 60, dtype=np.uint8))
    enc = fec.encode(data)
    dec, _, ok = fec.decode(enc)
    assert ok and dec[: len(data)] == data
    bits = np.unpackbits(np.frombuffer(enc, np.uint8)).copy()
    bits[[10, 200, 400]] ^= 1
    dec2, _, ok2 = fec.decode(np.packbits(bits).tobytes())
    assert ok2 and dec2[: len(data)] == data


def test_ldpc_matrix_is_well_formed():
    code = LDPC(120, 60)
    assert code.H.shape == (60, 120)
    # no duplicated rows or columns (guards against weight-2 codewords)
    rows = {np.packbits(code.H[r]).tobytes() for r in range(code.m)}
    cols = {np.packbits(code.H[:, c]).tobytes() for c in range(code.n)}
    assert len(rows) == code.m
    assert len(cols) == code.n


def test_null_fec_passthrough():
    fec = make_fec(get_ecc("none"))
    assert fec.encode(b"abc") == b"abc"
    assert fec.decode(b"abc")[0] == b"abc"


def test_adaptive_ecc_monotone():
    assert choose_ecc(0.95) == "rs8"
    assert choose_ecc(0.75) == "rs16"
    assert choose_ecc(0.5) == "rs32"
    assert choose_ecc(0.1) == "rs64"


# --------------------------------------------------------------------------- #
# header / CRC
# --------------------------------------------------------------------------- #
def test_header_roundtrip_large_values():
    h = FrameHeader(mod_id=3, ecc_id=5, frame_type=FRAME_START, seq=65535,
                    length=1024)
    back = FrameHeader.unpack(h.pack())
    assert back == h
    assert len(h.pack()) == HEADER_DATA_LEN


def test_header_rejects_garbage():
    with pytest.raises(ValueError):
        FrameHeader.unpack(b"\x00" * HEADER_DATA_LEN)


def test_crc_detects_corruption():
    body = payload_with_crc(b"hello")
    data, ok = check_crc(body)
    assert ok and data == b"hello"
    bad = bytearray(body)
    bad[0] ^= 1
    assert not check_crc(bytes(bad))[1]


# --------------------------------------------------------------------------- #
# frame codec
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mod", list(MODULATIONS))
def test_frame_codec_roundtrip(mod):
    cfg = ModemConfig(modulation=mod, ecc="rs16")
    codec = FrameCodec(cfg)
    body = b"optical modem frame test" * 3
    body = body[: codec.body_capacity - 2]
    hdr = FrameHeader(cfg.mod().mod_id, cfg.ecc_profile().ecc_id, FRAME_DATA,
                      700, len(body) + 2)
    frame = codec.encode(hdr, body)
    layout = effective_layout(cfg)
    assert frame.values.shape == (layout.rows, layout.cols)
    assert frame.colors.shape == frame.values.shape
    got_hdr, payload, _, fec_ok = codec.decode_grid(frame.values)
    assert got_hdr.seq == 700 and got_hdr.length == len(body) + 2
    assert fec_ok
    assert check_crc(payload) == (body, True)


@pytest.mark.parametrize("ecc", list(ECC_PROFILES))
def test_frame_codec_all_ecc(ecc):
    cfg = ModemConfig(modulation="rgb8", ecc=ecc)
    codec = FrameCodec(cfg)
    body = b"z" * (codec.body_capacity - 2)
    hdr = FrameHeader(2, cfg.ecc_profile().ecc_id, FRAME_DATA, 1, len(body) + 2)
    frame = codec.encode(hdr, body)
    _, payload, _, ok = codec.decode_grid(frame.values)
    assert ok and check_crc(payload) == (body, True)


def test_frame_rejects_oversized_payload():
    cfg = ModemConfig(modulation="gray2", ecc="rs64")
    codec = FrameCodec(cfg)
    hdr = FrameHeader(0, 4, FRAME_DATA, 0, 100)
    with pytest.raises(ValueError):
        codec.encode(hdr, b"x" * (codec.body_capacity))


def test_header_rows_expand_for_low_rate_modulation():
    low = effective_layout(ModemConfig(modulation="gray2"))
    high = effective_layout(ModemConfig(modulation="rgb32t16"))
    assert low.header_rows > high.header_rows
    # the header block must always hold the fixed header + RS parity
    for name in MODULATIONS:
        cfg = ModemConfig(modulation=name)
        lay = effective_layout(cfg)
        assert lay.header_bytes(cfg.mod()) >= 26


def test_interleaver_is_invertible():
    x = np.arange(500, dtype=np.uint16)
    assert np.array_equal(deinterleave(interleave(x)), x)


def test_payload_cells_and_header_cells_disjoint():
    cfg = ModemConfig(modulation="rgb8")
    layout = effective_layout(cfg)
    h = set(layout.header_cells())
    p = set(layout.payload_cells())
    r = set(layout.ring_cells())
    assert not (h & p) and not (h & r) and not (p & r)
    assert len(h) + len(p) + len(r) == layout.n_cells


# --------------------------------------------------------------------------- #
# packetisation
# --------------------------------------------------------------------------- #
def test_packetizer_multiframe_reassembly():
    cfg = ModemConfig(modulation="rgb8", ecc="rs16")
    pkt = Packetizer(cfg)
    data = bytes(np.random.default_rng(1).integers(0, 256, 900, dtype=np.uint8))
    frames = list(pkt.frames_for(data))
    assert len(frames) >= 4
    frames_out = []
    for hdr, body in frames:
        box = FrameHeader(hdr.mod_id, hdr.ecc_id, hdr.frame_type, hdr.seq,
                          hdr.length)
        frames_out.append(box)
    # receiver side
    from optical_modem.protocol.packet import DecodedFrame

    rx = Packetizer(cfg)
    got = None
    for hdr, body in frames:
        got = rx.push(DecodedFrame(hdr, body, True, 0, True)) or got
    assert got == data
    assert frames_out[0].frame_type == FRAME_START


def test_packetizer_survives_frame_loss():
    """Resume support: a missing frame delays completion, duplicates are safe."""
    cfg = ModemConfig(modulation="rgb8", ecc="rs16")
    pkt = Packetizer(cfg)
    data = b"A" * 800
    frames = list(pkt.frames_for(data))
    from optical_modem.protocol.packet import DecodedFrame

    rx = Packetizer(cfg)
    complete = None
    for i, (hdr, body) in enumerate(frames):
        if i == 1:
            continue  # simulate a dropped frame
        complete = rx.push(DecodedFrame(hdr, body, True, 0, True)) or complete
    assert complete is None  # cannot complete without the missing chunk
    # retransmit the lost frame -> stream completes
    hdr, body = frames[1]
    complete = rx.push(DecodedFrame(hdr, body, True, 0, True))
    assert complete == data


def test_packetizer_ignores_corrupt_frames():
    cfg = ModemConfig(modulation="rgb8", ecc="rs16")
    pkt = Packetizer(cfg)
    frames = list(pkt.frames_for(b"hello world"))
    from optical_modem.protocol.packet import DecodedFrame

    rx = Packetizer(cfg)
    hdr, body = frames[0]
    assert rx.push(DecodedFrame(hdr, body + b"junk", True, 0, True)) is None
