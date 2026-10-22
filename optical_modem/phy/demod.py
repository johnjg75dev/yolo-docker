"""Tile demodulation: colour calibration, pattern (image-hash) matching, LLRs.

Three ideas do the heavy lifting:

1. **Calibration ring.**  The outermost symbol border carries a known colour
   sequence, so a robust affine map ``observed -> palette`` can be fitted per
   frame.  Every camera, white balance and gamma is handled without tuning.
2. **Illumination normalisation.**  Dividing the frame by a blurred copy of
   itself cancels shadows and highlights; the ring decides whether that helped.
3. **Hue-differential pairs.**  In pair modes every symbol is drawn twice, in a
   colour and its hue+180 twin.  Classifying on ``A - B`` removes a *smooth*
   illumination field locally — no global model needed — and costs only the
   duplicated cells.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from ..config import Modulation


# --------------------------------------------------------------------------- #
# photometric helpers
# --------------------------------------------------------------------------- #
def illum_normalize(samples: np.ndarray, win: float = 7.0, floor: float = 8.0) -> np.ndarray:
    """Divide out smooth illumination (shadows, specular blobs, vignetting)."""
    s = np.asarray(samples, np.float32)
    num = cv2.GaussianBlur(s, (0, 0), sigmaX=max(0.5, win / 2.5),
                           sigmaY=max(0.5, win / 2.5), borderType=cv2.BORDER_REFLECT)
    return s / (num + floor)


@dataclass
class TileSymbols:
    """Demodulated symbol grid, ready for the frame codec."""

    values: np.ndarray        # (rows, cols) uint16
    llr: np.ndarray           # (rows, cols, bits) float32, >0 means bit == 0
    confidence: np.ndarray    # (rows, cols) float32
    colors: np.ndarray        # (rows, cols) uint8 chosen palette index
    patterns: np.ndarray      # (rows, cols) uint8 chosen pattern index
    ring_score: float = 0.0


class TileCalibrator:
    """Colour calibration + tile (pattern and colour) demodulation."""

    def __init__(self, mod: Modulation):
        self.mod = mod
        self.palette = np.asarray(mod.palette, np.float32)
        self.patterns = np.asarray(mod.pattern_mask, np.float32)  # (npat,P,P)
        self.npat, self.P = self.patterns.shape[0], self.patterns.shape[1]
        self.pat_flat = self.patterns.reshape(self.npat, -1)
        self.pat_norm2 = (self.pat_flat**2).sum(axis=1)
        self.pat_cent = self.pat_flat - self.pat_flat.mean(axis=1, keepdims=True)
        self.pat_cent2 = (self.pat_cent**2).sum(axis=1)
        self.W = np.zeros((4, 3), np.float32)
        self.W[:3] = np.eye(3)
        #: per-channel gain implied by the affine fit (used by pair decoding)
        self.gain = np.ones(3, np.float32)

    # ---- calibration -----------------------------------------------------
    def _feat(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, np.float32).reshape(-1, 3)
        return np.hstack([x, np.ones((len(x), 1), np.float32)])

    def fit(self, samples: np.ndarray, color_indices: np.ndarray,
            iters: int = 3, floor: float = 24.0, allow_normalized: bool = False):
        """Robust affine fit ``observed RGB -> palette RGB``; returns ring score."""
        X = np.asarray(samples, np.float32).reshape(-1, 3)
        y = self.palette[np.asarray(color_indices, int)]
        if len(X) < 6:
            return 0.0
        A = self._feat(X)
        W = self.W
        keep = np.ones(len(X), bool)
        for _ in range(iters):
            if keep.sum() < 6:
                break
            W, *_ = np.linalg.lstsq(A[keep], y[keep], rcond=None)
            pred = A @ W
            err = np.linalg.norm(pred - y, axis=1)
            thr = max(floor, float(np.quantile(err[keep], 0.85)))
            keep = err <= thr
        self.W = W.astype(np.float32)
        self.gain = np.diagonal(self.W[:3, :]).astype(np.float32).copy()
        pred = A @ self.W
        d = np.linalg.norm(pred[:, None, :] - self.palette[None], axis=2)
        return float((d.argmin(axis=1) == np.asarray(color_indices, int)).mean())

    def apply(self, samples: np.ndarray) -> np.ndarray:
        shape = samples.shape[:-1]
        A = self._feat(samples)
        return (A @ self.W).reshape(*shape, 3)

    # ---- tile demodulation ----------------------------------------------
    def demodulate_tiles(self, sub: np.ndarray, pair_map=None,
                         normalize: bool = False) -> TileSymbols:
        """Demodulate sampled tiles.

        ``sub`` is ``(rows, cols, K*P, K*P, 3)``: ``K*K`` samples per *cell*
        (``K >= 1``) which are averaged down to the pattern's ``P x P`` grid.
        ``pair_map`` (optional) marks which cells are the hue partners.
        """
        mod = self.mod
        R = sub.shape[2]
        K = max(1, R // self.P)
        P = self.P
        # ---- average samples down to P x P per cell ----------------------
        if R % P == 0 and K > 1:
            red = sub.reshape(sub.shape[0], sub.shape[1], P, K, P, K, 3).mean(axis=(3, 5))
        elif R % P == 0:
            red = sub
        else:
            red = np.stack([
                np.stack([cv2.resize(c, (P, P), interpolation=cv2.INTER_AREA)
                          for c in row], axis=0)
                for row in sub], axis=0)
        return self._decode_sub(red, pair_map, normalize)

    def demodulate_cells(self, cells: np.ndarray) -> TileSymbols:
        """Demodulate when only one RGB value per cell is available."""
        mod = self.mod
        rows, cols = cells.shape[:2]
        sub = np.repeat(np.repeat(cells[:, :, None, None, :], self.P, axis=2),
                        self.P, axis=3)
        del mod
        return self._decode_sub(sub, None, False)

    def _decode_sub(self, red: np.ndarray, pair_map,
                    normalize: bool = False) -> TileSymbols:
        mod = self.mod
        rows, cols = red.shape[:2]
        P = self.P
        flat = red.reshape(rows, cols, P * P, 3)

        # ---- 0. foreground/background levels ---------------------------
        # Every pattern keeps its centre sub-cells on, so the centre block of a
        # tile is always foreground: that gives the tile's own brightness after
        # whatever tone curve the camera applied.  The background level is
        # shared by the whole frame (it is DARK pushed through the same curve)
        # and is estimated from the darkest sub-cells.
        c0, c1 = P // 4, P - P // 4
        body0 = red[:, :, c0:c1, c0:c1, :].mean(axis=(2, 3))
        gray = flat.max(axis=-1)                        # (rows, cols, P*P)
        fg = np.maximum(body0.max(axis=-1), np.percentile(gray, 90, axis=-1))
        fg = fg[..., None]                              # (rows, cols, 1)
        # ``bg`` is the tile background (the holes).  A *median* over tiles is
        # wrong: most tiles are solid (zero padding, the ring) and would drag
        # the estimate up to the foreground level, which makes every solid tile
        # look like a pattern.  Take a low percentile of all sub-cell values
        # instead — the holes are exactly the dark mode of the distribution.
        bg = float(np.percentile(gray, 3.0))
        bg = float(np.clip(bg, 0.0, 0.7 * float(np.median(fg))))

        # ---- 1. pattern posterior (matched template, not correlation) --
        contrast = float(max(4.0, np.median(fg) - bg))
        pred = bg + (fg - bg)[..., None] * self.pat_flat[None, None]
        sse = ((gray[..., None, :] - pred) ** 2).sum(axis=-1)
        sigma = np.maximum(0.06 * (fg - bg), 3.0)            # per-tile noise
        like = -(sse - sse.min(axis=-1, keepdims=True)) / (
            2.0 * np.maximum(sigma, 1e-3) ** 2)
        p_pat = np.exp(np.maximum(like, -60.0))
        tot = p_pat.sum(axis=-1, keepdims=True)
        p_pat = np.where(tot > 0, p_pat / np.maximum(tot, 1e-12), 1.0 / self.npat)
        pat_idx = p_pat.argmax(axis=-1).astype(np.uint8)
        del contrast

        # ---- 2. body colour: the tile's centre block -------------------
        # Every pattern has its centre sub-cells forced on, so the middle of a
        # tile is *always* foreground.  Reading the colour there (instead of
        # from the whole mask) makes colour decoding immune to the pattern and
        # to a fraction of a cell of geometric error — the outer sub-cells are
        # the first thing neighbour bleed corrupts.
        c0, c1 = P // 4, P - P // 4
        core = red[:, :, c0:c1, c0:c1, :]
        body = core.mean(axis=(2, 3))
        if not np.all(np.isfinite(body)):  # pragma: no cover - defensive
            body = flat.mean(axis=2)
        if normalize:
            body = illum_normalize(body)

        body_cal = self.apply(body)

        # ---- 3. colour posterior (differential in pair modes) ----------
        if mod.pair and pair_map is not None:
            c_cost = self._pair_cost(body_cal, pair_map)
        else:
            c_cost = ((body_cal[..., None, :] - self.palette[None, None]) ** 2).sum(-1)
        c_cost = c_cost - c_cost.min(axis=-1, keepdims=True)
        # residual scale from the nearest palette point = local noise level
        sigma_c = 26.0
        p_col = np.exp(-c_cost / (2.0 * sigma_c))
        p_col /= p_col.sum(axis=-1, keepdims=True)
        color_idx = p_col.argmax(axis=-1).astype(np.uint8)

        # ---- 4. bits: pattern bits then colour bits --------------------
        bits = mod.bits
        llr = np.zeros((rows, cols, bits), np.float32)
        for b in range(mod.pattern_bits):
            p1 = p_pat[..., self._bit_mask(self.npat, b, mod.pattern_bits)].sum(-1)
            p0 = p_pat[..., ~self._bit_mask(self.npat, b, mod.pattern_bits)].sum(-1)
            llr[..., b] = np.log((p0 + 1e-9) / (p1 + 1e-9))
        for b in range(mod.color_bits):
            maskb = self._color_bit_mask(b)
            p1 = p_col[..., maskb].sum(-1)
            p0 = p_col[..., ~maskb].sum(-1)
            llr[..., mod.pattern_bits + b] = np.log((p0 + 1e-9) / (p1 + 1e-9))

        # ---- 5. symbol values -----------------------------------------
        values = np.zeros((rows, cols), np.uint16)
        for c in range(mod.n_colors):
            values[color_idx == c] = np.bitwise_or(
                values[color_idx == c],
                np.uint16(mod.labels[c]),
            )
        values |= (pat_idx.astype(np.uint16) << mod.color_bits)

        conf = np.sort(p_col, axis=-1)[..., -1] - (
            np.sort(p_col, axis=-1)[..., -2] if mod.n_colors > 1 else 0.0
        )
        conf = np.minimum(conf, np.sort(p_pat, axis=-1)[..., -1] * 2.0)
        return TileSymbols(values, llr, conf.astype(np.float32), color_idx,
                           pat_idx, 0.0)

    # ---- helpers ---------------------------------------------------------
    def _bit_mask(self, n: int, bit: int, nbits: int) -> np.ndarray:
        idx = np.arange(n)
        return ((idx >> (nbits - 1 - bit)) & 1).astype(bool)

    def _color_bit_mask(self, bit: int) -> np.ndarray:
        """Mask over palette indices whose *label* has ``bit`` set to 1."""
        nbits = self.mod.color_bits
        labels = np.array([int(l) for l in self.mod.labels])
        return ((labels >> (nbits - 1 - bit)) & 1).astype(bool)

    def _pair_cost(self, body_cal: np.ndarray, pair_map) -> np.ndarray:
        """Cost of every colour given a (colour, hue+180 complement) cell pair.

        ``pair_map`` is an ``(rows, cols)`` array holding +1 for the first cell
        of a pair and -1 for its complement.  The calibrated *difference* of the
        two cells is compared with the expected palette differences, which is
        immune to illumination that is smooth across a pair (shadow edges,
        vignetting, phone auto-exposure gradients) — that is the whole point of
        sending the hue-180 twin.  A weaker single-cell term is kept so that a
        very noisy pair can still be resolved by absolute colour.
        """
        mod = self.mod
        P = self.palette
        comp = np.asarray([mod.pair_partner(c) for c in range(mod.n_colors)])
        expected = P - P[comp]                              # (n_colors, 3)
        first = pair_map > 0
        second = pair_map < 0
        diff = np.zeros_like(body_cal)
        # pairs are horizontally adjacent: (i, 2j) and (i, 2j+1)
        diff[first] = body_cal[first] - np.roll(body_cal, -1, axis=1)[first]
        diff[second] = np.roll(body_cal, 1, axis=1)[second] - body_cal[second]

        c_cost = 0.30 * ((body_cal[..., None, :] - P[None, None]) ** 2).sum(-1)
        c_cost = c_cost + ((diff[..., None, :] - expected[None, None]) ** 2).sum(-1)
        return c_cost


class PaletteCalibrator(TileCalibrator):
    """Colour-only view of the calibrator, used by the fast ring-scoring path.

    Same affine fit, no pattern/tile logic: hypothesis scoring only needs to
    know whether the known ring colours decode.
    """
