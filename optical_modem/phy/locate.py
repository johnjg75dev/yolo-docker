"""Frame localisation (classical CV) and cell sampling.

The detector's job is only to answer *"where is the coded grid?"* — four
corners in image coordinates.  Everything else (demodulation, ECC) is shared
with the YOLO26 detector, which predicts the same four corners.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Quad:
    """Four corners in image coordinates, ordered TL, TR, BR, BL of the *grid*."""

    pts: np.ndarray  # (4,2) float32
    score: float = 0.0
    source: str = "classical"

    def copy(self) -> "Quad":
        return Quad(self.pts.copy(), self.score, self.source)

    @property
    def center(self) -> np.ndarray:
        return self.pts.mean(axis=0)

    @property
    def side(self) -> float:
        d = [np.linalg.norm(self.pts[i] - self.pts[(i + 1) % 4]) for i in range(4)]
        return float(np.mean(d))

    def rotated(self, k: int) -> "Quad":
        return Quad(np.roll(self.pts, -k, axis=0), self.score, self.source)

    def tolist(self) -> list:
        return [[float(x), float(y)] for x, y in self.pts]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def order_quad(pts: np.ndarray) -> np.ndarray:
    """Order corners TL, TR, BR, BL."""
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.stack(
        [pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]]
    ).astype(np.float32)


def _quad_lerp(q: np.ndarray, uv: np.ndarray) -> np.ndarray:
    """Bilinear interpolation of normalised (u,v) coords inside quad ``q``."""
    q = np.asarray(q, np.float32)
    tl, tr, br, bl = q
    out = []
    for (u, v) in uv:
        top = tl * (1 - u) + tr * u
        bot = bl * (1 - u) + br * u
        out.append(top * (1 - v) + bot * v)
    return order_quad(np.stack(out))


def _rect_uv(a: float, b: float) -> np.ndarray:
    return np.array([[a, a], [b, a], [b, b], [a, b]], np.float32)


def quad_from_border(q: np.ndarray, grid: int, quiet_zone_cells: int) -> np.ndarray:
    """Detected dark-border quad  ->  coded-grid quad.

    The renderer draws a 1-cell dark rectangle in the innermost quiet-zone
    cell, i.e. cell ``qz-1`` of the ``T = grid + 2*qz`` frame cells.  A contour
    around it is the *outer* edge of that rectangle, spanning cells
    ``qz-1 .. qz+grid+1`` (``grid+2`` cells).  The coded grid is one cell in
    from either side, so ``u = 1/(grid+2) .. (grid+1)/(grid+2)``.
    """
    span = grid + 2
    return _quad_lerp(q, _rect_uv(1.0 / span, (grid + 1) / span))


def quad_from_outer(q: np.ndarray, grid: int, quiet_zone_cells: int) -> np.ndarray:
    """Detected *whole-frame* quad (outer edge of the quiet zone) -> grid quad."""
    T = grid + 2 * quiet_zone_cells
    return _quad_lerp(q, _rect_uv(quiet_zone_cells / T, (quiet_zone_cells + grid) / T))


# --------------------------------------------------------------------------- #
# sub-pixel quad refinement
# --------------------------------------------------------------------------- #
def _bilinear(img: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    x = np.clip(xs, 0, w - 1.001)
    y = np.clip(ys, 0, h - 1.001)
    x0 = np.floor(x).astype(np.int32)
    y0 = np.floor(y).astype(np.int32)
    fx, fy = x - x0, y - y0
    a = img[y0, x0]
    b = img[y0, np.minimum(x0 + 1, w - 1)]
    c = img[np.minimum(y0 + 1, h - 1), x0]
    d = img[np.minimum(y0 + 1, h - 1), np.minimum(x0 + 1, w - 1)]
    return (a * (1 - fx) * (1 - fy) + b * fx * (1 - fy)
            + c * (1 - fx) * fy + d * fx * fy)


def _fit_line(pts: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """Least-squares line fit with one round of outlier rejection."""
    if len(pts) < 8:
        return None
    for _ in range(2):
        c = pts.mean(axis=0)
        u, sv, vt = np.linalg.svd(pts - c, full_matrices=False)
        direction = vt[0]
        resid = np.abs((pts - c) @ np.array([-direction[1], direction[0]]))
        keep = resid <= max(0.6, 2.5 * float(np.median(resid)) + 1e-6)
        if keep.all():
            break
        pts = pts[keep]
        if len(pts) < 8:
            return None
    return c, direction


def _line_intersect(l1, l2) -> np.ndarray:
    (c1, d1), (c2, d2) = l1, l2
    A = np.array([[d1[0], -d2[0]], [d1[1], -d2[1]]])
    b = c2 - c1
    t = np.linalg.solve(A, b)
    return c1 + t[0] * d1


def _edge_quad(gray: np.ndarray, H: np.ndarray, cells: int, u_centre: float,
               half: float, n_side: int, n_scan: int) -> np.ndarray | None:
    """Fit the 4 straight brightness-drop edges around ``u = u_centre``."""
    cell_u = 1.0 / float(cells)
    us = np.linspace(u_centre - half * cell_u, u_centre + half * cell_u, n_scan)
    vs = np.linspace(0.06, 0.94, n_side)
    lines = []
    for side in range(4):
        raw = []
        # sides 0 (top) and 1 (left) sit at 0 and are searched outwards
        # (negative); sides 2 (bottom) and 3 (right) sit at 1 and are searched
        # the other way, so the same offsets are mirrored about the side
        coord = us if side in (0, 1) else 1.0 - us
        for v in vs:
            # sides 0/2 are the horizontal edges: scan along y, walk along x
            if side in (0, 2):
                uu, vv = np.full_like(coord, v), coord
            else:
                uu, vv = coord, np.full_like(coord, v)
            p = H @ np.stack([uu, vv, np.ones_like(uu)], axis=0)
            prof = _bilinear(gray, (p[0] / p[2])[None, :],
                             (p[1] / p[2])[None, :]).ravel()
            d = np.diff(prof)
            # either polarity: the ring's outer edge drops white -> dark, the
            # code's outer edge rises dark -> white
            ad = np.abs(d)
            k = int(np.argmax(ad))
            if ad[k] < max(4.0, 3.0 * float(np.median(ad))) or k == 0 or k == len(d) - 1:
                continue
            denom = d[k - 1] - 2 * d[k] + d[k + 1]
            frac = 0.0 if abs(denom) < 1e-6 else 0.5 * (d[k - 1] - d[k + 1]) / denom
            u_edge = coord[k] + float(np.clip(frac, -1.0, 1.0)) * (coord[1] - coord[0])
            raw.append((u_edge, v))
        if len(raw) < 12:
            return None
        # a handful of scans always latch onto a neighbouring edge or a tile
        # boundary, so take the median edge position and keep only the scans
        # that agree with it
        med = float(np.median([u for u, _ in raw]))
        tol = 0.35 / float(cells)      # u is in grid units
        pts = []
        for u_edge, v in raw:
            if abs(u_edge - med) > tol:
                continue
            pt = H @ (np.array([u_edge, v, 1.0]) if side in (0, 2)
                      else np.array([v, u_edge, 1.0]))
            pts.append(pt[:2] / pt[2])
        if len(pts) < 10:
            return None
        fit = _fit_line(np.asarray(pts, np.float32))
        if fit is None:
            return None
        lines.append(fit)
    corners = []
    for i in range(4):
        try:
            corners.append(_line_intersect(lines[i], lines[(i + 1) % 4]))
        except np.linalg.LinAlgError:
            return None
    out = order_quad(np.asarray(corners, np.float32))
    if not np.all(np.isfinite(out)):
        return None
    sides = np.array([np.linalg.norm(out[i] - out[(i + 1) % 4]) for i in range(4)])
    if sides.min() < 40:
        return None
    return out


def align_energy(img: np.ndarray, grid_quad: np.ndarray, cells: int,
                 K: int = 8) -> float:
    """How well ``grid_quad`` lines up with the cell lattice (higher = better).

    The code has a hard brightness/colour edge at *every* cell boundary, so a
    correctly placed quad puts maximum gradient energy on the boundaries and
    minimum in the cell centres.  Unlike a palette or ring-based score this
    needs no demodulation, is cheap, and is sharply peaked: it resolves a
    tenth of a cell, which is where the ring saturates.
    """
    n = int(cells) * K
    dst = np.float32([[0, 0], [n, 0], [n, n], [0, n]])
    M = cv2.getPerspectiveTransform(np.asarray(grid_quad, np.float32), dst)
    warped = cv2.warpPerspective(img, M, (n, n), flags=cv2.INTER_LINEAR)
    if warped.ndim == 3:
        warped = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    g = warped.astype(np.float32)
    gx = np.abs(np.diff(g, axis=1)).mean(axis=0)        # per column
    gy = np.abs(np.diff(g, axis=0)).mean(axis=1)        # per row
    # ``np.diff`` puts the step between samples ``j`` and ``j+1`` at index
    # ``j``, so the boundary between cells ``k-1`` and ``k`` is at ``k*K - 1``
    idx = np.arange(1, cells) * K - 1
    mid = idx + K // 2
    keep = (idx >= 0) & (mid < gx.shape[0] - 1)
    idx, mid = idx[keep], mid[keep]
    return float(np.concatenate([gx[idx], gy[idx]]).mean()
                 - np.concatenate([gx[mid], gy[mid]]).mean())


def refine_quad_energy(img: np.ndarray, grid_quad: np.ndarray, cells: int,
                       steps=(0.5, 0.25, 0.12, 0.06), passes: int = 4,
                       K: int = 8) -> np.ndarray:
    """Coordinate-descent fit of the four corners on :func:`align_energy`.

    Each of the eight corner coordinates is nudged by +-``step`` *cells* while
    the alignment energy improves; the step is then halved.  This is what turns
    a quad that is a third of a cell off (every colour mode then fails on the
    header) into one that is a tenth of a cell off (everything decodes).  Cost
    is ~40 warps of ``cells * K`` pixels, i.e. a few milliseconds.
    """
    q = np.asarray(grid_quad, np.float32).copy()
    side = float(np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[3] - q[0])) / 2.0
    cell_px = max(1e-3, side / max(1, cells))

    best = align_energy(img, q, cells, K)
    for step in steps:
        px = step * cell_px
        for _ in range(passes):
            improved = False
            for corner in range(4):
                for axis in (0, 1):
                    for sgn in (1.0, -1.0):
                        cand = q.copy()
                        cand[corner, axis] = q[corner, axis] + sgn * px
                        e = align_energy(img, cand, cells, K)
                        if e > best + 1e-6:
                            q, best, improved = cand, e, True
            if not improved:
                break
    return q


def refine_quad(img: np.ndarray, grid_quad: np.ndarray, cells: int,
                quiet_zone_cells: int = 2, n_side: int = 40,
                n_scan: int = 28) -> list[np.ndarray]:
    """Sub-pixel grid quads from the two straight edges around the code.

    Two edges are worth fitting, and both are far sharper than anything the
    palette can offer:

    * the **outer edge of the dark border ring**, one cell outside the grid
      (white quiet zone -> dark ring), and
    * the **outer edge of the whole code** (quiet zone -> screen background).

    Which one a contour detector actually found is unknown, so both are fitted
    and both interpretations returned; the calibration ring then decides.  The
    calibration ring alone cannot localise better than about a third of a cell
    — a 30%-mixed colour still rounds to the right palette entry — which is
    enough to smear a patterned tile.  A brightness step does not have that
    problem.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    gray = gray.astype(np.float32)
    q = np.asarray(grid_quad, np.float32).reshape(4, 2)
    unit = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32)
    H = cv2.getPerspectiveTransform(unit, q)          # grid coords -> image
    cell_u = 1.0 / float(cells)
    out: list[np.ndarray] = []
    # (a) dark ring outer edge at u = -1 cell -> grid = span of cells+2
    ring = _edge_quad(gray, H, cells, -cell_u, 0.55, n_side, n_scan)
    if ring is not None:
        out.append(quad_from_border(ring, cells, quiet_zone_cells))
    # (b) code outer edge at u = -quiet_zone cells -> grid = span of cells+2*qz
    code = _edge_quad(gray, H, cells, -max(1, quiet_zone_cells) * cell_u, 0.55,
                      n_side, n_scan)
    if code is not None:
        out.append(quad_from_outer(code, cells, quiet_zone_cells))
    return out


# --------------------------------------------------------------------------- #
# classical detection
# --------------------------------------------------------------------------- #
def _quad_quality(q: np.ndarray, img_shape) -> float:
    """Score a candidate: square-ish, convex, large, inside the image."""
    q = order_quad(q)
    sides = np.array([np.linalg.norm(q[i] - q[(i + 1) % 4]) for i in range(4)])
    if sides.min() < 12:
        return 0.0
    ratio = sides.max() / max(sides.min(), 1e-6)
    # internal angles close to 90 deg
    ang = []
    for i in range(4):
        a, b = q[(i - 1) % 4], q[i]
        c = q[(i + 1) % 4]
        v1, v2 = a - b, c - b
        cosang = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-6)
        ang.append(abs(np.degrees(np.arccos(np.clip(cosang, -1, 1))) - 90))
    if max(ang) > 40:
        return 0.0
    area = cv2.contourArea(q.astype(np.float32))
    img_area = img_shape[0] * img_shape[1]
    if area < 0.02 * img_area:
        return 0.0
    conv = cv2.isContourConvex(q.astype(np.int32))
    if not conv:
        return 0.0
    ang_pen = np.mean(ang) / 90.0
    return float(np.clip(area / img_area, 0, 1) * (1.0 - 0.35 * (ratio - 1)) * (1 - 0.4 * ang_pen))


def detect_candidates(img: np.ndarray, grid: int, quiet_zone_cells: int,
                      max_candidates: int = 3) -> list[Quad]:
    """Return up to ``max_candidates`` grid quads, best first."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    h, w = gray.shape
    cands: list[np.ndarray] = []

    # --- strategy A: dark border ring ------------------------------------
    thr = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 9
    )
    thr = cv2.morphologyEx(thr, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    cnts, _ = cv2.findContours(thr, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:12]:
        if cv2.contourArea(c) < 0.015 * h * w:
            continue
        peri = cv2.arcLength(c, True)
        for eps in (0.02, 0.04, 0.08):
            approx = cv2.approxPolyDP(c, eps * peri, True)
            if len(approx) == 4:
                cands.append(approx.reshape(4, 2).astype(np.float32))
                break
        else:
            rect = cv2.minAreaRect(c)
            cands.append(cv2.boxPoints(rect).astype(np.float32))

    # --- strategy B: bright quiet-zone plate ------------------------------
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    cnts, _ = cv2.findContours(otsu, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:6]:
        if cv2.contourArea(c) < 0.03 * h * w:
            continue
        rect = cv2.minAreaRect(c)
        (cx, cy), (rw, rh), ang = rect
        if rw < 1 or rh < 1 or not (0.75 < rw / rh < 1.35):
            continue
        cands.append(cv2.boxPoints(rect).astype(np.float32))

    # --- edge based fallback ---------------------------------------------
    edges = cv2.Canny(gray, 40, 120)
    cnts, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:6]:
        if cv2.contourArea(c) < 0.03 * h * w:
            continue
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.03 * peri, True)
        if len(approx) == 4:
            cands.append(approx.reshape(4, 2).astype(np.float32))

    scored = []
    for c in cands:
        sc = _quad_quality(c, gray.shape)
        if sc > 0:
            scored.append((sc, order_quad(c)))
    scored.sort(key=lambda t: -t[0])

    out, seen = [], []
    for sc, q in scored:
        if any(np.allclose(q, s, atol=8) for s in seen):
            continue
        seen.append(q)
        out.append(Quad(q, sc, "classical"))
        if len(out) >= max_candidates:
            break
    return out


# --------------------------------------------------------------------------- #
# sampling
# --------------------------------------------------------------------------- #
def warp_grid(img: np.ndarray, quad: np.ndarray, grid: int, supersample: int = 6,
              pad_cells: float = 0.0) -> np.ndarray:
    """Warp the grid region to ``grid*supersample`` pixels, one cell = S x S."""
    n = int(round((grid + 2 * pad_cells) * supersample))
    dst = np.array([[0, 0], [n, 0], [n, n], [0, n]], dtype=np.float32)
    # image quad -> warp rectangle (forward mapping, as cv2.warpPerspective expects)
    M = cv2.getPerspectiveTransform(np.asarray(quad, np.float32), dst)
    return cv2.warpPerspective(
        img, M, (n, n), flags=cv2.INTER_AREA, borderMode=cv2.BORDER_REPLICATE
    )


def sample_cells(img: np.ndarray, quad: np.ndarray, grid: int,
                 inset: float = 0.22, supersample: int = 6) -> np.ndarray:
    """Return ``(grid, grid, 3)`` median RGB per cell.

    Each cell is sampled from its central ``1-2*inset`` box, using a
    supersampled warp + median so a single specular pixel cannot flip a symbol.
    """
    S = supersample
    warped = warp_grid(img, quad, grid, S)
    inner = max(1, int(round(S * (1 - 2 * inset))))
    off = (S - inner) // 2
    cells = warped.reshape(grid, S, grid, S, -1).transpose(0, 2, 1, 3, 4)
    sub = cells[:, :, off : off + inner, off : off + inner, :]
    flat = sub.reshape(grid, grid, inner * inner, -1)
    return np.median(flat, axis=2).astype(np.float32)


def sample_cells_fast(img: np.ndarray, quad: np.ndarray, grid: int,
                      inset: float = 0.22, S: int = 4) -> np.ndarray:
    """Cheaper path used during dataset generation (box resize + mean)."""
    n = grid * S
    dst = np.array([[0, 0], [n, 0], [n, n], [0, n]], np.float32)
    M = cv2.getPerspectiveTransform(np.asarray(quad, np.float32), dst)
    warped = cv2.warpPerspective(img, M, (n, n), flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_REPLICATE)
    cells = warped.reshape(grid, S, grid, S, -1).transpose(0, 2, 1, 3, 4)
    inner = max(1, int(round(S * (1 - 2 * inset))))
    off = (S - inner) // 2
    sub = cells[:, :, off : off + inner, off : off + inner, :]
    return sub.reshape(grid, grid, -1).mean(axis=2).astype(np.float32)
