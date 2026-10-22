"""Optional **finder patterns** for a coded frame.

A finder pattern is a small, high-contrast geometric mark drawn at known
positions inside the code (the four inner corners, three corners — QR style — or
the centre).  It gives the localiser something to lock onto that is *not* data:
a phone camera sees the same shape at every frame, in every modulation, whether
the payload is a 40-byte text or a 5 kB file.

Design rules (they are what make this safe rather than decorative):

* **Reserved, not painted over.**  :meth:`FrameLayout.finder_symbols_xy` removes
  the finder's symbols from the header *and* payload placement lists, so the
  finder never overwrites data and the capacity calculation shrinks by exactly
  the number of symbols it takes.  :class:`~optical_modem.protocol.frames.FrameCodec`
  asserts at construction that ring / header / payload / finder are pairwise
  disjoint — a layout bug raises instead of silently corrupting a frame.
* **Drawn from the palette.**  Two palette entries only — the darkest and the
  brightest the modulation offers — drawn *solid* (pattern 0) so blur and colour
  fitting cannot mistake them for data tiles.
* **Shape lives on the symbol grid**, so it survives the pair (hue-differential)
  modes, where one symbol spans two cells.

Shapes are defined as a boolean mask over an ``size x size`` symbol block,
``1`` = bright tile, ``0`` = dark tile.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np


@dataclass(frozen=True)
class FinderShape:
    """One entry of the finder-shape catalogue."""

    name: str
    mask: tuple[tuple[int, ...], ...]
    description: str
    #: how well the shape pins down a corner: used by :func:`score_block`
    symmetry: str = "corner"        # "corner" | "rotational" | "radial"

    @property
    def size(self) -> int:
        return len(self.mask)

    @property
    def array(self) -> np.ndarray:
        return np.asarray(self.mask, np.uint8)

    @property
    def n_light(self) -> int:
        return int(self.array.sum())

    @property
    def n_dark(self) -> int:
        return int(self.array.size - self.n_light)

    def as_dict(self) -> dict:
        return {"name": self.name, "size": self.size, "symmetry": self.symmetry,
                "description": self.description,
                "mask": [list(row) for row in self.mask]}


def _ring(n: int) -> tuple[tuple[int, ...], ...]:
    return tuple(
        tuple(1 if (i in (0, n - 1) or j in (0, n - 1)) else 0 for j in range(n))
        for i in range(n)
    )


def _bullseye(n: int) -> tuple[tuple[int, ...], ...]:
    c = (n - 1) / 2
    rows = []
    for i in range(n):
        row = []
        for j in range(n):
            d = max(abs(i - c), abs(j - c))
            # outer ring bright, then dark, then bright centre
            row.append(1 if int(round(d)) % 2 == 0 else 0)
        rows.append(tuple(row))
    return tuple(rows)


def _checker(n: int) -> tuple[tuple[int, ...], ...]:
    return tuple(tuple((i + j) % 2 for j in range(n)) for i in range(n))


def _diamond(n: int) -> tuple[tuple[int, ...], ...]:
    c = (n - 1) // 2
    return tuple(tuple(1 if abs(i - c) + abs(j - c) <= c else 0 for j in range(n))
                 for i in range(n))


def _cross(n: int) -> tuple[tuple[int, ...], ...]:
    c = n // 2
    return tuple(tuple(1 if (i == c or j == c) else 0 for j in range(n))
                 for i in range(n))


def _xmark(n: int) -> tuple[tuple[int, ...], ...]:
    return tuple(tuple(1 if (i == j or i + j == n - 1) else 0 for j in range(n))
                 for i in range(n))


def _bracket(n: int) -> tuple[tuple[int, ...], ...]:
    """Top + left edges — an L that points at the block's top-left corner."""
    return tuple(tuple(1 if (i == 0 or j == 0) else 0 for j in range(n))
                 for i in range(n))


def _triangle(n: int) -> tuple[tuple[int, ...], ...]:
    return tuple(tuple(1 if i >= j else 0 for j in range(n)) for i in range(n))


def _solid(n: int) -> tuple[tuple[int, ...], ...]:
    return tuple(tuple(1 for _ in range(n)) for _ in range(n))


def _dice(n: int) -> tuple[tuple[int, ...], ...]:
    """Bright border with a single bright centre dot — QR-ish and rotation-safe."""
    rows = [[1 if (i in (0, n - 1) or j in (0, n - 1)) else 0 for j in range(n)]
            for i in range(n)]
    c = n // 2
    rows[c][c] = 1
    return tuple(tuple(r) for r in rows)


#: The catalogue the UI lists.  Order = the order the dropdown shows.
SHAPES: dict[str, FinderShape] = {
    "ring": FinderShape("ring", _ring(5),
                        "hollow 5x5 square: rotation-safe, the default", "rotational"),
    "dice": FinderShape("dice", _dice(5),
                        "5x5 ring with a centre dot (QR-ish)", "rotational"),
    "bullseye": FinderShape("bullseye", _bullseye(5),
                            "concentric rings, scale-tolerant", "radial"),
    "checker": FinderShape("checker", _checker(4),
                           "4x4 checkerboard, densest", "corner"),
    "diamond": FinderShape("diamond", _diamond(5),
                           "rotated square", "rotational"),
    "cross": FinderShape("cross", _cross(5),
                         "plus sign", "rotational"),
    "x": FinderShape("x", _xmark(5),
                     "diagonal cross", "rotational"),
    "bracket": FinderShape("bracket", _bracket(5),
                           "L-bracket pointing at the top-left corner", "corner"),
    "triangle": FinderShape("triangle", _triangle(5),
                            "upper-left triangle", "corner"),
    "solid": FinderShape("solid", _solid(4),
                         "4x4 solid block (maximum contrast, minimum information)",
                         "radial"),
}

#: spans -> which of the reserved slots get a pattern
SPANS = {
    "corners": "all four inner corners",
    "three": "QR-style: top-left, top-right, bottom-left",
    "center": "one block in the middle of the code",
    "edges": "mid-points of the four edges",
}

#: ``"none"`` / ``""`` / ``None`` all mean "no finder"
NONE = "none"


def get_shape(name: str | None) -> FinderShape | None:
    """Look a shape up; ``None``/``"none"`` means no finder pattern."""
    if name is None:
        return None
    key = str(name).strip().lower()
    if key in ("", NONE, "off", "0", "false"):
        return None
    if key not in SHAPES:
        raise KeyError(f"unknown finder shape {name!r}; choose from {', '.join(SHAPES)}")
    return SHAPES[key]


def auto_size(shape: FinderShape | None, rows: int, cols: int) -> int:
    """Block size that keeps the finder under ~6 % of the code.

    A 5x5 block per corner costs 100 symbols; on a 28x28 code that is 13 % of the
    capacity, which is too much for an optional aid.  Below ~32 symbols per side
    the catalogue's 4x4/5x5 shapes are scaled down to a 3x3 crop.
    """
    if shape is None:
        return 0
    inner = min(rows, cols)
    return shape.size if inner >= 30 else max(3, shape.size - 2)


def mask_for(shape: FinderShape, size: int) -> np.ndarray:
    """The shape's mask resampled to ``size`` (nearest neighbour)."""
    m = shape.array
    if size == m.shape[0]:
        return m.copy()
    idx = (np.arange(size) * (m.shape[0] / size)).astype(int).clip(0, m.shape[0] - 1)
    return m[np.ix_(idx, idx)].astype(np.uint8)


def block_origins(span: str, size: int, rows: int, cols: int, ring: int
                  ) -> list[tuple[int, int]]:
    """Top-left *symbol* coordinates of every finder block for a span.

    Blocks are placed against the inner corners (inside the calibration ring) so
    they never touch the ring, and are clamped to stay inside the inner area.
    """
    span = (span or "corners").strip().lower()
    if span not in SPANS:
        raise KeyError(f"unknown finder span {span!r}; choose from {', '.join(SPANS)}")
    if size <= 0:
        return []
    r0, c0 = ring, ring
    r1, c1 = rows - ring - size, cols - ring - size
    if r1 < r0 or c1 < c0:                      # does not fit: no finder at all
        return []
    mid_r, mid_c = (rows - size) // 2, (cols - size) // 2
    if span == "corners":
        pts = [(r0, c0), (r0, c1), (r1, c1), (r1, c0)]
    elif span == "three":
        pts = [(r0, c0), (r0, c1), (r1, c0)]
    elif span == "center":
        pts = [(mid_r, mid_c)]
    else:                                        # edges
        pts = [(r0, mid_c), (mid_r, c1), (r1, mid_c), (mid_r, c0)]
    seen, out = set(), []
    for p in pts:                                # dedupe on tiny grids
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


@lru_cache(maxsize=64)
def _symbol_map_cached(shape_name: str, size: int, span: str, rows: int,
                       cols: int, ring: int) -> tuple[tuple[int, int, int], ...]:
    shape = get_shape(shape_name)
    if shape is None:
        return ()
    mask = mask_for(shape, size)
    cells: list[tuple[int, int, int]] = []
    for (r0, c0) in block_origins(span, size, rows, cols, ring):
        for i in range(size):
            for j in range(size):
                cells.append((r0 + i, c0 + j, int(mask[i, j])))
    return tuple(cells)


def symbol_map(shape_name: str | None, span: str, rows: int, cols: int, ring: int,
               size: int | None = None) -> dict[tuple[int, int], int]:
    """``{(row, col): 0|1}`` for every finder symbol of a layout.

    ``1`` = bright tile, ``0`` = dark tile.  Symbols outside the inner area are
    never produced, so a finder can not eat into the calibration ring.
    """
    shape = get_shape(shape_name)
    if shape is None:
        return {}
    size = auto_size(shape, rows, cols) if not size else int(size)
    entries = _symbol_map_cached(shape.name, size, span, rows, cols, ring)
    return {(i, j): v for (i, j, v) in entries}


def bright_dark_indices(palette) -> tuple[int, int]:
    """``(bright, dark)`` palette indices by luminance — the finder's two tones."""
    p = np.asarray(palette, np.float32)
    lum = 0.2126 * p[:, 0] + 0.7152 * p[:, 1] + 0.0722 * p[:, 2]
    return int(np.argmax(lum)), int(np.argmin(lum))


def score_block(sampled: np.ndarray, mask: np.ndarray, bright_level: float,
                dark_level: float) -> float:
    """How well a sampled block matches ``mask`` (0..1), scale-invariant.

    ``sampled`` is a ``size x size`` array of comparable brightness values (the
    caller decides: luma, or a distance-to-palette projection).  The block is
    normalised to its own min/max first, so exposure and gamma cancel out.
    """
    s = np.asarray(sampled, np.float32)
    m = np.asarray(mask, np.float32)
    if s.shape != m.shape or s.size == 0:
        return 0.0
    lo, hi = float(s.min()), float(s.max())
    if hi - lo < 1e-3:
        return 0.0
    norm = (s - lo) / (hi - lo)
    # nearest-template correlation: mean |norm - mask| over the block
    err = float(np.abs(norm - m).mean())
    return max(0.0, 1.0 - err * 2.0)


def score_grid(cell_colours: np.ndarray, layout, mod,
               size: int | None = None) -> float | None:
    """Finder match score (0..1) from a rectified ``(cells, cells, 3)`` grid.

    Returns ``None`` when the layout has no finder, so callers can report
    "not applicable" instead of a misleading 0.  The score is deliberately
    photometry-invariant (each block is normalised to its own min/max), which is
    what makes it usable as a *verification* signal next to ``ring_score``:
    the ring says "the colours are right", the finder says "this really is a
    frame, in this orientation, and here is where its corners are".
    """
    shape = get_shape(getattr(layout, "finder", None))
    if shape is None or cell_colours is None:
        return None
    rows, cols = layout.rows, layout.cols
    size = int(size or getattr(layout, "finder_size", 0)
               or auto_size(shape, rows, cols))
    mask = mask_for(shape, size)
    blocks = block_origins(layout.finder_span, size, rows, cols, layout.ring)
    if not blocks:
        return None
    grid = np.asarray(cell_colours, np.float32)
    if grid.ndim != 3 or grid.shape[0] < rows or grid.shape[1] < cols:
        return None
    # one sample per *symbol*: the first cell (in pair modes the second cell is
    # the hue complement and would score as "dark")
    lum = 0.2126 * grid[..., 0] + 0.7152 * grid[..., 1] + 0.0722 * grid[..., 2]
    cps = int(getattr(layout, "cps", 1) or 1)
    sym_lum = lum[:, ::cps]
    scores = []
    for (r0, c0) in blocks:
        block = sym_lum[r0:r0 + size, c0:c0 + size]
        if block.shape != (size, size):
            continue
        scores.append(score_block(block, mask, float(block.max()), float(block.min())))
    if not scores:
        return None
    return float(np.mean(scores))


def describe(shape_name: str | None, span: str | None = None) -> dict:
    """Small JSON-friendly description for ``ModemConfig.describe()``."""
    shape = get_shape(shape_name)
    if shape is None:
        return {"finder": None}
    return {"finder": shape.name, "finder_size": shape.size,
            "finder_span": (span or "corners").lstrip().lower(),
            "finder_symmetry": shape.symmetry}
