"""Geometric refinement of a grid quad.

Two ideas make this work:

* **Sharpness objective.**  If the sampling lattice is aligned with the coded
  cells, every cell is uniform; if it is off by even a fraction of a cell, the
  cell borders leak in and the within-cell spread explodes.  Mean within-cell
  standard deviation is therefore a *photometric-independent* alignment score
  (measured 1-8 when aligned vs 18-35 when off by 0.3 cell).
* **Cheap parametric search.**  The quad is perturbed by a zoom about the grid
  centre plus a translation, both expressed *in cell units*, and the resulting
  sample coordinates come straight from the grid->image homography.  All
  candidates share one vectorised remap, so a full coarse+fine search costs
  ~15 ms.
"""
from __future__ import annotations

import cv2
import numpy as np


def cell_centers(grid: int) -> tuple[np.ndarray, np.ndarray]:
    cs = np.arange(grid) + 0.5
    return np.meshgrid(cs, cs, indexing="ij")  # Y, X


def _coords(H: np.ndarray, X: np.ndarray, Y: np.ndarray, scale: float,
            dx: float, dy: float, offx: float = 0.0, offy: float = 0.0):
    g = X.shape[0]
    Xs = (X - g / 2.0) * scale + g / 2.0 + dx + offx
    Ys = (Y - g / 2.0) * scale + g / 2.0 + dy + offy
    pts = np.stack([Xs.ravel(), Ys.ravel(), np.ones(X.size)], 0)
    m = H @ pts
    return (m[0] / m[2]).reshape(g, g).astype(np.float32), \
           (m[1] / m[2]).reshape(g, g).astype(np.float32)


def sample_H(img: np.ndarray, H: np.ndarray, grid: int, scale: float = 1.0,
             dx: float = 0.0, dy: float = 0.0) -> np.ndarray:
    """Sample one RGB value per cell using homography ``H`` (grid -> image)."""
    Y, X = cell_centers(grid)
    mx, my = _coords(H, X, Y, scale, dx, dy)
    return cv2.remap(img, mx, my, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REPLICATE).astype(np.float32)


def sharpness(img: np.ndarray, H: np.ndarray, grid: int, scale: float = 1.0,
              dx: float = 0.0, dy: float = 0.0, span: float = 0.5) -> float:
    """Mean within-cell standard deviation (lower = better aligned)."""
    Y, X = cell_centers(grid)
    offs = (-span / 2, span / 2)
    acc = []
    for oy in offs:
        for ox in offs:
            mx, my = _coords(H, X, Y, scale, dx, dy, ox, oy)
            acc.append(cv2.remap(img, mx, my, cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_REPLICATE).astype(np.float32))
    stack = np.stack(acc, -1)  # (G,G,3,4) after moveaxis
    stack = np.moveaxis(stack, -1, 2)
    return float(stack.std(axis=2).mean())


def search_alignment(img: np.ndarray, H: np.ndarray, grid: int,
                     scales=(0.94, 0.97, 1.0, 1.03, 1.06),
                     coarse_range: float = 0.45, coarse_step: float = 0.15,
                     mid_range: float = 0.18, mid_step: float = 0.06,
                     fine_step: float = 0.02) -> tuple[tuple[float, float, float], float]:
    """Coarse-to-fine search for (zoom, dx, dy) minimising within-cell spread.

    The search deliberately stays inside **±0.5 cell**: shifting the sampling
    lattice by exactly one cell lands on neighbouring cells and is *equally*
    sharp, so the objective is periodic and any wider search is ill-posed.
    Coarse lattice errors (a detector that is a cell or two out) are handled
    upstream by the ring-scored hypotheses in :mod:`optical_modem.phy.classical`,
    which are not ambiguous.
    """
    best = (1.0, 0.0, 0.0)
    best_score = sharpness(img, H, grid)

    def scan(s_list, rng_x, step, base):
        nonlocal best, best_score
        s0, dx0, dy0 = base
        steps = np.arange(-rng_x, rng_x + 1e-9, step)
        for sc in [s0 * f for f in s_list]:
            for ddx in steps:
                for ddy in steps:
                    v = sharpness(img, H, grid, sc, dx0 + ddx, dy0 + ddy)
                    if v < best_score:
                        best_score, best = v, (sc, float(dx0 + ddx), float(dy0 + ddy))

    scan(scales, coarse_range, coarse_step, best)
    scan((0.97, 1.0, 1.03), mid_range, mid_step, best)
    scan((0.99, 1.0, 1.01), mid_step, fine_step, best)
    return best, best_score


def quad_from_alignment(quad: np.ndarray, grid: int, scale: float,
                        dx: float, dy: float) -> np.ndarray:
    """Apply a cell-space zoom+shift to a grid quad (returns new quad)."""
    q = np.asarray(quad, np.float32)
    src = np.array([[0, 0], [grid, 0], [grid, grid], [0, grid]], np.float32)
    dst = np.array(
        [[grid / 2 - grid / (2 * scale) + dx, grid / 2 - grid / (2 * scale) + dy],
         [grid / 2 + grid / (2 * scale) + dx, grid / 2 - grid / (2 * scale) + dy],
         [grid / 2 + grid / (2 * scale) + dx, grid / 2 + grid / (2 * scale) + dy],
         [grid / 2 - grid / (2 * scale) + dx, grid / 2 + grid / (2 * scale) + dy]],
        np.float32,
    )
    H = cv2.getPerspectiveTransform(src, q)
    hom = np.hstack([dst, np.ones((4, 1), np.float32)]) @ H.T
    return (hom[:, :2] / hom[:, 2:3]).astype(np.float32)
