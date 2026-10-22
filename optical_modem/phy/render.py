"""Rendering a coded frame to pixels (transmitter side).

A tile is drawn as ``pattern`` pixels of the symbol colour over a dark
background — the same idea as cimbar's "image hash" icons, except the pattern
*is* data here (``pattern_bits`` per tile) instead of being a fixed symbol set.

Rendering happens at sub-cell resolution and is then upscaled by an integer
factor, so a "symbol pixel size" (scale factor) of, say, 16 px stays crisp and
a 1920x1080 screen gets the whole code centred on a black canvas.
"""
from __future__ import annotations

import numpy as np

from ..config import DisplayProfile, ModemConfig, Modulation
from ..protocol.frames import FrameCodec, TileFrame, effective_layout

DARK = 16  # background inside a patterned tile


def palette_rgb(mod: Modulation) -> np.ndarray:
    return np.array(mod.palette, dtype=np.uint8)


def render_tile_frame(tf: TileFrame, cfg: ModemConfig,
                      include_border: bool = True) -> np.ndarray:
    """Render a :class:`TileFrame` to a BGR uint8 image of ``cfg.frame_px``."""
    mod = cfg.mod()
    layout = effective_layout(cfg)
    pal = palette_rgb(mod)
    cells = layout.cells
    P = int(mod.pattern_mask.shape[-1])       # 1 for colour-only modes
    cell_px = cfg.cell_px
    qz = max(1, cfg.quiet_zone_cells)

    cell_colors, cell_patterns = tf.to_cells(layout, mod)

    # ---- sub-cell canvas at P x P per cell -------------------------------
    body = pal[cell_colors]                              # (cells, cells, 3)
    masks = mod.pattern_mask[cell_patterns]              # (cells, cells, P, P)
    sub = np.where(masks[..., None].astype(bool),
                   body[:, :, None, None, :],
                   np.array([DARK, DARK, DARK], np.uint8))
    # (cell_r, cell_c, P, P, 3) -> (cell_r, P, cell_c, P, 3) -> image
    sub = sub.transpose(0, 2, 1, 3, 4).reshape(cells * P, cells * P, 3)

    # ---- upscale to the requested symbol pixel size ----------------------
    grid_px = cells * cell_px
    if sub.shape[0] != grid_px:
        import cv2

        sub = cv2.resize(sub, (grid_px, grid_px), interpolation=cv2.INTER_NEAREST)

    # ---- quiet zone + dark border ---------------------------------------
    T = cells + 2 * qz
    canvas = np.full((T * cell_px, T * cell_px, 3), 255, np.uint8)
    y0 = qz * cell_px
    canvas[y0 : y0 + grid_px, y0 : y0 + grid_px] = sub
    if include_border:
        t = cell_px
        a = (qz - 1) * cell_px
        hi = (qz + cells) * cell_px
        canvas[a : a + t, a : hi + t] = 16
        canvas[hi : hi + t, a : hi + t] = 16
        canvas[a : hi + t, a : a + t] = 16
        canvas[a : hi + t, hi : hi + t] = 16
    return canvas


def render_frame(cfg: ModemConfig, header, payload: bytes) -> np.ndarray:
    """Full TX path: header + payload -> tile frame -> image (square code)."""
    cfg = cfg.fitted()
    codec = FrameCodec(cfg)
    tf = codec.encode(header, payload)
    return render_tile_frame(tf, cfg)


def render_screen(cfg: ModemConfig, header, payload: bytes,
                  display: DisplayProfile | None = None) -> np.ndarray:
    """Render the code onto a full screen canvas (e.g. 1920x1080, black)."""
    disp = display or cfg.display
    code = render_frame(cfg, header, payload)
    if disp is None:
        return code
    W, H = disp.width, disp.height
    canvas = np.zeros((H, W, 3), np.uint8)
    h, w = code.shape[:2]
    if h > H or w > W:
        # Never rescale here: a fractional rescale destroys the sub-cell
        # pattern grid, and the geometry reported by ``screen_geometry`` would
        # no longer describe the drawn image.  ``cfg.fitted()`` prevents this.
        raise ValueError(
            f"code {w}x{h}px does not fit a {W}x{H} display; "
            f"use a config from ModemConfig.for_resolution()/cfg.fitted()"
        )
    y = (H - h) // 2
    x = (W - w) // 2
    canvas[y : y + h, x : x + w] = code
    return canvas


def make_frame_sequence(cfg: ModemConfig, frames, repeat: int = 1,
                        display: DisplayProfile | None = None):
    """Yield rendered images for a list of ``(header, payload)`` tuples."""
    for _ in range(repeat):
        for hdr, payload in frames:
            yield render_screen(cfg, hdr, payload, display)


def render_symbols(symbols: np.ndarray, cfg: ModemConfig) -> np.ndarray:
    """Compatibility helper: render a grid of *symbol values* directly."""
    mod = cfg.mod()
    layout = effective_layout(cfg)
    values = np.asarray(symbols, np.uint16)
    colors = np.zeros_like(values, np.uint8)
    patterns = np.zeros_like(values, np.uint8)
    for v in np.unique(values):
        c, p = mod.split_value(int(v))
        m = values == v
        colors[m] = c
        patterns[m] = p
    tf = TileFrame(layout.rows, layout.cols, values, colors, patterns)
    return render_tile_frame(tf, cfg)


def screen_geometry(cfg: ModemConfig, display: DisplayProfile | None = None) -> dict:
    """Where the code lands on the screen canvas, in pixels.

    Returns ``x0, y0, x1, y1`` (code square incl. quiet zone and border),
    ``grid`` (``x0, y0, x1, y1`` of just the coded cells) and ``code_px`` /
    ``cell_px``.  Used by the camera simulator, the GUI preview and by tests to
    know exactly which pixels carry data.
    """
    cfg = cfg.fitted()
    disp = display or cfg.display
    layout = effective_layout(cfg)
    code_px = (layout.cells + 2 * max(1, cfg.quiet_zone_cells)) * cfg.cell_px
    if disp is None:
        W = H = code_px
    else:
        W, H = disp.width, disp.height
    x0, y0 = (W - code_px) // 2, (H - code_px) // 2
    qz = max(1, cfg.quiet_zone_cells) * cfg.cell_px
    return {
        "x0": x0, "y0": y0, "x1": x0 + code_px, "y1": y0 + code_px,
        "grid": (x0 + qz, y0 + qz, x0 + qz + layout.cells * cfg.cell_px,
                 y0 + qz + layout.cells * cfg.cell_px),
        "code_px": code_px, "cell_px": cfg.cell_px, "cells": layout.cells,
        "width": W, "height": H,
    }
