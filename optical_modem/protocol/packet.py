"""Packet ('frame') layer: header layout, CRC, chunking of a byte stream.

Frame layout on air (spec 4.1)::

    ┌──────────────── known calibration ring (pilot cells) ────────────────┐
    │  HEADER block  (fixed rate: 8 data bytes + RS(16) parity)            │
    │  PAYLOAD block (user data, protected by the selected ECC)            │
    └──────────────────────────────────────────────────────────────────────┘

Header fields (10 bytes, little endian)::

    magic      2B   b"OM"            frame sync
    version    1B   hi nibble major, lo nibble minor
    mod_id     1B   modulation id (see config.MODULATIONS)
    ecc_id     1B   ecc profile id
    frame_type 1B   0=DATA 1=START 2=END 3=ACK 4=NACK 5=META
    seq        2B   sequence number (16 bit, wraps)
    length     2B   payload length in bytes (incl. 2B CRC)
    mask       1B   hi 2 bits = mask family, lo 6 bits = seed (protocol.mask)

``seq``/``length`` were 1 byte in the first draft; 16 bits are required because
the spec allows payloads up to 1024 B per frame.  A CRC is appended to the
payload by :class:`Packetizer`.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from ..config import HEADER_DATA_LEN, ModemConfig

HEADER_MAGIC = b"OM"
HEADER_VERSION = 0x11
#: RS(16) parity is always used for the header so it can be read before the
#: sender's ECC choice is known.
HEADER_RS_PARITY = 16

FRAME_DATA, FRAME_START, FRAME_END, FRAME_ACK, FRAME_NACK, FRAME_META = range(6)
FRAME_NAMES = {
    0: "DATA",
    1: "START",
    2: "END",
    3: "ACK",
    4: "NACK",
    5: "META",
}

_HEADER_STRUCT = struct.Struct("<2sBBBBHHB")


@dataclass
class FrameHeader:
    mod_id: int = 2
    ecc_id: int = 2
    frame_type: int = FRAME_DATA
    seq: int = 0
    length: int = 0
    #: whitening mask for the payload block — see :mod:`optical_modem.protocol.mask`
    mask_byte: int = 0x40          # family PRNG, seed 0

    @property
    def mask_family(self) -> int:
        return (int(self.mask_byte) >> 6) & 0x3

    @property
    def mask_seed(self) -> int:
        return int(self.mask_byte) & 0x3F

    def pack(self) -> bytes:
        return _HEADER_STRUCT.pack(
            HEADER_MAGIC,
            HEADER_VERSION,
            self.mod_id & 0xFF,
            self.ecc_id & 0xFF,
            self.frame_type & 0xFF,
            self.seq & 0xFFFF,
            self.length & 0xFFFF,
            self.mask_byte & 0xFF,
        )

    @classmethod
    def unpack(cls, data: bytes) -> "FrameHeader":
        if len(data) < HEADER_DATA_LEN:
            raise ValueError("short header")
        (magic, ver, mod_id, ecc_id, ftype, seq, length,
         mask_byte) = _HEADER_STRUCT.unpack(data[:HEADER_DATA_LEN])
        if magic != HEADER_MAGIC:
            raise ValueError("bad magic")
        if ver >> 4 != HEADER_VERSION >> 4:
            raise ValueError("unsupported version")
        return cls(mod_id=mod_id, ecc_id=ecc_id, frame_type=ftype, seq=seq,
                   length=length, mask_byte=mask_byte)

    def __str__(self) -> str:
        return (
            f"{FRAME_NAMES.get(self.frame_type, '?'):<5} seq={self.seq:5d} "
            f"len={self.length:4d} mod={self.mod_id} ecc={self.ecc_id}"
        )


# --------------------------------------------------------------------------- #
# CRC
# --------------------------------------------------------------------------- #
def crc16(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFF


def payload_with_crc(data: bytes) -> bytes:
    return data + struct.pack("<H", crc16(data))


def check_crc(buf: bytes) -> tuple[bytes, bool]:
    if len(buf) < 2:
        return b"", False
    body, crc = buf[:-2], struct.unpack("<H", buf[-2:])[0]
    return body, crc == crc16(body)


# --------------------------------------------------------------------------- #
# Packetizer
# --------------------------------------------------------------------------- #
@dataclass
class DecodedFrame:
    header: FrameHeader
    payload: bytes
    crc_ok: bool
    n_corrected: int
    fec_ok: bool
    quality: float = 0.0

    @property
    def ok(self) -> bool:
        return self.crc_ok and self.fec_ok


class Packetizer:
    """Splits a byte stream into frames and reassembles it on the far side."""

    def __init__(self, cfg: ModemConfig):
        self.cfg = cfg
        # every chunk carries a CRC16 and START/END frames add a 4-byte
        # (total_frames, message_crc) trailer, so leave room for both
        self.max_payload = max(1, cfg.describe()["max_user_payload"] - 4)
        self._buf: dict[int, bytes] = {}
        self._total: int | None = None
        self._msg_crc: int | None = None
        self._next_seq = 0
        self._expected = 0
        #: reassembly statistics — the UI shows these, and they must survive the
        #: buffer being cleared when a message completes
        self.received = 0
        self.duplicates = 0

    # ---- transmit side ---------------------------------------------------
    def frames_for(self, data: bytes):
        """Yield (FrameHeader, payload_bytes) for a file/chat message."""
        chunks = [
            data[i : i + self.max_payload]
            for i in range(0, len(data), self.max_payload)
        ] or [b""]
        total = len(chunks)
        for i, chunk in enumerate(chunks):
            ftype = FRAME_DATA
            if total == 1:
                # a one-frame message is its own start *and* end: without a
                # START/END trailer the receiver never learns `total` and the
                # transfer can never complete
                ftype = FRAME_START
            elif i == 0:
                ftype = FRAME_START
            elif i == total - 1:
                ftype = FRAME_END
            # the frame codec appends the CRC16 itself, so the packet layer only
            # adds its own trailer: total frame count + whole-message CRC
            body = chunk
            if ftype in (FRAME_START, FRAME_END):
                body = body + struct.pack("<HH", total, crc16(data))
            seq = self._next_seq & 0xFFFF
            hdr = FrameHeader(
                mod_id=self.cfg.mod().mod_id,
                ecc_id=self.cfg.ecc_profile().ecc_id,
                frame_type=ftype,
                seq=seq,
                length=len(body) + 2,  # + CRC16 appended by FrameCodec
                # a fresh whitening seed per frame, so consecutive frames of a
                # transfer do not share the same tile pattern
                mask_byte=self.cfg.mask_byte(seq),
            )
            self._next_seq += 1
            yield hdr, body

    # ---- receive side ----------------------------------------------------
    @property
    def buffered(self) -> int:
        """How many *distinct* frames are held (duplicates do not count)."""
        return len(self._buf)

    @property
    def expected(self) -> int | None:
        """Total frames announced by the START/END trailer, if known."""
        return self._total

    def reset(self, stats: bool = True) -> None:
        """Forget buffered frames (``stats=False`` keeps the counters)."""
        self._buf.clear()
        self._total = None
        self._msg_crc = None
        if stats:
            self.received = 0
            self.duplicates = 0

    def push(self, frame: DecodedFrame) -> bytes | None:
        """Add a decoded frame; return the completed message when available.

        Frames are buffered by sequence number, so duplicates are harmless and
        an out-of-order arrival is simply kept until the gap is filled by a
        retransmission — that is what makes *resume after failure* work without
        restarting the transfer.
        """
        if not frame.ok:
            return None
        body = frame.payload
        seq = frame.header.seq
        # the length field counts the payload plus its CRC16 (which the frame
        # codec stripped before handing the payload over)
        if frame.header.length not in (0, len(body) + 2):
            return None
        if frame.header.frame_type in (FRAME_START, FRAME_END) and len(body) >= 4:
            total, msg_crc = struct.unpack("<HH", body[-4:])
            self._total = total
            self._msg_crc = msg_crc
            body = body[:-4]
        if seq in self._buf:
            self.duplicates += 1     # a repeat exposure of a frame we already have
        else:
            self.received += 1
        self._buf[seq] = body
        if self._total is None or len(self._buf) < self._total:
            return None
        ordered = [self._buf.get(s) for s in range(self._total)]
        if any(c is None for c in ordered):
            return None
        joined = b"".join(ordered)  # type: ignore[arg-type]
        if self._msg_crc is not None and crc16(joined) != self._msg_crc:
            return None
        self.reset(stats=False)      # keep the counters: the receipt reports them
        return joined
