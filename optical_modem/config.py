"""Configuration objects for the optical modem.

Everything that a user can tune (per the spec's parameter table) lives here so
that the transmitter, receiver, trainer and benchmark all agree on one schema.

Since v0.2 the modulation model is *tile based*, in the spirit of cimbar:

    symbol = colour (2^color_bits choices)  x  pattern (2^pattern_bits choices)

and an optional **differential pair** mode draws every symbol twice, in a colour
and its hue+180 degree complement, so the receiver can classify on the
difference and cancel illumination.
"""
from __future__ import annotations

import dataclasses
import colorsys
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Tuple

import numpy as np

# --------------------------------------------------------------------------- #
# colour palettes
# --------------------------------------------------------------------------- #


def _hsv_to_rgb(h: float, s: float, v: float) -> Tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))


def build_hue_palette(n_pairs: int, levels=(1.0,), sats=(1.0,),
                      hue_offset: float = 15.0) -> tuple[tuple, tuple]:
    """Palette of ``2 * n_pairs * len(levels) * len(sats)`` colours.

    Colours come in **hue+180 degree pairs**: index ``i`` and ``i ^ n_pairs``
    are complementary (same saturation and value).  That exact closure is what
    makes differential-pair transmission possible: the complement of every
    palette entry is itself a palette entry.

    Returns ``(palette, complement_index)``.
    """
    hues = [(hue_offset + 360.0 * i / (2 * n_pairs)) / 360.0 for i in range(2 * n_pairs)]
    col = []
    for v in levels:
        for s in sats:
            for h in hues:
                col.append(_hsv_to_rgb(h, s, v))
    palette = tuple(col)
    # index layout: (value, saturation, hue) -> hue index is the fastest axis and
    # there are exactly 2*n_pairs hues, so hue+180 is i + n_pairs
    comp = []
    per_level = 2 * n_pairs
    for i in range(len(palette)):
        base = (i // per_level) * per_level
        hue_i = (i - base + n_pairs) % per_level
        comp.append(base + hue_i)
    if len(set(comp)) != len(palette) or any(comp[comp[i]] != i for i in range(len(comp))):
        raise RuntimeError("palette is not closed under hue+180; adjust n_pairs")
    return palette, tuple(comp)


def _label_by_distance(points: np.ndarray) -> tuple[int, ...]:
    """Assign bit patterns so that *nearby* colours differ in few bits.

    Greedy: start from the darkest point, walk a nearest-neighbour chain and
    assign consecutive Gray codes.  Adjacent palette entries then differ by one
    bit, which is what the FEC layer is sized for.
    """
    n = len(points)
    bits = max(1, int(round(np.log2(n))))
    if 2**bits != n:
        raise ValueError("palette size must be a power of two")
    # nearest-neighbour chain in RGB
    remaining = list(range(n))
    start = int(np.argmin(points.sum(axis=1)))
    order = [start]
    remaining.remove(start)
    while remaining:
        last = points[order[-1]]
        d = np.linalg.norm(points[remaining] - last, axis=1)
        nxt = remaining[int(np.argmin(d))]
        order.append(nxt)
        remaining.remove(nxt)
    labels = [0] * n
    for rank, idx in enumerate(order):
        g = rank ^ (rank >> 1)  # Gray code
        labels[idx] = g
    return tuple(labels)


def min_palette_distance(palette) -> float:
    p = np.asarray(palette, np.float32)
    d = np.linalg.norm(p[:, None] - p[None], axis=-1)
    np.fill_diagonal(d, 1e9)
    return float(d.min())


# --------------------------------------------------------------------------- #
# tile patterns (cimbar-style image-hash symbols)
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=32)
def build_patterns(n_patterns: int, size: int = 4, seed: int = 0) -> np.ndarray:
    """Binary ``size x size`` tile patterns with maximum mutual Hamming distance.

    ``patterns[0]`` is always solid (all ones): it carries no pattern bits and is
    what the calibration ring uses, so colour fitting never looks at a hole.
    The rest are chosen greedily from a random pool, maximising the *minimum*
    Hamming distance to everything already selected — the same idea as cimbar's
    hand-drawn symbols, but generated and measured instead of drawn.
    Requires a ~50% fill ratio so blur cannot bias a pattern towards bright/dark.
    The centre sub-cell is forced on, which keeps a clean colour sample available.
    """
    if n_patterns < 1:
        raise ValueError("n_patterns must be >= 1")
    solid = np.ones((size, size), np.uint8)
    if n_patterns == 1:
        return solid[None]
    rng = np.random.default_rng(seed + 1000 * size + n_patterns)
    centre = (slice(size // 2 - 1, size // 2 + 1), slice(size // 2 - 1, size // 2 + 1))
    chosen = [solid]
    target_fill = size * size // 2
    pool = []
    for _ in range(4000):
        p = (rng.random((size, size)) < 0.5).astype(np.uint8)
        p[centre] = 1
        # balance fill so blur cannot separate patterns by brightness alone
        while p.sum() > target_fill:
            yy, xx = np.nonzero(p)
            i = rng.integers(len(yy))
            if (yy[i], xx[i]) not in {(size // 2 - 1, size // 2 - 1),
                                      (size // 2 - 1, size // 2),
                                      (size // 2, size // 2 - 1),
                                      (size // 2, size // 2)}:
                p[yy[i], xx[i]] = 0
        while p.sum() < target_fill:
            yy, xx = np.nonzero(p == 0)
            i = rng.integers(len(yy))
            p[yy[i], xx[i]] = 1
        pool.append(p)
    while len(chosen) < n_patterns and pool:
        best, best_score = None, -1
        for p in pool:
            d = min(int((p != q).sum()) for q in chosen)
            if d > best_score:
                best, best_score = p, d
        if best is None:
            break
        chosen.append(best)
        pool = [p for p in pool if int((p != best).sum()) > 0]
    if len(chosen) < n_patterns:
        raise RuntimeError(f"could not build {n_patterns} patterns of size {size}")
    return np.stack(chosen).astype(np.uint8)


def pattern_min_distance(patterns: np.ndarray) -> int:
    n = len(patterns)
    d = min(int((patterns[i] != patterns[j]).sum())
            for i in range(n) for j in range(i + 1, n))
    return d


# --------------------------------------------------------------------------- #
# modulation / constellation
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Modulation:
    """A tile-to-bits mapping (colour x pattern, optionally hue-differential).

    Attributes
    ----------
    name        : identifier used in headers / configs
    mod_id      : numeric id written in the frame header (byte)
    palette     : ``(n_colors, 3)`` RGB triples
    color_bits  : log2(n_colors)
    labels      : colour index -> colour bit pattern (Hamming-minimal)
    pattern_size: sub-cell resolution of the tile pattern (P x P)
    patterns    : ``(n_patterns, P, P)`` 0/1 masks, index 0 == solid
    pattern_bits: log2(n_patterns) or 0
    complement  : colour index -> hue+180 twin index (differential pairs)
    pair        : if True every symbol is drawn as two adjacent cells
                  (colour, complement) so classification can use the difference
    """

    name: str
    mod_id: int
    palette: Tuple[Tuple[int, int, int], ...]
    color_bits: int
    labels: Tuple[int, ...]
    pattern_size: int = 1
    patterns: Tuple = ()
    pattern_bits: int = 0
    complement: Tuple[int, ...] = ()
    pair: bool = False

    # ---- basic properties ------------------------------------------------
    @property
    def n_colors(self) -> int:
        return len(self.palette)

    @property
    def n_patterns(self) -> int:
        return max(1, len(self.patterns))

    @property
    def bits(self) -> int:
        """Bits carried by one *symbol* (a pair counts as one symbol)."""
        return self.color_bits + self.pattern_bits

    @property
    def n_values(self) -> int:
        """Size of the symbol alphabet (colours x patterns)."""
        return int(self.n_colors) * int(self.n_patterns)

    @property
    def cells_per_symbol(self) -> int:
        return 2 if self.pair else 1

    @property
    def min_px_per_cell(self) -> int:
        """Smallest on-screen cell size that still resolves the symbol.

        Colour-only symbols only need the cell interior, so ~8 px is enough.
        Patterned tiles need at least three pixels per pattern pixel for the
        pattern to survive resampling; below that the measured symbol-error
        rate climbs past what the FEC can repair (measured: 14 px/cell is
        clean, 11 px/cell gives ~3% symbol errors).
        """
        if not self.pattern_bits:
            return 8
        return 4 * self.pattern_size

    @property
    def bits_per_cell(self) -> float:
        return self.bits / self.cells_per_symbol

    @property
    def n_symbols(self) -> int:
        return 1 << self.bits

    @property
    def pattern_mask(self) -> np.ndarray:
        if self.pattern_bits == 0:
            return np.ones((1, self.pattern_size, self.pattern_size), np.uint8)
        return np.asarray(self.patterns, np.uint8)

    # ---- bit <-> symbol --------------------------------------------------
    def bits_to_symbol_index(self) -> Tuple[int, ...]:
        """colour bit pattern -> colour index (inverse of ``labels``)."""
        return tuple(self.labels.index(v) for v in range(self.n_colors))

    def bits_to_palette_index(self, value: int) -> int:
        return self.bits_to_symbol_index()[value]

    def symbol_to_bits(self, color_idx: int, pattern_idx: int = 0) -> int:
        value = self.labels[int(color_idx)]
        return (int(pattern_idx) << self.color_bits) | value

    def split_value(self, symbol_value: int) -> tuple[int, int]:
        """symbol value -> (colour index, pattern index)."""
        pattern_idx = symbol_value >> self.color_bits
        color_val = symbol_value & ((1 << self.color_bits) - 1)
        return self.bits_to_palette_index(color_val), int(pattern_idx)

    def pair_partner(self, color_idx: int) -> int:
        if not self.complement:
            return int(color_idx)
        return int(self.complement[int(color_idx)])

    # ---- human readable --------------------------------------------------
    def describe(self) -> str:
        p = f"{self.n_colors} colours ({self.color_bits} b)"
        if self.pattern_bits:
            p += f" x {self.n_patterns} patterns ({self.pattern_bits} b)"
        if self.pair:
            p += " / hue-differential pairs (2 cells)"
        return f"{self.name}: {self.bits} bit/symbol, {self.bits_per_cell:.1f} bit/cell — {p}"

    def to_dict(self) -> dict:
        return {
            "name": self.name, "mod_id": self.mod_id, "bits": self.bits,
            "color_bits": self.color_bits, "pattern_bits": self.pattern_bits,
            "pattern_size": self.pattern_size, "pair": self.pair,
            "colors": self.n_colors, "patterns": self.n_patterns,
            "bits_per_cell": self.bits_per_cell,
            "min_color_distance": round(min_palette_distance(self.palette), 1),
            "min_pattern_distance": (
                pattern_min_distance(np.asarray(self.patterns))
                if self.pattern_bits else 0
            ),
        }


def _mk(name, mod_id, palette, comp, pattern_bits=0, pattern_size=4, pair=False,
        seed=0) -> Modulation:
    n_colors = len(palette)
    color_bits = int(round(np.log2(n_colors)))
    if 2**color_bits != n_colors:
        raise ValueError(f"{name}: palette size {n_colors} is not a power of two")
    labels = _label_by_distance(np.asarray(palette, np.float32))
    n_pat = 1 << pattern_bits if pattern_bits else 1
    if pattern_bits == 0:
        pattern_size = 1                      # colour-only: single solid pixel
    patterns = tuple(
        tuple(map(tuple, p)) for p in build_patterns(n_pat, pattern_size, seed)
    )
    return Modulation(
        name=name, mod_id=mod_id, palette=tuple(palette), color_bits=color_bits,
        labels=labels, pattern_size=pattern_size, patterns=patterns,
        pattern_bits=pattern_bits, complement=tuple(comp), pair=pair,
    )


def _build_modulations() -> dict[str, Modulation]:
    mods: dict[str, Modulation] = {}

    # ---- legacy intensity / small palettes --------------------------------
    # NOTE dark levels are kept well above the tile background (DARK = 16) so
    # that a patterned tile still shows its holes after a phone camera has
    # squashed the tone curve; a near-black palette entry would make its own
    # pattern invisible.
    gray2 = ((64, 64, 64), (208, 208, 208))
    mods["gray2"] = _mk("gray2", 0, gray2, (0, 1))
    gray4 = ((64, 64, 64), (112, 112, 112), (160, 160, 160), (208, 208, 208))
    mods["gray4"] = _mk("gray4", 1, gray4, (0, 1, 2, 3))

    rgb8 = []
    for v in range(8):
        b2, b1, b0 = (v >> 2) & 1, (v >> 1) & 1, v & 1
        rgb8.append((200 if b2 else 80, 200 if b1 else 80, 200 if b0 else 80))
    mods["rgb8"] = _mk("rgb8", 2, tuple(rgb8), tuple(range(8)))

    comp = []
    for intensity in (1, 0):
        for v in range(8):
            b2, b1, b0 = (v >> 2) & 1, (v >> 1) & 1, v & 1
            scale, floor = (1.0, 0.0) if intensity else (0.60, 0.0)
            comp.append(tuple(
                int(round(((200 if b else 80) * scale) + floor)) for b in (b2, b1, b0)
            ))
    mods["rgb8x2"] = _mk("rgb8x2", 3, tuple(comp),
                         tuple((i ^ 8) for i in range(16)))

    # ---- hue palettes: 8 hues x 2 values = 16, complements closed ---------
    pal16, comp16 = build_hue_palette(4, levels=(0.88, 0.50), sats=(1.0,))
    mods["rgb16"] = _mk("rgb16", 4, pal16, comp16)
    mods["rgb16dp"] = _mk("rgb16dp", 5, pal16, comp16, pair=True)

    # ---- 32 colours: 8 hues x 2 values x 2 saturations -------------------
    pal32, comp32 = build_hue_palette(4, levels=(0.82, 0.55), sats=(1.0, 0.60))
    mods["rgb32"] = _mk("rgb32", 6, pal32, comp32)
    mods["rgb32dp"] = _mk("rgb32dp", 7, pal32, comp32, pair=True)

    # ---- patterned tiles (cimbar class): colour x pattern ----------------
    mods["rgb8t4"] = _mk("rgb8t4", 8, tuple(rgb8), tuple(range(8)),
                         pattern_bits=2, pattern_size=4)
    mods["rgb16t4"] = _mk("rgb16t4", 9, pal16, comp16, pattern_bits=2,
                          pattern_size=4)
    mods["rgb32t4"] = _mk("rgb32t4", 10, pal32, comp32, pattern_bits=2,
                          pattern_size=4, seed=7)
    mods["rgb32t16"] = _mk("rgb32t16", 11, pal32, comp32, pattern_bits=4,
                           pattern_size=4, seed=11)
    mods["rgb16t4dp"] = _mk("rgb16t4dp", 12, pal16, comp16, pattern_bits=2,
                            pattern_size=4, pair=True)
    return mods


MODULATIONS: dict[str, Modulation] = _build_modulations()


def get_modulation(name: str) -> Modulation:
    try:
        return MODULATIONS[name]
    except KeyError:
        raise KeyError(
            f"unknown modulation {name!r}; available: {sorted(MODULATIONS)}"
        ) from None


# --------------------------------------------------------------------------- #
# Forward error correction
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ECCProfile:
    name: str
    ecc_id: int
    kind: str  # "none" | "rs" | "ldpc"
    parity: int = 16
    ldpc_n: int = 648
    ldpc_k: int = 324
    description: str = ""

    @property
    def overhead(self) -> float:
        if self.kind == "rs":
            return self.parity / max(1, 255 - self.parity)
        if self.kind == "ldpc":
            return (self.ldpc_n - self.ldpc_k) / self.ldpc_k
        return 0.0


ECC_PROFILES: dict[str, ECCProfile] = {
    "none": ECCProfile("none", 0, "none", description="no FEC (benchmark only)"),
    "rs8": ECCProfile("rs8", 1, "rs", parity=8, description="light FEC, max rate"),
    "rs16": ECCProfile("rs16", 2, "rs", parity=16, description="balanced (default)"),
    "rs32": ECCProfile("rs32", 3, "rs", parity=32, description="heavy FEC, noisy"),
    "rs64": ECCProfile("rs64", 4, "rs", parity=64, description="very heavy FEC"),
    "ldpc12": ECCProfile("ldpc12", 5, "ldpc", ldpc_n=648, ldpc_k=324,
                         description="rate-1/2 LDPC"),
    "ldpc23": ECCProfile("ldpc23", 6, "ldpc", ldpc_n=576, ldpc_k=384,
                         description="rate-2/3 LDPC"),
}


def get_ecc(name: str) -> ECCProfile:
    try:
        return ECC_PROFILES[name]
    except KeyError:
        raise KeyError(
            f"unknown ecc profile {name!r}; available: {sorted(ECC_PROFILES)}"
        ) from None


def choose_ecc(quality: float) -> str:
    """Adaptive ECC: map measured channel quality (0=awful, 1=clean) to a profile."""
    if quality >= 0.90:
        return "rs8"
    if quality >= 0.70:
        return "rs16"
    if quality >= 0.45:
        return "rs32"
    return "rs64"


# --------------------------------------------------------------------------- #
# Display profiles (screen resolutions + symbol scale factor)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DisplayProfile:
    """A target screen and how large each symbol should be drawn.

    width/height  : the display (or capture) resolution
    symbol_px     : requested scale factor — pixels per *cell*
    margin_px     : black border kept around the code
    aspect        : the code stays square and centred in the display
    """

    width: int = 1920
    height: int = 1080
    symbol_px: int = 12
    margin_px: int = 32

    @property
    def label(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def usable(self) -> int:
        return max(0, min(self.width, self.height) - 2 * self.margin_px)

    def fit(self, cells: int | None = None, max_cells: int = 256,
            quiet_zone: int = 2, min_px: int = 1) -> tuple[int, int]:
        """Return ``(cells, symbol_px)`` that fills the display as well as possible.

        With ``cells`` given, the symbol size is grown to the largest integer that
        still fits (so a "scale factor" request is a *minimum*).  Without it, the
        grid is expanded to the largest even cell count that fits.

        ``quiet_zone`` is the number of *cells* of white margin around the coded
        grid: the drawn code is ``(cells + 2 * quiet_zone) * symbol_px`` wide, so
        that — not ``cells`` — is what has to fit inside :attr:`usable`.
        """
        usable = self.usable
        qz = 2 * max(0, int(quiet_zone))
        if cells is not None:
            px = max(1, usable // (int(cells) + qz))
            return int(cells), int(px)
        px = max(1, int(self.symbol_px), int(min_px))
        n = usable // px - qz
        if n % 2:  # keep the symbol grid even (differential pairs need pairs)
            n -= 1
        n = max(8, min(n, max_cells))
        return int(n), int(px)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def parse(cls, text: str, symbol_px: int = 12, margin_px: int = 32
              ) -> "DisplayProfile":
        """Parse ``"1920x1080"`` (also accepts ``1080p`` / ``720p``/ ``4k``)."""
        t = text.strip().lower()
        aliases = {"1080p": (1920, 1080), "720p": (1280, 720),
                   "1440p": (2560, 1440), "4k": (3840, 2160),
                   "2160p": (3840, 2160), "vga": (640, 480)}
        if t in aliases:
            w, h = aliases[t]
        elif "x" in t:
            w, h = (int(x) for x in t.split("x", 1))
        else:
            raise ValueError(f"cannot parse resolution {text!r}")
        return cls(width=w, height=h, symbol_px=symbol_px, margin_px=margin_px)


# --------------------------------------------------------------------------- #
# Frame layout
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FrameLayout:
    """Geometry of the coded frame.

    ``cells``  : physical cells per side (the frame is square)
    ``cps``    : cells per symbol (2 for hue-differential pair modes)
    ``ring``   : width of the known calibration ring, in **symbols**
    ``header_rows`` : symbol rows reserved for the fixed-rate header block
    """

    cells: int = 32
    cps: int = 1
    ring: int = 1
    header_rows: int = 4
    #: optional finder pattern (see :mod:`optical_modem.protocol.finder`)
    finder: str = "none"
    #: where the finder blocks go: corners | three | center | edges
    finder_span: str = "corners"
    #: 0 = pick from the code size (see ``finder.auto_size``)
    finder_size: int = 0

    #: kept for backwards compatibility with code that talks about the cell grid
    @property
    def grid(self) -> int:
        return self.cells

    @property
    def rows(self) -> int:
        """Symbol rows."""
        return self.cells

    @property
    def cols(self) -> int:
        """Symbol columns (a pair occupies two cells in a row)."""
        return self.cells // self.cps

    @property
    def n_cells(self) -> int:
        return self.cells * self.cells

    @property
    def n_symbols(self) -> int:
        return self.rows * self.cols

    # -- symbol-space regions ---------------------------------------------
    def ring_symbols_xy(self) -> list[tuple[int, int]]:
        r, c = self.rows, self.cols
        out = []
        for i in range(r):
            for j in range(c):
                if i < self.ring or j < self.ring or i >= r - self.ring or j >= c - self.ring:
                    out.append((i, j))
        return out

    # -- optional finder pattern ------------------------------------------
    def finder_map(self) -> dict[tuple[int, int], int]:
        """``{(row, col): 0|1}`` of the reserved finder symbols (1 = bright)."""
        from .protocol.finder import symbol_map

        return symbol_map(self.finder, self.finder_span, self.rows, self.cols,
                          self.ring, self.finder_size or None)

    def finder_symbols_xy(self) -> list[tuple[int, int]]:
        return sorted(self.finder_map())

    def finder_cells(self) -> list[tuple[int, int]]:
        out = []
        for (i, j) in self.finder_symbols_xy():
            out.extend(self.cells_of_symbol(i, j))
        return out

    def _with_finder_removed(self, xy: list[tuple[int, int]]) -> list[tuple[int, int]]:
        fnd = self.finder_map()
        if not fnd:
            return xy
        return [rc for rc in xy if rc not in fnd]

    def header_symbols_xy(self, header_rows: int | None = None) -> list[tuple[int, int]]:
        rows = self.header_rows if header_rows is None else int(header_rows)
        out = []
        for i in range(self.ring, min(self.ring + rows, self.rows - self.ring)):
            for j in range(self.ring, self.cols - self.ring):
                out.append((i, j))
        return self._with_finder_removed(out)

    def payload_symbols_xy(self) -> list[tuple[int, int]]:
        out = []
        for i in range(self.ring + self.header_rows, self.rows - self.ring):
            for j in range(self.ring, self.cols - self.ring):
                out.append((i, j))
        return self._with_finder_removed(out)

    def cells_of_symbol(self, i: int, j: int) -> list[tuple[int, int]]:
        if self.cps == 1:
            return [(i, j)]
        return [(i, j * self.cps), (i, j * self.cps + 1)]

    def symbol_cell_arrays(self):
        """Return ``(rows_idx, cols_idx)`` of the *first* cell of every symbol."""
        i = np.repeat(np.arange(self.rows), self.cols)
        j = np.tile(np.arange(self.cols), self.rows)
        return i, j * self.cps

    # -- calibration ring sequence ---------------------------------------
    def ring_values(self, mod: Modulation) -> dict[tuple[int, int], tuple[int, int]]:
        """Known ``(colour index, pattern index)`` for every ring symbol.

        The sequence walks the palette in a deterministic pseudo-random order so
        that a damaged edge still leaves every palette point represented, and it
        keeps the ring **solid** (pattern 0) so colour fitting never sees holes.
        Additionally the ring is laid out so that half of its symbols sit next to
        their own hue complement, which lets the receiver *verify* the
        differential model before trusting it.
        """
        cells = self.ring_symbols_xy()
        n = mod.n_colors
        step = 1
        while np.gcd(step, n) != 1:
            step += 1
        step = max(1, (n // 3) | 1)
        while np.gcd(step, n) != 1:
            step += 1
        return {rc: ((i * step) % n, 0) for i, rc in enumerate(cells)}

    def ring_cells(self) -> list[tuple[int, int]]:
        """Physical cells of the calibration ring (all cells of ring symbols)."""
        out = []
        for (i, j) in self.ring_symbols_xy():
            out.extend(self.cells_of_symbol(i, j))
        return out

    def header_cells(self) -> list[tuple[int, int]]:
        out = []
        for (i, j) in self.header_symbols_xy():
            out.extend(self.cells_of_symbol(i, j))
        return out

    def payload_cells(self) -> list[tuple[int, int]]:
        out = []
        for (i, j) in self.payload_symbols_xy():
            out.extend(self.cells_of_symbol(i, j))
        return out

    # -- capacity ---------------------------------------------------------
    def header_capacity(self, mod: Modulation) -> int:
        """bits available in the header block."""
        return len(self.header_symbols_xy()) * mod.bits

    def finder_cost(self) -> int:
        """Symbols the finder reserves (i.e. capacity it gives up)."""
        return len(self.finder_symbols_xy())

    def payload_capacity(self, mod: Modulation) -> int:
        return len(self.payload_symbols_xy()) * mod.bits

    def header_bytes(self, mod: Modulation) -> int:
        return self.header_capacity(mod) // 8

    def payload_bytes(self, mod: Modulation) -> int:
        return self.payload_capacity(mod) // 8


# Fixed size of the (pre-FEC) frame header, in bytes.
HEADER_DATA_LEN = 11


@dataclass
class ModemConfig:
    """Top level configuration object shared by TX / RX / training / GUI."""

    modulation: str = "rgb8"
    ecc: str = "rs16"
    layout: FrameLayout = field(default_factory=FrameLayout)
    cell_px: int = 10
    quiet_zone_cells: int = 2
    #: payload whitening family: 0 none, 1 PRNG (default), 2 alternating, 3 ramp
    mask_family: int = 1
    interleave: bool = True
    auto_detect_modulation: bool = True
    try_rotations: bool = True
    #: display target (None -> standalone square frame of ``frame_px``)
    display: DisplayProfile | None = None

    def __post_init__(self):
        # adapt the layout to the modulation's cells-per-symbol
        cps = self.mod().cells_per_symbol
        lay = self.layout
        if lay.cps != cps:
            lay = FrameLayout(cells=lay.cells, cps=cps, ring=lay.ring,
                              header_rows=lay.header_rows, finder=lay.finder,
                              finder_span=lay.finder_span,
                              finder_size=lay.finder_size)
        # validate the finder selection early: a typo in a config file should
        # fail at construction, not halfway through a transfer
        if lay.finder and str(lay.finder).lower() not in ("none", "", "off"):
            from .protocol.finder import get_shape

            get_shape(lay.finder)
            if lay.finder != str(lay.finder).lower():
                lay = dataclasses.replace(lay, finder=str(lay.finder).lower())
        self.layout = lay

    def mod(self) -> Modulation:
        return get_modulation(self.modulation)

    def ecc_profile(self) -> ECCProfile:
        return get_ecc(self.ecc)

    @property
    def frame_px(self) -> int:
        return (self.layout.cells + 2 * self.quiet_zone_cells) * self.cell_px

    def describe(self) -> dict:
        m, e = self.mod(), self.ecc_profile()
        try:
            from .protocol.frames import FrameCodec

            codec = FrameCodec(self)
            body_cap = codec.body_capacity
            user_payload = max(0, body_cap - 2)
        except Exception:  # pragma: no cover
            body_cap = max(0, self.layout.payload_bytes(m) - 16)
            user_payload = max(0, body_cap - 2)
        d = {
            "modulation": m.name,
            "bits_per_symbol": m.bits,
            "bits_per_cell": round(m.bits_per_cell, 2),
            "cells_per_symbol": m.cells_per_symbol,
            "colors": m.n_colors,
            "patterns": m.n_patterns,
            "pair": m.pair,
            "ecc": e.name,
            "cells": self.layout.cells,
            "symbol_grid": [self.layout.rows, self.layout.cols],
            "cell_px": self.cell_px,
            "frame_px": self.frame_px,
            "payload_bytes_on_air": self.layout.payload_bytes(m),
            "max_user_payload": user_payload,
            "max_user_payload_kb": round(user_payload / 1024.0, 1),
        }
        fnd = self.layout.finder_map()
        d["finder"] = self.layout.finder if fnd else None
        if fnd:
            from .protocol.finder import get_shape
            from .protocol.finder import auto_size

            shape = get_shape(self.layout.finder)
            d["finder"] = {
                "shape": shape.name,
                "span": self.layout.finder_span,
                "size": auto_size(shape, self.layout.rows, self.layout.cols)
                if not self.layout.finder_size else self.layout.finder_size,
                "symbols": len(fnd),
                "cells": len(self.layout.finder_cells()),
                "blocks": len(fnd) // max(1, (auto_size(shape, self.layout.rows,
                                                        self.layout.cols)
                                             if not self.layout.finder_size
                                             else self.layout.finder_size) ** 2),
            }
            d["finder"]["share"] = round(len(fnd) / max(1, self.layout.n_symbols), 4)
        else:
            d["finder"] = None
        if self.display is not None:
            d["display"] = self.display.to_dict()
        return d

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["layout"] = dataclasses.asdict(self.layout)
        d["display"] = None if self.display is None else self.display.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ModemConfig":
        d = dict(d)
        lay = d.pop("layout", {}) or {}
        if "grid" in lay and "cells" not in lay:
            lay["cells"] = lay.pop("grid")
        disp = d.pop("display", None)
        return cls(layout=FrameLayout(**lay),
                   display=DisplayProfile(**disp) if disp else None, **d)

    # ---- convenience constructors ---------------------------------------
    @classmethod
    def for_resolution(cls, resolution: str | tuple[int, int],
                       modulation: str = "rgb8", ecc: str = "rs16",
                       symbol_px: int = 12, margin_px: int = 32,
                       cells: int | None = None,
                       quiet_zone: int = 2) -> "ModemConfig":
        """Build a config sized for a real display, e.g. ``for_resolution("1920x1080")``."""
        if isinstance(resolution, tuple):
            disp = DisplayProfile(resolution[0], resolution[1], symbol_px, margin_px)
        else:
            disp = DisplayProfile.parse(resolution, symbol_px, margin_px)
        m = get_modulation(modulation)
        n_cells, px = disp.fit(cells, quiet_zone=quiet_zone,
                               min_px=m.min_px_per_cell)
        return cls(modulation=modulation, ecc=ecc, cell_px=px,
                   display=disp, quiet_zone_cells=quiet_zone,
                   layout=FrameLayout(cells=n_cells, cps=m.cells_per_symbol,
                                      ring=1, header_rows=4))

    def mask_byte(self, seq: int = 0) -> int:
        """Whitening mask byte for frame ``seq`` (family from ``mask_family``).

        Family 0 disables whitening (diagnostics); families 1-3 are the cheap
        masks in :mod:`optical_modem.protocol.mask`.  The 6-bit seed is derived
        from the sequence number so every frame of a transfer looks different,
        and it is carried in the header, so the receiver needs no side channel.
        """
        family = int(self.mask_family) & 0x3
        return ((family & 0x3) << 6) | (int(seq) & 0x3F)

    def fitted(self) -> "ModemConfig":
        """Return a config whose rendered code is guaranteed to fit the display.

        ``render_screen`` used to rescale an oversized code by a non-integer
        factor, which destroys the sub-cell pattern grid and quietly made every
        large ``cell_px`` fail.  Shrinking the symbol size to the largest integer
        that fits keeps the code crisp and keeps :func:`screen_geometry` exact.
        """
        if self.display is None:
            return self
        qz = max(1, self.quiet_zone_cells)
        total = self.layout.cells + 2 * qz
        px = int(self.display.usable // max(1, total))
        if px >= 1 and px == self.cell_px:
            return self
        return dataclasses.replace(self, cell_px=max(1, px))
