"""Classical receiver: detect -> hypothesise -> refine -> demodulate -> FEC.

This is (a) the baseline the YOLO26 detector is compared against and (b) the
downstream half of the YOLO path: given a grid quad — from contours *or* from
the neural network — the same sampling / calibration / FEC code runs.

Robustness comes from scoring rather than from one clever detector: every
candidate contour yields two geometric hypotheses (it is the dark border ring,
or it is the whole bright plate), each is scored by how well the *known*
calibration ring around the grid decodes, and only the best few are pushed
through the expensive demodulation + FEC path.
"""
from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..config import MODULATIONS, ModemConfig, Modulation, get_modulation
from ..protocol.frames import FrameCodec, effective_layout
from ..protocol.packet import FrameHeader, check_crc
from . import locate
from .align import quad_from_alignment, sample_H, search_alignment, sharpness
from .demod import (PaletteCalibrator, TileCalibrator, TileSymbols,
                    illum_normalize)
from .locate import Quad


@dataclass
class FrameResult:
    ok: bool
    header: FrameHeader | None = None
    payload: bytes = b""
    modulation: str = ""
    ecc: str = ""
    ring_score: float = 0.0
    #: finder-pattern match (0..1); ``None`` when the frame has no finder
    finder_score: float | None = None
    quality: float = 0.0
    n_corrected: int = 0
    quad: np.ndarray | None = None
    rotation: int = 0
    detector: str = "classical"
    timings: dict = field(default_factory=dict)
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "header": str(self.header) if self.header else None,
            "payload_len": len(self.payload),
            "modulation": self.modulation,
            "ecc": self.ecc,
            "ring_score": round(self.ring_score, 3),
            "finder_score": (None if self.finder_score is None
                             else round(self.finder_score, 3)),
            "quality": round(self.quality, 3),
            "n_corrected": self.n_corrected,
            "rotation": self.rotation,
            "detector": self.detector,
            "quad": None if self.quad is None else np.asarray(self.quad).round(1).tolist(),
            "timings_ms": {k: round(v * 1000, 2) for k, v in self.timings.items()},
            "reason": self.reason,
        }


# --------------------------------------------------------------------------- #
# sampling helpers
# --------------------------------------------------------------------------- #
def cell_homography(quad: np.ndarray, grid: int) -> np.ndarray:
    """Homography mapping grid coordinates -> image coordinates."""
    src = np.array([[0, 0], [grid, 0], [grid, grid], [0, grid]], np.float32)
    return cv2.getPerspectiveTransform(src, np.asarray(quad, np.float32))


def _warp_quad(img, quad_img: np.ndarray, n: int, flags=cv2.INTER_AREA):
    dst = np.array([[0, 0], [n, 0], [n, n], [0, n]], np.float32)
    M = cv2.getPerspectiveTransform(np.asarray(quad_img, np.float32), dst)
    return cv2.warpPerspective(img, M, (n, n), flags=flags,
                               borderMode=cv2.BORDER_REPLICATE)


def sample_at(img, quad, grid, dx=0.0, dy=0.0, scale=1.0, supersample=5,
              inset=0.24) -> np.ndarray:
    """Sample ``grid x grid`` cells, allowing a sub-cell shift / zoom."""
    H = cell_homography(quad, grid)
    half = grid / 2.0
    a = half - half / scale
    b = half + half / scale
    pts = np.array([[a + dx, a + dy], [b + dx, a + dy],
                    [b + dx, b + dy], [a + dx, b + dy]], np.float32)
    hom = np.hstack([pts, np.ones((4, 1), np.float32)]) @ H.T
    sub = (hom[:, :2] / hom[:, 2:3]).astype(np.float32)

    n = grid * supersample
    warped = _warp_quad(img, sub, n)
    cells = warped.reshape(grid, supersample, grid, supersample, -1).transpose(0, 2, 1, 3, 4)
    inner = max(1, int(round(supersample * (1 - 2 * inset))))
    off = (supersample - inner) // 2
    box = cells[:, :, off : off + inner, off : off + inner, :]
    return np.median(box.reshape(grid, grid, inner * inner, -1), axis=2).astype(np.float32)


def sample_tiles(img, quad, cells: int, R: int, inset: float = 0.08) -> np.ndarray:
    """Sample every cell at ``R x R`` points -> ``(cells, cells, R, R, 3)``.

    ``R`` adapts to the modulation: solid-colour modes need only 4 samples per
    cell, patterned tiles need at least two samples per pattern sub-cell so the
    icon can actually be resolved.
    """
    n = cells * R
    warped = _warp_quad(img, np.asarray(quad, np.float32), n, flags=cv2.INTER_AREA)
    tiles = warped.reshape(cells, R, cells, R, -1).transpose(0, 2, 1, 3, 4)
    k = int(round(R * inset))
    if 2 * k < R - 1:
        tiles = tiles[:, :, k : R - k, k : R - k]
    return np.ascontiguousarray(tiles.astype(np.float32))


def sample_cells(img, quad, grid, supersample=3, inset=0.25) -> np.ndarray:
    """Fast cell sampling for hypothesis scoring (mean of a 3x3 warp)."""
    n = grid * supersample
    warped = _warp_quad(img, quad, n, flags=cv2.INTER_LINEAR)
    S = supersample
    cells = warped.reshape(grid, S, grid, S, -1).transpose(0, 2, 1, 3, 4)
    inner = max(1, int(round(S * (1 - 2 * inset))))
    off = (S - inner) // 2
    box = cells[:, :, off : off + inner, off : off + inner, :]
    return box.reshape(grid, grid, -1, warped.shape[-1]).mean(axis=2).astype(np.float32)


def mask_outside_quads(img: np.ndarray, quads: list[Quad], grid: int,
                       quiet_zone_cells: int, expand_cells: float = 2.5) -> np.ndarray:
    """Black out everything outside the given quads (plus a cell margin)."""
    mask = np.zeros(img.shape[:2], np.uint8)
    T = grid + 2 * quiet_zone_cells
    f = expand_cells / T
    for q in quads:
        pts = np.asarray(q.pts, np.float32)
        c = pts.mean(axis=0)
        outer = (pts - c) * (1 + 2 * f) + c
        cv2.fillConvexPoly(mask, outer.astype(np.int32), 255)
    out = np.zeros_like(img)
    out[mask > 0] = img[mask > 0]
    return out


def estimate_quality(llr: np.ndarray, mod: Modulation) -> float:
    """Map mean |LLR| onto a 0..1 channel-quality figure (drives adaptive ECC)."""
    m = float(np.mean(np.abs(llr)))
    return float(np.clip(m / (2.0 * mod.bits * 0.8), 0, 1))


# --------------------------------------------------------------------------- #
# canonical (rectified) sampling + ring-driven alignment
# --------------------------------------------------------------------------- #
def rectify(img: np.ndarray, quad: np.ndarray, cells: int, K: int = 24,
            margin: int = 2) -> tuple[np.ndarray, int, int]:
    """Warp the grid quad to a canonical square image of ``K`` px per cell.

    Sub-cell geometry (shift / zoom of a fraction of a cell) can then be
    searched by moving a sampling grid over this *one* warp instead of
    re-warping the frame for every candidate — that is what makes a
    ring-scored alignment search affordable.
    """
    c = np.asarray(quad, np.float32).mean(axis=0)
    f = (cells + 2 * margin) / float(cells)
    expanded = (np.asarray(quad, np.float32) - c) * f + c
    T = int(round((cells + 2 * margin) * K))
    return _warp_quad(img, expanded, T, flags=cv2.INTER_AREA), K, margin


def _box_sums(integ: np.ndarray, y0, y1, x0, x1) -> np.ndarray:
    """Mean colour of every box, from a summed-area table (``cv2.integral``)."""
    y0 = np.clip(y0, 0, integ.shape[0] - 1).astype(np.int32)
    y1 = np.clip(y1, 1, integ.shape[0] - 1).astype(np.int32)
    x0 = np.clip(x0, 0, integ.shape[1] - 1).astype(np.int32)
    x1 = np.clip(x1, 1, integ.shape[1] - 1).astype(np.int32)
    s = (integ[y1, x1] - integ[y0, x1] - integ[y1, x0] + integ[y0, x0])
    n = np.maximum((y1 - y0) * (x1 - x0), 1)[..., None]
    return (s / n).astype(np.float32)


def integral_of(canon: np.ndarray) -> np.ndarray:
    return cv2.integral(canon.astype(np.float32))


def _cell_index(cells: int, K: int, margin: int, shift: float, scale: float,
                inset: float, sub: int = 1):
    """Pixel edges of every (sub-)cell box in the canonical image."""
    step = K * scale
    base = (np.arange(cells, dtype=np.float32) + margin * K / step)[:, None]
    frac = (np.arange(sub, dtype=np.float32)[None, :]) / sub
    lo = np.round((base + shift + inset + frac * (1.0 - 2 * inset)) * step)
    span = (1.0 - 2 * inset) / sub * step
    hi = np.round(lo + max(1.0, span))
    if sub == 1:
        return lo[:, 0].astype(np.int32), hi[:, 0].astype(np.int32)
    return lo.astype(np.int32), hi.astype(np.int32)


def canon_cell_colours(integ: np.ndarray, cells: int, K: int, margin: int,
                       dx: float = 0.0, dy: float = 0.0, scale: float = 1.0,
                       inset: float = 0.20) -> np.ndarray:
    """Cell colours from the canonical sampled-area table, sub-cell shifted.

    ``dx`` moves the sampling grid down, ``dy`` to the right (in cells).
    """
    y0, y1 = _cell_index(cells, K, margin, dx, scale, inset)
    x0, x1 = _cell_index(cells, K, margin, dy, scale, inset)
    return _box_sums(integ, y0[:, None], y1[:, None], x0[None, :], x1[None, :])


def canon_tiles(integ: np.ndarray, cells: int, K: int, margin: int, P: int,
                m: int = 3, dx: float = 0.0, dy: float = 0.0, scale: float = 1.0,
                inset: float = 0.18, rows=None, cols=None) -> np.ndarray:
    """``(cells, cells, P*m, P*m, 3)`` tile samples from the canonical image.

    The samples are **sub-cell aligned**: each of the ``P x P`` pattern
    sub-cells gets its own ``m x m`` box, so reducing ``P*m -> P`` later is an
    exact box average of that sub-cell instead of a guess.  (A uniform grid
    with an inset drifts against the sub-cell edges and turns binary patterns
    into ramps — which is exactly what breaks pattern decoding at small cell
    pitches.)
    """
    step = K * scale
    ridx = (np.arange(cells) if rows is None else np.asarray(rows, np.int32))
    cidx = (np.arange(cells) if cols is None else np.asarray(cols, np.int32))
    base = (ridx.astype(np.float32) + margin * K / step)[:, None]
    base_c = (cidx.astype(np.float32) + margin * K / step)[:, None]
    t = (np.arange(m, dtype=np.float32) + inset)[None, :] / m
    t2 = (np.arange(m, dtype=np.float32) + 1.0 - inset)[None, :] / m
    sub = (np.arange(P, dtype=np.float32)[:, None] + t) / P           # (P, m)
    sub2 = (np.arange(P, dtype=np.float32)[:, None] + t2) / P
    sub = sub.reshape(1, P * m)
    sub2 = sub2.reshape(1, P * m)

    def axis(base_, shift):
        lo = np.round((base_ + shift + sub) * step).astype(np.int32)
        hi = np.round((base_ + shift + sub2) * step).astype(np.int32)
        return lo, np.maximum(hi, lo + 1)

    def axis_c(shift):
        return axis(base_c, shift)

    y0, y1 = axis(base, dx)
    x0, x1 = axis(base_c, dy)
    out = _box_sums(integ, y0[:, :, None, None], y1[:, :, None, None],
                    x0[None, None, :, :], x1[None, None, :, :])
    return np.ascontiguousarray(out.transpose(0, 2, 1, 3, 4))


# --------------------------------------------------------------------------- #
# per-quad decoding core (shared with the YOLO path)
# --------------------------------------------------------------------------- #
@dataclass
class Attempt:
    quad: Quad
    score: float
    rotation: int
    samples: np.ndarray          # (cells, cells, 3) cell-centre colours
    tiles: np.ndarray | None = None  # (cells, cells, R, R, 3) full tile samples


class QuadDecoder:
    """Turns (image, grid quad) into decoded frames."""

    def __init__(self, cfg: ModemConfig, refine: bool = True):
        self.cfg = cfg
        self.refine = refine
        self._img: np.ndarray | None = None
        self._pal: dict[str, np.ndarray] = {}
        #: how many sub-cell offsets to try before giving up on a quad
        self.pose_tries = 8
        #: wall-clock budget for the *fast* pass of one :meth:`decode` call
        #: (seconds); the deep pass is allowed a multiple of it
        self.time_budget = 2.5
        self.deadline = float("inf")

    # ---- config plumbing --------------------------------------------------
    def cfg_for(self, mod: Modulation) -> ModemConfig:
        """The receiver config with a different modulation (same geometry)."""
        if mod.name == self.cfg.modulation:
            return self.cfg
        return dataclasses.replace(self.cfg, modulation=mod.name)

    # ---- ring scoring ----------------------------------------------------
    def _palette(self, mod: Modulation) -> np.ndarray:
        """Palette as float32, cached per modulation (filled on demand)."""
        pal = self._pal.get(mod.name)
        if pal is None:
            pal = np.asarray(mod.palette, np.float32)
            self._pal[mod.name] = pal
        return pal

    def _ring_refs(self, mod: Modulation):
        """Calibration-ring reference cells and their known colours."""
        layout = effective_layout(self.cfg_for(mod))
        cells, colors = self._ring_expected_cells(mod, layout)
        rr = np.array([r for r, _ in cells])
        cc = np.array([c for _, c in cells])
        self._palette(mod)
        return layout, rr, cc, colors

    @staticmethod
    def _variants(samples: np.ndarray) -> dict[str, np.ndarray]:
        """Candidate photometric pre-processings.

        Division by a local mean kills shadows and highlights, but for
        *intensity* modulations the local mean is itself data dependent, so it
        can hurt.  We therefore keep both variants and let the calibration ring
        decide which one describes this frame.
        """
        return {"raw": samples, "norm": illum_normalize(samples)}

    def prepare(self, samples: np.ndarray, mod: Modulation, rr, cc, exp,
                rotations=(0, 1, 2, 3)):
        """Return ``(rotation, ring_score, samples)`` best over variants/rotations."""
        best = (0, -1.0, samples)
        for _name, variant in self._variants(np.asarray(samples, np.float32)).items():
            for k in rotations:
                ss = np.ascontiguousarray(np.rot90(variant, k=k, axes=(0, 1)))
                cal = PaletteCalibrator(mod)
                sc = cal.fit(ss[rr, cc], exp)
                if sc > best[1]:
                    best = (k, float(sc), ss)
        return best

    def _orient(self, samples: np.ndarray, mod: Modulation, layout, rr, cc, exp):
        """Pick the 90-degree rotation whose ring decodes best."""
        best = (0, -1.0)
        for k in range(4):
            ss = np.rot90(samples, k=k, axes=(0, 1))
            cal = PaletteCalibrator(mod)
            sc = cal.fit(ss[rr, cc], exp)
            if sc > best[1]:
                best = (k, float(sc))
        k = best[0]
        return k, best[1], np.ascontiguousarray(np.rot90(samples, k=k, axes=(0, 1)))

    def score_hypotheses(self, img: np.ndarray, quads: list[Quad],
                         mod: Modulation) -> list[Attempt]:
        layout, rr, cc, exp = self._ring_refs(mod)
        out: list[Attempt] = []
        for q in quads:
            H = cell_homography(q.pts, layout.grid)
            raw = sample_H(img, H, layout.grid)
            k, sc, ss = self.prepare(raw, mod, rr, cc, exp)
            out.append(Attempt(q, sc, k, ss))
        out.sort(key=lambda a: -a.score)
        return out

    def refine_attempt(self, img: np.ndarray, att: Attempt,
                       mod: Modulation) -> Attempt:
        """Sharpness-driven zoom/shift search + orientation re-check."""
        layout, rr, cc, exp = self._ring_refs(mod)
        grid = layout.grid
        H = cell_homography(att.quad.pts, grid)
        (scale, dx, dy), sharp = search_alignment(img, H, grid)
        if abs(scale - 1.0) < 1e-6 and abs(dx) < 1e-6 and abs(dy) < 1e-6:
            return att
        quad = quad_from_alignment(att.quad.pts, grid, scale, dx, dy)
        H2 = cell_homography(quad, grid)
        raw = sample_H(img, H2, grid)
        k, sc, ss = self.prepare(raw, mod, rr, cc, exp)
        if sc >= att.score - 0.02:
            return Attempt(Quad(quad, sc, att.quad.source), sc, k, ss)
        return att

    # ---- full decode for one attempt -------------------------------------
    def _ring_expected_cells(self, mod: Modulation, layout) -> tuple[list, np.ndarray]:
        """Known colour of every *cell* of the calibration ring.

        In pair modes the second cell of a ring symbol carries the hue+180
        complement, so it is just as useful for colour calibration.
        """
        ring = layout.ring_values(mod)
        cells: list[tuple[int, int]] = []
        colors: list[int] = []
        for (i, j), (color_idx, _pat) in ring.items():
            for k, (r, c) in enumerate(layout.cells_of_symbol(i, j)):
                cells.append((r, c))
                colors.append(color_idx if k == 0 else mod.pair_partner(color_idx))
        return cells, np.asarray(colors, int)

    def align_by_ring(self, integ, cells, K, margin, mod, rr, cc, rcol,
                      wide: float = 1.5,
                      ) -> tuple[int, float, float, float, float]:
        """Sub-cell alignment searched by *decoding the calibration ring*.

        Contour and network quads are accurate to roughly a fifth of a cell at
        best — and to a whole cell when the contour is the dark border rather
        than the quiet-zone plate.  Rather than trust a sharpness proxy, this
        walks a coarse-to-fine grid of ``(dy, dx)`` and keeps the one whose
        known ring colours decode best.

        The first stage is deliberately wide (**+-1.5 cells**): the mapping
        from "which contour did I find" to "where is the coded grid" is only
        known to about a cell, so the search has to be able to recentre.  A
        narrow (+-0.25 cell) search quietly fails every frame whose contour came
        from the wrong edge — which is most of them.

        ``wide`` is how far (in cells) the coarse stage may recentre.  Pass the
        full 1.5 for contours (``classical`` sources, which may have locked onto
        the border instead of the plate) and ~0.6 for ``refined`` quads: the
        ring is periodic with the cell, so a wide search on an already accurate
        quad can settle a whole cell away, which scores just as well on the ring
        and destroys the payload.

        Returns ``(rotation, ring_score, dx, dy, scale)``.
        """
        # 1. orientation: one sampling pass, four rotations
        # sample almost the whole cell here: the decoder integrates the full
        # tile (the pattern lives on the cell edges), so an alignment objective
        # that only looks at the cell centre will happily sit a fifth of a cell
        # off and still score 1.0
        ALIGN_INSET = 0.06
        body0 = canon_cell_colours(integ, cells, K, margin, inset=ALIGN_INSET)
        best_k, best_sc = 0, -1.0
        for k in range(4):
            b = body0 if k == 0 else np.rot90(body0, k, axes=(0, 1))
            cal = PaletteCalibrator(mod)
            sc = cal.fit(b[rr, cc], rcol, iters=2)
            if sc > best_sc:
                best_k, best_sc = k, float(sc)
        cal = PaletteCalibrator(mod)
        cal.fit((body0 if best_k == 0 else np.rot90(body0, best_k, axes=(0, 1)))[rr, cc],
                rcol, iters=2)

        pal = self._palette(mod)

        def accuracy(dx: float, dy: float, scale: float) -> float:
            body = canon_cell_colours(integ, cells, K, margin, dx, dy, scale,
                                      inset=ALIGN_INSET)
            if best_k:
                body = np.rot90(body, best_k, axes=(0, 1))
            pred = cal.apply(body[rr, cc])
            d = np.linalg.norm(pred[:, None, :] - pal[None], axis=2)
            acc = float((d.argmin(axis=1) == rcol).mean())
            # ties are common (the ring is periodic with the cell): prefer the
            # pose closest to the quad the detector actually handed us
            return acc - 1e-4 * (abs(dx) + abs(dy))

        best = (best_k, best_sc, 0.0, 0.0, 1.0)
        dx = dy = 0.0
        scale = 1.0
        sc_best = accuracy(0.0, 0.0, 1.0)
        # wide -> fine translation search, scale held fixed
        for span, n in ((max(0.1, wide), 7), (0.5, 5), (0.15, 5), (0.05, 3)):
            offs = np.linspace(-span, span, n)
            cx, cy = dx, dy
            for oy in offs:
                for ox in offs:
                    sc = accuracy(cx + ox, cy + oy, scale)
                    if sc > sc_best:
                        sc_best, dx, dy = sc, cx + ox, cy + oy
        # scale search around the best translation, then one more fine pass
        for s_cand in (0.96, 0.98, 0.99, 1.0, 1.01, 1.02, 1.04):
            sc = accuracy(dx, dy, s_cand)
            if sc > sc_best:
                sc_best, scale = sc, s_cand
        for span, n in ((0.05, 3), (0.02, 3)):
            offs = np.linspace(-span, span, n)
            cx, cy = dx, dy
            for oy in offs:
                for ox in offs:
                    sc = accuracy(cx + ox, cy + oy, scale)
                    if sc > sc_best:
                        sc_best, dx, dy = sc, cx + ox, cy + oy
        best = (best_k, float(sc_best), dx, dy, scale)
        return best

    def _ring_cost(self, integ, cells, K, margin, mod, rr, cc, rcol, cal,
                   dx: float, dy: float, scale: float) -> tuple[float, float]:
        """``(objective, ring accuracy)`` for one pose.

        Accuracy first (it is what the frame needs), mean residual to the
        expected palette point as the continuous tie-breaker — accuracy
        saturates at 1.0 while the residual keeps falling, and it is the
        residual that actually locates the sub-cell optimum.
        """
        pal = self._palette(mod)
        body = canon_cell_colours(integ, cells, K, margin, dx, dy, scale)
        ring = body[rr, cc]
        pred = cal.apply(ring)
        d = ((pred[:, None, :] - pal[None]) ** 2).sum(-1)
        acc = float((d.argmin(axis=1) == rcol).mean())
        idx = np.arange(len(rcol))
        denom = max(1.0, float(np.mean(((pal[:, None] - pal[None]) ** 2).sum(-1))))
        resid = float(d[idx, rcol].mean()) / denom
        return (1.0 - acc) * 1e3 + resid, acc

    def refine_pose(self, integ, cells, K, margin, mod, rr, cc, rcol, cal,
                    dx: float, dy: float, scale: float,
                    stages=((0.24, 5), (0.06, 5), (0.025, 3)),
                    ) -> tuple[float, float, float, float]:
        """Sub-cell ``(dx, dy)`` refinement by minimising the ring residual."""
        best = (self._ring_cost(integ, cells, K, margin, mod, rr, cc, rcol, cal,
                                dx, dy, scale)[0], dx, dy, scale)
        for span, n in stages:
            offs = np.linspace(-span, span, n)
            cx, cy = best[1], best[2]
            for oy in offs:
                for ox in offs:
                    c = self._ring_cost(integ, cells, K, margin, mod, rr, cc,
                                        rcol, cal, cx + ox, cy + oy, best[3])[0]
                    if c < best[0]:
                        best = (c, cx + ox, cy + oy, best[3])
        return float(best[1]), float(best[2]), float(best[3]), float(best[0])

    def refine_rotation(self, canon, cells, K, margin, mod, rr, cc, rcol, cal,
                        dx: float, dy: float, scale: float,
                        rot_stages=((1.2, 5), (0.4, 3)),
                        ) -> tuple[float, float, float, float, float]:
        """Search a residual **rotation** of the sampling grid.

        A contour (or network) quad is typically off by a few tenths of a
        degree, which at 60+ cells displaces the far corners by more than a
        cell.  ``dx/dy/scale`` cannot absorb that — the ring still reads well
        near the edges while the payload is smeared in the middle — so the
        canonical image is rotated by ``theta`` and the ring re-scored.

        Returns ``(dx, dy, scale, theta_degrees, objective)``.
        """
        h, w = canon.shape[:2]
        ctr = (w / 2.0, h / 2.0)

        def evaluate(theta: float, ox: float, oy: float, sc: float):
            if abs(theta) < 1e-9:
                integ = integral_of(canon)
            else:
                M = cv2.getRotationMatrix2D(ctr, float(theta), 1.0)
                rot = cv2.warpAffine(canon, M, (w, h), flags=cv2.INTER_LINEAR,
                                     borderMode=cv2.BORDER_REPLICATE)
                integ = integral_of(rot)
            obj, _ = self._ring_cost(integ, cells, K, margin, mod, rr, cc, rcol,
                                     cal, ox, oy, sc)
            return obj, integ

        # Hoist the rotation out of the offset loop. ``evaluate`` re-ran
        # warpAffine + cv2.integral for every (ox, oy) even though theta -- and
        # therefore the rotated image and its summed-area table -- is constant
        # across the whole inner loop. Measured on an 864x864x3 canonical image:
        # 2.69 ms (warp) + 4.50 ms (integral) per evaluate, 41 evaluations per
        # attempt of which only 9 do distinct work -> 295 ms down to ~65 ms.
        # The arithmetic below is unchanged, so the optimum is identical.
        def rotated_integral(theta: float):
            if abs(theta) < 1e-9:
                return integral_of(canon)
            M = cv2.getRotationMatrix2D(ctr, float(theta), 1.0)
            rot = cv2.warpAffine(canon, M, (w, h), flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_REPLICATE)
            return integral_of(rot)

        best_obj, _ = evaluate(0.0, dx, dy, scale)
        best = (dx, dy, scale, 0.0, best_obj)
        for span, n in rot_stages:
            base_theta = best[3]
            for theta in np.linspace(base_theta - span, base_theta + span, n):
                integ = rotated_integral(theta)
                for ox, oy in ((0.0, 0.0), (0.06, 0.0), (-0.06, 0.0),
                               (0.0, 0.06), (0.0, -0.06)):
                    obj, _ = self._ring_cost(integ, cells, K, margin, mod, rr, cc,
                                             rcol, cal, best[0] + ox, best[1] + oy,
                                             best[2])
                    if obj < best[4]:
                        best = (best[0] + ox, best[1] + oy, best[2], float(theta), obj)
        return float(best[0]), float(best[1]), float(best[2]), float(best[3]), float(best[4])

    # ---- demodulation ----------------------------------------------------
    def _demod(self, integ, cells, K, margin, mod, dx, dy, scale, k, cal,
               use_norm, pair_map, rows=None):
        """Demodulate (a row subset of) the aligned grid -> ``(values, llr)``."""
        P = int(mod.pattern_mask.shape[-1])
        tiles = canon_tiles(integ, cells, K, margin, P, m=2 if P > 1 else 8,
                            inset=0.25, dx=dx, dy=dy, scale=scale, rows=rows)
        if k:
            tiles = np.ascontiguousarray(np.rot90(tiles, k=k, axes=(0, 1)))
        sym = cal.demodulate_tiles(tiles, pair_map=pair_map, normalize=use_norm)
        values, llr = sym.values, sym.llr
        if mod.pair:
            values, llr = values[:, 0::2], llr[:, 0::2]
        return values, llr

    def _pose_offsets(self, span: float = 0.12):
        """Small translations tried when verifying a pose against the header."""
        offs = []
        for f in (1.0, 2.0):
            s_ = span * f
            offs += [(s_, 0.0), (-s_, 0.0), (0.0, s_), (0.0, -s_)]
        out = []
        for ox, oy in offs:
            if (round(ox, 3), round(oy, 3)) not in {(round(a, 3), round(b, 3))
                                                    for a, b in out}:
                out.append((ox, oy))
        return out

    def _demod_and_verify(self, integ, cells, K, margin, mod, layout, codec,
                          dx, dy, scale, k, cal, use_norm, pair_map):
        """Demodulate the whole grid at one pose and check the header.

        Returns ``(verified, values, llr)``.  Demodulating the whole grid (not
        just the header rows) keeps the frame-global background estimate the
        same as the one the final decode will use — a header-only demod gets a
        different noise floor and mis-classifies ~10% of the header symbols,
        which is far too many for RS(16).
        """
        values, llr = self._demod(integ, cells, K, margin, mod, dx, dy, scale,
                                  k, cal, use_norm, pair_map)
        try:
            hdr, ncorr, ok = codec.read_header(values, soft=np.abs(llr))
        except Exception:
            return False, values, llr
        if not ok or hdr.mod_id != mod.mod_id:
            return False, values, llr
        if not (0 < hdr.length <= codec.body_capacity + 2):
            return False, values, llr
        return True, values, llr

    def decode_attempt(self, att: Attempt, mod: Modulation,
                       deep: bool = False) -> FrameResult:
        t0 = time.perf_counter()
        cfg_mod = self.cfg_for(mod)
        codec = FrameCodec(cfg_mod)
        layout = codec.layout
        cells = layout.cells

        # ---- geometry: rectify once, then align by ring ------------------
        quad_pts = att.quad.pts
        if deep:
            # The ring/pose search cannot recover a quad that is a third of a
            # cell off (a 30% colour mix still rounds to the right palette
            # entry, but the header RS does not survive it).  Fit the four
            # corners directly on the cell-lattice gradient energy first: it
            # resolves a tenth of a cell, and then the pose search below is
            # only correcting what is left.
            quad_pts = locate.refine_quad_energy(
                self._img, np.asarray(quad_pts, np.float32), cells)
        canon, K, margin = rectify(self._img, quad_pts, cells, K=16, margin=2)
        integ = integral_of(canon)
        ring_cells, ring_colors = self._ring_expected_cells(mod, layout)
        rr = np.array([r for r, _ in ring_cells])
        cc = np.array([c for _, c in ring_cells])
        wide = 0.6 if getattr(att.quad, "source", "") == "refined" else 1.5
        k, ring_score, dx, dy, scale = self.align_by_ring(
            integ, cells, K, margin, mod, rr, cc, ring_colors, wide=wide)
        # ... then a continuous sub-cell refinement on the same ring,
        # including a residual-rotation search (accuracy plateaus long before
        # the pose is good; the ring residual does not).
        # Fit *both* photometric variants and keep both calibrators: the ring
        # score only says which one describes the border, and a shadow or a
        # specular highlight over the payload can make that the wrong one for
        # the interior (see the variant search below).
        base_body = canon_cell_colours(integ, cells, K, margin, dx, dy, scale)
        if k:
            base_body = np.rot90(base_body, k, axes=(0, 1))
        cals: dict = {}
        fits: dict = {}
        for mode in (False, True):
            cand = PaletteCalibrator(mod)
            variant = (illum_normalize(base_body) if mode is True else base_body)
            fits[mode] = float(cand.fit(variant[rr, cc], ring_colors))
            cals[mode] = cand
        # ---- optional finder pattern check ------------------------------
        # Cheap (a few dozen cells) and independent of the data: it tells the
        # caller whether the thing it just rectified really carries a finder in
        # the expected orientation.  Frames without one get ``None``.  Computed
        # here, before ``base_body`` is released, and never allowed to raise:
        # a diagnostic must not be able to fail a decode.
        try:
            from ..protocol.finder import score_grid

            finder_sc = score_grid(base_body, layout, mod)
        except Exception:                       # pragma: no cover - diagnostics only
            finder_sc = None
        del base_body
        use_norm = max(fits, key=lambda m: fits[m])
        best_fit = fits[use_norm]
        cal = cals[use_norm]

        pair_map = None
        if mod.pair:
            # physical cells, not symbols: demodulation runs on the cell grid
            pair_map = np.zeros((cells, cells), np.int8)
            pair_map[:, 0::2] = 1
            pair_map[:, 1::2] = -1

        # ---- decode at the ring pose (fast path) ------------------------
        def attempt(dx_, dy_, sc_, k_, norm_=None):
            use_n = use_norm if norm_ is None else norm_
            values, llr = self._demod(integ, cells, K, margin, mod, dx_, dy_, sc_,
                                      k_, cals[use_n], use_n, pair_map)
            q = estimate_quality(llr, mod)
            # The calibration ring is (almost) rotation invariant, so ``k_`` can
            # be 90/180/270 degrees off on a square frame.  Rotation of the
            # sampled grid is free, so try the remaining three quarter turns on
            # the *symbols* before declaring the header unreadable.
            rot_err = None
            for r in (k_, (k_ + 1) % 4, (k_ + 2) % 4, (k_ + 3) % 4):
                rot = (r - k_) % 4
                v = np.rot90(values, rot) if rot else values
                s = np.abs(llr)
                s = np.rot90(s, rot) if rot else s
                try:
                    hdr, body_bytes, ncorr, fec_ok = codec.decode_grid(v, soft=s)
                except Exception as exc:
                    rot_err = rot_err or type(exc).__name__
                    continue
                payload, crc_ok = check_crc(body_bytes)
                ok = bool(crc_ok and fec_ok)
                if ok:
                    return FrameResult(
                        ok=True, header=hdr, payload=payload,
                        modulation=mod.name, ecc=self.cfg.ecc,
                        ring_score=fits[use_n], finder_score=finder_sc,
                        quality=q, n_corrected=ncorr,
                        quad=att.quad.pts, rotation=r, reason=""), values
            return FrameResult(False, modulation=mod.name, ecc=self.cfg.ecc,
                               ring_score=best_fit, finder_score=finder_sc,
                               quality=q,
                               quad=att.quad.pts, rotation=k_,
                               reason=f"header:{rot_err or 'decode'}"), None
        # ---- refine the pose on the ring (skipped when out of budget) -----
        pose_ring = (float(dx), float(dy), float(scale))
        if time.perf_counter() < self.deadline:
            dx, dy, scale, theta, _ = self.refine_rotation(
                canon, cells, K, margin, mod, rr, cc, ring_colors, cal,
                dx, dy, scale)
            if abs(theta) > 1e-6:
                M = cv2.getRotationMatrix2D(
                    (canon.shape[1] / 2.0, canon.shape[0] / 2.0), theta, 1.0)
                canon = cv2.warpAffine(canon, M,
                                       (canon.shape[1], canon.shape[0]),
                                       flags=cv2.INTER_LINEAR,
                                       borderMode=cv2.BORDER_REPLICATE)
                integ = integral_of(canon)
            dx, dy, scale, _ = self.refine_pose(
                integ, cells, K, margin, mod, rr, cc, ring_colors, cal,
                dx, dy, scale)

        # The ring objective is not a reliable pose estimate on its own (it is
        # satisfied by a half-cell shift as soon as two neighbouring symbols
        # average into a third palette entry), so the ring pose from
        # ``align_by_ring`` is kept as a first-class candidate next to the
        # ring-residual refined pose.  A pose is accepted only if the frame
        # actually decodes — the header RS + CRC is the oracle.
        dx0, dy0, scale0 = pose_ring
        cands: list[tuple[float, float, float]] = [(dx, dy, scale),
                                                   (dx0, dy0, scale0)]
        if deep:
            for ox, oy in self._pose_offsets():
                cands.append((dx + ox, dy + oy, scale))
        # The illumination normalisation is a global switch chosen by the ring
        # score, but a shadow or a specular highlight covers only *part* of the
        # frame: the ring sits on the (unshaded) border and therefore votes for
        # the variant that is wrong for the payload in the middle.  Trying both
        # variants per pose costs one extra demodulation (a few ms) and lets the
        # header decide — the same oracle the pose search already uses.
        others = [m for m in (False, True) if m != use_norm]
        best = None
        for px, py, ps in cands:
            if best is not None and time.perf_counter() > self.deadline:
                break
            for norm_ in [None] + others:
                res, _ = attempt(px, py, ps, k, norm_)
                if res.ok:
                    res.timings = {"decode": time.perf_counter() - t0}
                    return res
                if best is None or (best.header is None and res.header is not None):
                    best = res
        res = best

        best.timings = {"decode": time.perf_counter() - t0}
        return best


# --------------------------------------------------------------------------- #
# receiver
# --------------------------------------------------------------------------- #
class ClassicalReceiver:
    def __init__(self, cfg: ModemConfig | None = None, refine: bool = True,
                 min_ring: float = 0.55):
        self.cfg = cfg or ModemConfig()
        self.refine = refine
        self.min_ring = min_ring
        self.decoder = QuadDecoder(self.cfg, refine)

    def _cell_px(self, att: Attempt) -> float:
        side = np.linalg.norm(att.quad.pts[0] - att.quad.pts[1])
        return max(1.0, side / float(effective_layout(self.cfg).cells))

    def _mods(self) -> list[Modulation]:
        names = [self.cfg.modulation]
        if self.cfg.auto_detect_modulation:
            names += [m for m in sorted(MODULATIONS, key=lambda x: -MODULATIONS[x].bits)
                      if m not in names]
        return [get_modulation(n) for n in names]

    def quads_from_image(self, img: np.ndarray,
                         seeds: list[Quad] | None = None,
                         expand_cells: float = 5.0) -> list[Quad]:
        """Contour hypotheses, optionally restricted to regions around ``seeds``.

        ``seeds`` usually come from YOLO26.  Each seed is **cropped** (not
        masked: masking destroys the global threshold statistics the contour
        detector relies on) so background clutter — a very common source of
        false contours — is simply not in the image.  What remains is the
        geometric question "is this contour the dark border ring or the bright
        plate?", which the calibration ring answers unambiguously.
        """
        layout = effective_layout(self.cfg)
        grid = layout.grid
        qz = self.cfg.quiet_zone_cells
        regions: list[tuple[int, int, np.ndarray]] = []
        if seeds:
            h, w = img.shape[:2]
            for q in seeds:
                pts = np.asarray(q.pts, np.float32)
                c = pts.mean(axis=0)
                outer = (pts - c) * (1 + 2 * expand_cells / (grid + 2 * qz)) + c
                x0 = int(max(0, np.floor(outer[:, 0].min()) - 4))
                y0 = int(max(0, np.floor(outer[:, 1].min()) - 4))
                x1 = int(min(w, np.ceil(outer[:, 0].max()) + 4))
                y1 = int(min(h, np.ceil(outer[:, 1].max()) + 4))
                if x1 - x0 < 32 or y1 - y0 < 32:
                    continue
                regions.append((x0, y0, img[y0:y1, x0:x1]))
        else:
            regions.append((0, 0, img))

        quads: list[Quad] = []
        for x0, y0, roi in regions:
            for n_cand, c in enumerate(locate.detect_candidates(roi, grid, qz)):
                if n_cand >= 2:
                    quads.append(Quad(locate.quad_from_outer(
                        c.pts + np.array([x0, y0], np.float32), grid, qz),
                        c.score, "classical"))
                    continue
                pts = c.pts + np.array([x0, y0], np.float32)
                seed = locate.quad_from_outer(pts, grid, qz)
                # sub-pixel refinement from the straight brightness edges: the
                # contours above are only good to a fifth of a cell, which is
                # already too coarse for a patterned tile
                for refined in locate.refine_quad(img, seed, grid, qz):
                    quads.append(Quad(refined, max(c.score, 0.5), "refined"))
                quads.append(Quad(locate.quad_from_border(pts, grid, qz),
                                  c.score, "classical"))
        return quads[:8]

    def decode(self, img: np.ndarray, quads: list[Quad] | None = None,
               seeds: list[Quad] | None = None,
               modulations: list[str] | None = None,
               max_attempts: int = 6) -> list[FrameResult]:
        t0 = time.perf_counter()
        self.decoder._img = img
        self.decoder.deadline = t0 + self.decoder.time_budget
        if quads is None:
            quads = self.quads_from_image(img, seeds=seeds)
        elif seeds:
            quads = list(quads) + self.quads_from_image(img, seeds=seeds)
        if not quads:
            return []
        quads = list(quads)[:6]
        mods = [get_modulation(m) for m in modulations] if modulations else self._mods()

        attempted_names: set[str] = {m.name for m in mods}
        # score every (quad, modulation) pair, then drop near-duplicates so the
        # attempt budget is spent on *distinct* geometries
        attempts: list[tuple[Attempt, Modulation]] = []
        for mod in mods:
            for att in self.decoder.score_hypotheses(img, quads, mod)[:4]:
                attempts.append((att, mod))
        # quasi-refined quads come from a straight-edge fit and are far more
        # accurate than contour quality suggests, so they are tried first
        configured = self.cfg.modulation
        # group per modulation, configured first, then interleave so a frame
        # sent with another modulation is still found inside the attempt budget
        by_mod: dict[str, list[tuple[Attempt, Modulation]]] = {}
        for att, mod in attempts:
            by_mod.setdefault(mod.name, []).append((att, mod))
        for lst in by_mod.values():
            lst.sort(key=lambda t: (0 if getattr(t[0].quad, "source", "") == "refined"
                                    else 1, -t[0].score))
        order = sorted(by_mod, key=lambda n: (0 if n == configured else 1,
                                              -by_mod[n][0][0].score))
        attempts = []
        for rank in range(max((len(v) for v in by_mod.values()), default=0)):
            for name in order:
                if rank < len(by_mod[name]):
                    attempts.append(by_mod[name][rank])
        deduped: list[tuple[Attempt, Modulation]] = []
        for att, mod in attempts:
            if any(np.abs(att.quad.pts - o.quad.pts).mean() < 0.25 * self._cell_px(att)
                   for o, _ in deduped):
                continue
            deduped.append((att, mod))
        attempts = deduped

        tried = 0
        by_id = {m.mod_id: m for m in MODULATIONS.values()}
        for att, mod in attempts:
            if tried >= max_attempts:
                break
            if att.score < self.min_ring:
                continue
            tried += 1
            if time.perf_counter() > self.decoder.deadline:
                break
            best = att
            res = self.decoder.decode_attempt(best, mod, deep=False)
            res.detector = "classical"
            # the frame header is self-describing: if it parsed and names a
            # different modulation, retry with that one (cheap, high value)
            if res.header is not None and res.header.mod_id != mod.mod_id:
                other = by_id.get(res.header.mod_id)
                if other is not None and other.name not in attempted_names:
                    attempted_names.add(other.name)
                    alt = self.decoder.decode_attempt(att, other)
                    alt.detector = "classical"
                    alt.timings["total"] = time.perf_counter() - t0
                    if alt.ok:
                        return [alt]
            res.timings["total"] = time.perf_counter() - t0
            if res.ok:
                return [res]
        # ---- second pass: same quads, but with the full pose search -------
        # the fast pass must not eat the whole budget: the deep pass is the one
        # that recovers a quad which is a third of a cell off
        self.decoder.deadline = max(self.decoder.deadline,
                                    t0 + 2.0 * self.decoder.time_budget)
        for att, mod in attempts[:max(1, min(2, max_attempts))]:
            if att.score < self.min_ring or time.perf_counter() > self.decoder.deadline:
                continue
            res = self.decoder.decode_attempt(att, mod, deep=True)
            res.detector = "classical"
            res.timings["total"] = time.perf_counter() - t0
            if res.ok:
                return [res]
        # nothing decoded: return the best effort for diagnostics
        if attempts:
            att, mod = attempts[0]
            res = self.decoder.decode_attempt(att, mod)
            res.timings["total"] = time.perf_counter() - t0
            return [res]
        return []

    def decode_with_quads(self, img: np.ndarray, quads: list[Quad],
                          modulations: list[str] | None = None,
                          max_attempts: int = 2) -> list[FrameResult]:
        return self.decode(img, quads=quads, modulations=modulations,
                           max_attempts=max_attempts)
