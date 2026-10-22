"""Byte <-> tile mapping: bit packing, interleaving and the two frame blocks.

A frame is a square array of **cells**.  One *symbol* occupies ``cps`` cells
(1 normally, 2 in hue-differential pair mode) and carries
``color_bits + pattern_bits`` bits.  Each symbol is drawn as a tile: a colour
from the palette, masked by a binary pattern (cimbar-style "image hash" icon).

Block sizing
------------
Both the header block and the payload block are fixed in size, so we solve for
the largest *data* length ``m`` whose FEC encoding still fits (binary search),
keeping the layout identical for every frame of a given configuration.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from ..config import (
    HEADER_DATA_LEN,
    FrameLayout,
    ModemConfig,
    Modulation,
)
from . import ecc as ecc_mod
from .mask import HEADER_MASK_BYTE, mask_values
from .packet import HEADER_RS_PARITY, FrameHeader, payload_with_crc

# --------------------------------------------------------------------------- #
# bit packing helpers
# --------------------------------------------------------------------------- #


def bytes_to_symbols(data: bytes, bits: int, n_out: int) -> np.ndarray:
    """MSB-first unpacking of ``data`` into ``n_out`` symbol values."""
    arr = np.frombuffer(data, dtype=np.uint8)
    bit_arr = np.unpackbits(arr)
    need = n_out * bits
    if len(bit_arr) < need:
        bit_arr = np.concatenate([bit_arr, np.zeros(need - len(bit_arr), np.uint8)])
    bit_arr = bit_arr[:need].reshape(n_out, bits)
    weights = (1 << np.arange(bits - 1, -1, -1)).astype(np.uint16)
    return (bit_arr.astype(np.uint16) * weights).sum(axis=1).astype(np.uint16)


def symbols_to_bytes_map(symbols: np.ndarray, bits: int) -> tuple[bytes, np.ndarray]:
    """Pack symbols MSB-first; also return which symbol feeds each bit."""
    n = len(symbols)
    out = np.zeros((n, bits), dtype=np.uint8)
    for i in range(bits):
        out[:, i] = (symbols >> (bits - 1 - i)) & 1
    owner = np.repeat(np.arange(n), bits)
    return np.packbits(out.reshape(-1)).tobytes(), owner


def symbols_to_bytes(symbols: np.ndarray, bits: int) -> bytes:
    return symbols_to_bytes_map(symbols, bits)[0]


# --------------------------------------------------------------------------- #
# interleaver
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=64)
def interleave_order(n: int, seed: int = 1234) -> np.ndarray:
    rng = np.random.default_rng(seed)
    order = rng.permutation(n)
    order.setflags(write=False)
    return order


def interleave(symbols: np.ndarray, seed: int = 1234) -> np.ndarray:
    """Spread neighbouring symbols apart (burst-noise robustness)."""
    order = interleave_order(len(symbols), seed)
    out = np.empty_like(symbols)
    out[order] = symbols
    return out


def deinterleave(symbols: np.ndarray, seed: int = 1234) -> np.ndarray:
    order = interleave_order(len(symbols), seed)
    return symbols[order]


# --------------------------------------------------------------------------- #
# layout helpers
# --------------------------------------------------------------------------- #


def effective_layout(cfg: ModemConfig) -> FrameLayout:
    """Header rows must be tall enough for the fixed-rate header block.

    A finder pattern steals symbols from the header band as well as the payload
    (it sits in the inner corners, and the header occupies the top rows), so the
    check is "is the *carved* header still big enough?" — and the layout grows a
    row at a time until it is.  That is why enabling a finder can never silently
    truncate the header: either it fits, or it costs a header row.
    """
    layout = cfg.layout
    mod = cfg.mod()
    need_bits = (HEADER_DATA_LEN + HEADER_RS_PARITY) * 8
    max_rows = max(0, layout.rows - 2 * layout.ring - 1)      # keep a payload row
    rows = max(1, layout.header_rows)
    best = layout
    while rows <= max(1, max_rows):
        cand = FrameLayout(layout.cells, layout.cps, layout.ring, rows,
                           layout.finder, layout.finder_span, layout.finder_size)
        best = cand
        if len(cand.header_symbols_xy()) * mod.bits >= need_bits:
            return cand
        rows += 1
    return best


def _pick_erasures(byte_conf: np.ndarray, nsym: int, frac: float = 0.6) -> list[int]:
    """Indices of the least reliable bytes, capped to the RS erasure budget."""
    if nsym <= 0 or len(byte_conf) == 0:
        return []
    med = float(np.median(byte_conf))
    thr = med * frac
    bad = np.nonzero(byte_conf < thr)[0]
    if len(bad) == 0:
        return []
    order = bad[np.argsort(byte_conf[bad])][: max(1, nsym - 1)]
    return [int(i) for i in order]


def _confidence_to_erasures(owner: np.ndarray, soft: np.ndarray, bits: int,
                            n_bytes: int, nsym: int) -> list[int]:
    """Per-symbol soft confidence -> per-byte erasure list."""
    bit_conf = np.array(
        [soft[o * bits + (i % bits)] for i, o in enumerate(owner)], np.float32
    )
    byte_conf = np.full(max(1, n_bytes), 1e9, np.float32)
    np.minimum.at(byte_conf, np.arange(len(bit_conf)) // 8, bit_conf)
    return _pick_erasures(byte_conf[:n_bytes], nsym)


# --------------------------------------------------------------------------- #
# the tile frame
# --------------------------------------------------------------------------- #


@dataclass
class TileFrame:
    """A rendered-ready description of one coded frame, in symbol space."""

    rows: int
    cols: int
    values: np.ndarray  # (rows, cols) uint16 symbol value
    colors: np.ndarray  # (rows, cols) uint8  palette index (first cell of symbol)
    patterns: np.ndarray  # (rows, cols) uint8 pattern index

    def to_cells(self, layout: FrameLayout,
                 mod: Modulation) -> tuple[np.ndarray, np.ndarray]:
        """Expand to full cell grids ``(cells, cells)`` of colours and patterns."""
        g = layout.cells
        cell_colors = np.zeros((g, g), np.uint8)
        cell_patterns = np.zeros((g, g), np.uint8)
        rows_idx, cols_idx = layout.symbol_cell_arrays()
        cell_colors[rows_idx, cols_idx] = self.colors.reshape(-1)
        cell_patterns[rows_idx, cols_idx] = self.patterns.reshape(-1)
        if mod.pair:
            # the second cell of every pair carries the hue+180 complement with
            # the same pattern, so a difference of the two cells isolates colour
            comp = np.asarray([mod.pair_partner(c) for c in range(mod.n_colors)],
                              np.uint8)
            cell_colors[:, 1::2] = comp[cell_colors[:, 0::2]]
            cell_patterns[:, 1::2] = cell_patterns[:, 0::2]
        return cell_colors, cell_patterns


# --------------------------------------------------------------------------- #
# frame codec
# --------------------------------------------------------------------------- #
class FrameCodec:
    """Turns (header, payload) into a symbol grid and back."""

    def __init__(self, cfg: ModemConfig):
        self.cfg = cfg
        self.mod: Modulation = cfg.mod()
        self.layout: FrameLayout = effective_layout(cfg)
        self.ring = self.layout.ring_values(self.mod)
        self.finder: dict[tuple[int, int], int] = self.layout.finder_map()
        self.header_xy = self.layout.header_symbols_xy()
        self.payload_xy = self.layout.payload_symbols_xy()
        self.ring_xy = self.layout.ring_symbols_xy()
        self._check_regions()
        self.hdr_cap = self.layout.header_bytes(self.mod)
        self.pay_cap = self.layout.payload_bytes(self.mod)
        self.header_fec = ecc_mod.ReedSolomon(HEADER_RS_PARITY)
        self.payload_fec = ecc_mod.make_fec(cfg.ecc_profile())
        self.header_on_air = len(self.header_fec.encode(b"\x00" * HEADER_DATA_LEN))
        self.body_capacity = self._max_body()
        self._len_cache: dict[int, int] = {}

    # ---- layout invariants ----------------------------------------------
    def _check_regions(self) -> None:
        """Ring, finder, header and payload must never share a symbol.

        This is the guarantee behind "the finder does not overwrite data": if a
        layout ever produced overlapping regions, :meth:`encode` would paint a
        finder over a payload tile and the frame would decode to garbage *only
        at some payload lengths* — the nastiest possible bug.  It is cheaper to
        refuse the config.
        """
        regions = {"ring": set(self.ring_xy), "header": set(self.header_xy),
                   "payload": set(self.payload_xy), "finder": set(self.finder)}
        names = list(regions)
        for a in range(len(names)):
            for b in range(a + 1, len(names)):
                overlap = regions[names[a]] & regions[names[b]]
                if overlap:
                    raise ValueError(
                        f"frame layout overlaps: {names[a]} and {names[b]} share "
                        f"{len(overlap)} symbol(s), e.g. {sorted(overlap)[:3]} — "
                        f"lower the finder size/span or the header rows")
        # and every symbol of the grid must belong to exactly one region
        total = (len(regions["ring"]) + len(regions["header"])
                 + len(regions["payload"]) + len(regions["finder"]))
        if total != self.layout.n_symbols:
            raise ValueError(
                f"frame layout leaves {self.layout.n_symbols - total} symbol(s) "
                f"unassigned ({total} assigned of {self.layout.n_symbols})")

    # ---- finder tones ----------------------------------------------------
    def finder_colors(self) -> tuple[int, int]:
        """``(bright, dark)`` palette indices used by the finder tiles."""
        from .finder import bright_dark_indices

        return bright_dark_indices(self.mod.palette)

    def finder_mask(self) -> np.ndarray | None:
        """``size x size`` 0/1 mask of one finder block (for verification)."""
        from .finder import auto_size, get_shape, mask_for

        shape = get_shape(self.layout.finder)
        if shape is None or not self.finder:
            return None
        size = self.layout.finder_size or auto_size(shape, self.layout.rows,
                                                    self.layout.cols)
        return mask_for(shape, size)

    def finder_blocks(self) -> list[tuple[int, int]]:
        """Top-left symbol coordinates of the finder blocks, in grid order."""
        from .finder import auto_size, block_origins, get_shape

        shape = get_shape(self.layout.finder)
        if shape is None:
            return []
        size = self.layout.finder_size or auto_size(shape, self.layout.rows,
                                                    self.layout.cols)
        return block_origins(self.layout.finder_span, size, self.layout.rows,
                             self.layout.cols, self.layout.ring)

    # ---- sizing ----------------------------------------------------------
    def _max_body(self) -> int:
        lo, hi = 0, self.pay_cap
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if len(self.payload_fec.encode(bytes(mid))) <= self.pay_cap:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def encoded_len(self, body_len: int) -> int:
        if body_len not in self._len_cache:
            self._len_cache[body_len] = len(self.payload_fec.encode(bytes(body_len)))
        return self._len_cache[body_len]

    # ---- transmit --------------------------------------------------------
    def encode(self, header: FrameHeader, payload: bytes) -> TileFrame:
        rows, cols = self.layout.rows, self.layout.cols
        colors = np.zeros((rows, cols), np.uint8)
        patterns = np.zeros((rows, cols), np.uint8)
        values = np.zeros((rows, cols), np.uint16)

        # --- calibration ring (solid tiles, known colours) ---------------
        for (i, j), (color_idx, pat) in self.ring.items():
            colors[i, j] = color_idx
            patterns[i, j] = pat
            values[i, j] = self.mod.symbol_to_bits(color_idx, pat)

        # --- finder pattern: written into *reserved* symbols only --------
        # `self.finder` was removed from header_xy/payload_xy in __init__, so
        # this can never land on data.
        if self.finder:
            bright, dark = self.finder_colors()
            for (i, j), level in self.finder.items():
                color_idx = bright if level else dark
                colors[i, j] = color_idx
                patterns[i, j] = 0                    # solid: blur-safe
                values[i, j] = self.mod.symbol_to_bits(color_idx, 0)

        # --- header: always RS(16) so it can be read before ECC is known --
        hdr_enc = self.header_fec.encode(header.pack())
        hdr_enc = hdr_enc + b"\x00" * (self.hdr_cap - len(hdr_enc))
        hdr_vals = bytes_to_symbols(hdr_enc, self.mod.bits, len(self.header_xy))
        # the header block must be readable *before* its own mask byte is known,
        # so it uses a public constant mask; the payload uses the header's
        hdr_vals = hdr_vals ^ mask_values(HEADER_MASK_BYTE, self.header_xy,
                                          self.mod.n_values)
        self._place(colors, patterns, values, self.header_xy, hdr_vals, 99)

        # --- payload: crc + FEC, zero padded to the block's data capacity --
        body = payload_with_crc(payload)
        if len(body) > self.body_capacity:
            raise ValueError(
                f"payload too large: {len(body)}B > {self.body_capacity}B "
                f"(body incl. CRC; increase cells/modulation or lower ECC)"
            )
        enc = self.payload_fec.encode(body)
        enc = enc + b"\x00" * (self.pay_cap - len(enc))
        pay_vals = bytes_to_symbols(enc, self.mod.bits, len(self.payload_xy))
        # whitening: without it the zero-padded tail of a short payload is one
        # huge slab of identical dark tiles (bad for local statistics, bad for
        # looks, and a waste of the display)
        pay_vals = pay_vals ^ mask_values(header.mask_byte, self.payload_xy,
                                          self.mod.n_values)
        self._place(colors, patterns, values, self.payload_xy, pay_vals, 1234)
        return TileFrame(rows, cols, values, colors, patterns)

    def _place(self, colors, patterns, values, xy, sym_values, seed):
        if self.cfg.interleave:
            sym_values = interleave(sym_values, seed)
        for (i, j), v in zip(xy, sym_values):
            color_idx, pat = self.mod.split_value(int(v))
            colors[i, j] = color_idx
            patterns[i, j] = pat
            values[i, j] = int(v)

    # ---- receive ---------------------------------------------------------
    def read_header(self, values: np.ndarray, soft: np.ndarray | None = None):
        sym = np.array([values[i, j] for i, j in self.header_xy], np.uint16)
        sft = None
        if soft is not None:
            sft = np.array([soft[i, j] for i, j in self.header_xy], np.float32)
        if self.cfg.interleave:
            sym = deinterleave(sym, 99)
            if sft is not None:
                sft = deinterleave(sft, 99)
        sym = sym ^ mask_values(HEADER_MASK_BYTE, self.header_xy, self.mod.n_values)
        hdr_bytes, owner = symbols_to_bytes_map(sym, self.mod.bits)
        n = self.header_on_air
        erasures = None
        if sft is not None:
            erasures = _confidence_to_erasures(owner, sft.reshape(-1),
                                               self.mod.bits, len(hdr_bytes),
                                               HEADER_RS_PARITY)
        dec, ncorr, ok = self.header_fec.decode(hdr_bytes[:n], erasures)
        if not ok:
            raise ValueError("header FEC failed")
        return FrameHeader.unpack(dec[:HEADER_DATA_LEN]), ncorr, ok

    def decode_headerless(self, values: np.ndarray, header: FrameHeader,
                          soft: np.ndarray | None = None):
        sym = np.array([values[i, j] for i, j in self.payload_xy], np.uint16)
        sft = None
        if soft is not None:
            sft = np.array([soft[i, j] for i, j in self.payload_xy], np.float32)
        if self.cfg.interleave:
            sym = deinterleave(sym, 1234)
            if sft is not None:
                sft = deinterleave(sft, 1234)
        sym = sym ^ mask_values(header.mask_byte, self.payload_xy,
                                self.mod.n_values)
        pay_bytes, owner = symbols_to_bytes_map(sym, self.mod.bits)
        n = min(self.encoded_len(header.length), len(pay_bytes))
        erasures = None
        if sft is not None:
            erasures = _confidence_to_erasures(
                owner, sft.reshape(-1), self.mod.bits, len(pay_bytes),
                int(getattr(self.payload_fec, "nsym", 0)),
            )
        dec, ncorr, fec_ok = self.payload_fec.decode(pay_bytes[:n], erasures)
        return dec[: header.length], ncorr, fec_ok

    def decode_grid(self, values: np.ndarray, header: FrameHeader | None = None,
                    soft: np.ndarray | None = None):
        ncorr = 0
        if header is None:
            header, ncorr, _ = self.read_header(values, soft)
        body, n2, fec_ok = self.decode_headerless(values, header, soft)
        return header, body, ncorr + n2, fec_ok
