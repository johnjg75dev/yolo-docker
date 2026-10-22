"""Camera-channel simulator.

Used by (a) the training-set generator, (b) the benchmark harness and (c) the
unit tests.  It models the effects that actually break screen-to-camera links:

* perspective / scale / rotation (camera at an angle)
* screen gamma + ambient light (washed out blacks, clipped whites)
* white balance / per-channel colour gain (phone cameras)
* motion blur, defocus blur, sensor noise, JPEG artefacts
* vignetting (lens falloff)
* specular highlight / shadow patches
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class ChannelConfig:
    background: int = 40
    blur: float = 0.0
    noise_sigma: float = 0.0
    gamma: float = 1.0
    brightness: float = 1.0
    contrast: float = 1.0
    color_gain: tuple[float, float, float] = (1.0, 1.0, 1.0)
    vignette: float = 0.0
    jpeg_quality: int = 0  # 0 = off
    highlight: bool = False
    shadow: bool = False
    perspective: float = 0.0  # max corner jitter as a fraction of the side
    rotation: float = 0.0
    scale_min: float = 0.55
    scale_max: float = 0.95
    out_size: int = 640

    @staticmethod
    def random(rng: np.random.Generator, level: float = 1.0) -> "ChannelConfig":
        """Sample a plausible random capture.  ``level`` scales severity."""
        L = float(level)
        return ChannelConfig(
            background=int(rng.integers(12, 110)),
            blur=float(rng.uniform(0, 2.2 * L)),
            noise_sigma=float(rng.uniform(0, 9 * L)),
            gamma=float(rng.uniform(0.6, 1.5)),
            brightness=float(rng.uniform(0.62, 1.12)),
            contrast=float(rng.uniform(0.65, 1.05)),
            color_gain=(
                float(rng.uniform(0.85, 1.15)),
                float(rng.uniform(0.9, 1.1)),
                float(rng.uniform(0.85, 1.15)),
            ),
            vignette=float(rng.uniform(0, 0.55 * L)),
            jpeg_quality=int(rng.integers(35, 95)) if rng.random() < 0.5 * L else 0,
            highlight=bool(rng.random() < 0.25 * L),
            shadow=bool(rng.random() < 0.35 * L),
            perspective=float(rng.uniform(0.0, 0.16 * L)),
            rotation=float(rng.uniform(-28, 28) * L),
            scale_min=float(rng.uniform(0.45, 0.7)),
            scale_max=float(rng.uniform(0.82, 0.98)),
            out_size=int(rng.choice([480, 540, 640, 720])),
        )

    @staticmethod
    def handheld(rng: np.random.Generator, level: float = 1.0) -> "ChannelConfig":
        """A *normal* handheld capture: the user frames the code, so it fills the
        camera frame.  ``random()`` includes code-squeezed-into-a-corner
        captures that no sensible person would photograph; keep those for the
        stress sweep and use this profile for the "does it work" numbers.
        """
        L = float(level)
        return ChannelConfig(
            background=int(rng.integers(20, 90)),
            blur=float(rng.uniform(0, 1.4 * L)),
            noise_sigma=float(rng.uniform(0, 6 * L)),
            gamma=float(rng.uniform(0.7, 1.4)),
            brightness=float(rng.uniform(0.72, 1.10)),
            contrast=float(rng.uniform(0.8, 1.05)),
            color_gain=(float(rng.uniform(0.9, 1.1)),
                        float(rng.uniform(0.93, 1.07)),
                        float(rng.uniform(0.9, 1.1))),
            vignette=float(rng.uniform(0, 0.35 * L)),
            jpeg_quality=int(rng.integers(55, 95)) if rng.random() < 0.4 * L else 0,
            highlight=bool(rng.random() < 0.15 * L),
            shadow=bool(rng.random() < 0.2 * L),
            perspective=float(rng.uniform(0.0, 0.09 * L)),
            rotation=float(rng.uniform(-18, 18) * L),
            scale_min=float(rng.uniform(0.72, 0.86)),
            scale_max=float(rng.uniform(0.9, 0.99)),
            out_size=1080,
        )


# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #
def _order_quad(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as TL, TR, BR, BL (clockwise)."""
    pts = np.asarray(pts, dtype=np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmin(d)]
    bl = pts[np.argmax(d)]
    return np.stack([tl, tr, br, bl]).astype(np.float32)


def sample_quad(rng: np.random.Generator, out_size: int, scale: float,
                perspective: float, rotation: float, center=None,
                aspect: float = 1.0) -> np.ndarray:
    """Return a 4x2 corner quad (TL,TR,BR,BL) for a placed frame in the image.

    ``aspect`` is the source's width / height, and it **must** be honoured: a
    1280x720 screen mapped onto a square quad is squeezed 2.4:1, which no
    receiver can undo (cells stop being square and the geometry checks fail).
    The long side of the placed frame is ``out_size * scale``.
    """
    side = out_size * scale
    cx, cy = (out_size / 2, out_size / 2) if center is None else center
    aspect = float(aspect) or 1.0
    half_w = side / 2 if aspect >= 1.0 else side * aspect / 2
    half_h = side / 2 if aspect <= 1.0 else side / (2 * aspect)
    base = np.array(
        [[-half_w, -half_h], [half_w, -half_h], [half_w, half_h], [-half_w, half_h]],
        dtype=np.float32,
    )
    th = np.deg2rad(rotation)
    R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]], dtype=np.float32)
    pts = base @ R.T
    jitter = rng.normal(0, perspective * side, size=(4, 2)).astype(np.float32)
    pts = pts + jitter + np.array([cx, cy], dtype=np.float32)
    return _order_quad(pts)


def warp_frame(frame: np.ndarray, quad: np.ndarray, out_size, 
               background: int) -> np.ndarray:
    """Perspective-warp ``frame`` so that its full extent lands on ``quad``."""
    ow, oh = (int(out_size[0]), int(out_size[1])) if isinstance(out_size, (tuple, list)) \
        else (int(out_size), int(out_size))
    h, w = frame.shape[:2]
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)
    H = cv2.getPerspectiveTransform(src, quad.astype(np.float32))
    out = np.full((oh, ow, 3), background, dtype=np.uint8)
    warped = cv2.warpPerspective(
        frame, H, (ow, oh), flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT, borderValue=(background,) * 3,
    )
    mask = cv2.warpPerspective(
        np.full((h, w), 255, np.uint8), H, (ow, oh),
        flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    )
    out[mask > 0] = warped[mask > 0]
    return out


# --------------------------------------------------------------------------- #
# photometry
# --------------------------------------------------------------------------- #
def _vignette(img: np.ndarray, strength: float) -> np.ndarray:
    if strength <= 0:
        return img
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cy, cx = h / 2.0, w / 2.0
    r = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2) / np.sqrt(cy**2 + cx**2)
    mask = 1.0 - strength * r**2
    return np.clip(img.astype(np.float32) * mask[..., None], 0, 255).astype(np.uint8)


def _blob(img: np.ndarray, rng: np.random.Generator, bright: bool) -> np.ndarray:
    h, w = img.shape[:2]
    cx, cy = rng.integers(0, w), rng.integers(0, h)
    ax, ay = rng.integers(int(w * 0.12), int(w * 0.42)), rng.integers(
        int(h * 0.12), int(h * 0.42)
    )
    mask = np.zeros((h, w), np.float32)
    cv2.ellipse(mask, (int(cx), int(cy)), (int(ax), int(ay)),
                float(rng.uniform(0, 360)), 0, 360, 1.0, -1)
    mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=max(3, ax / 3))
    mask = np.clip(mask / (mask.max() + 1e-6), 0, 1)
    f = img.astype(np.float32)
    if bright:
        f = f + mask[..., None] * float(rng.uniform(60, 150))
    else:
        f = f * (1.0 - 0.75 * mask[..., None])
    return np.clip(f, 0, 255).astype(np.uint8)


def crop_square(frame: np.ndarray, rect, background: int = 40,
                pad: float = 0.0) -> tuple[np.ndarray, tuple[int, int, int]]:
    """Cut a square region around ``rect`` (padding outside with background).

    Real captures frame the *code*, not the whole desktop, so the simulator has
    to be able to look at a region of a 16:9 screen and give it the camera's
    full resolution — otherwise an 84-cell code on a 1920x1080 screen would be
    squeezed into a third of the frame and the experiment would be meaningless.
    """
    x0, y0, x1, y1 = [float(v) for v in rect]
    side = max(x1 - x0, y1 - y0) * (1.0 + pad)
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    X0, Y0 = int(round(cx - side / 2)), int(round(cy - side / 2))
    S = int(round(side))
    out = np.full((S, S, 3), background, np.uint8)
    H, W = frame.shape[:2]
    sx0, sy0 = max(0, X0), max(0, Y0)
    sx1, sy1 = min(W, X0 + S), min(H, Y0 + S)
    if sx1 > sx0 and sy1 > sy0:
        out[sy0 - Y0 : sy1 - Y0, sx0 - X0 : sx1 - X0] = frame[sy0:sy1, sx0:sx1]
    return out, (X0, Y0, S)


def simulate(frame: np.ndarray, cfg: ChannelConfig,
             rng: np.random.Generator | None = None,
             roi=None, pad: float = 0.0,
             ) -> tuple[np.ndarray, np.ndarray]:
    """Apply the channel.  Returns ``(image, quad_of_frame_outline)``.

    ``quad`` are the 4 corners of the *rendered image* (frame incl. quiet
    zone) in the output image, ordered TL,TR,BR,BL — used as ground truth.
    ``roi`` optionally restricts the simulation to a rectangle of ``frame``
    (see :func:`crop_square`); the returned quad then refers to that crop.
    """
    rng = np.random.default_rng() if rng is None else rng
    osz = cfg.out_size
    if isinstance(osz, (tuple, list)):
        ow, oh = int(osz[0]), int(osz[1])
    else:
        ow = oh = int(osz)
    if roi is not None:
        frame, _crop = crop_square(frame, roi, cfg.background, pad)
    scale = float(rng.uniform(cfg.scale_min, cfg.scale_max))
    fh, fw = frame.shape[:2]
    quad = sample_quad(rng, min(ow, oh), scale, cfg.perspective, cfg.rotation,
                       center=(ow / 2.0, oh / 2.0), aspect=fw / fh)
    img = warp_frame(frame, quad, (ow, oh), cfg.background)
    return _photometry(img, cfg, rng), quad


def _photometry(img: np.ndarray, cfg: ChannelConfig,
                rng: np.random.Generator) -> np.ndarray:
    """Everything the sensor + display pipeline does after geometry."""
    if cfg.highlight:
        img = _blob(img, rng, bright=True)
    if cfg.shadow:
        img = _blob(img, rng, bright=False)

    f = img.astype(np.float32)
    f *= cfg.brightness
    f = (f - 128.0) * cfg.contrast + 128.0
    f = np.clip(f, 0, 255) / 255.0
    f = np.power(f, cfg.gamma) * 255.0
    f = np.clip(f, 0, 255)
    img = f.astype(np.uint8)

    for c in range(3):
        img[..., c] = np.clip(img[..., c].astype(np.float32) * cfg.color_gain[c], 0, 255)

    img = _vignette(img, cfg.vignette)

    if cfg.blur > 0.15:
        img = cv2.GaussianBlur(img, (0, 0), sigmaX=cfg.blur, sigmaY=cfg.blur)

    if cfg.noise_sigma > 0.1:
        noise = rng.normal(0, cfg.noise_sigma, img.shape)
        img = np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)

    if cfg.jpeg_quality:
        ok, buf = cv2.imencode(
            ".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), int(cfg.jpeg_quality)]
        )
        if ok:
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return img


def grid_quad_from_frame_quad(
    quad: np.ndarray,
    frame_px: int,
    cell_px: int,
    quiet_zone_cells: int,
    grid: int,
    inset_cells: float = 0.0,
) -> np.ndarray:
    """Map the coded-grid corners through the same homography as the frame.

    ``quad`` are the corners of the rendered image (cell coords ``0..T``).
    Returns the 4 corners of the coded grid in image coordinates, ordered
    TL,TR,BR,BL in the *transmitter's* orientation.
    """
    T = grid + 2 * quiet_zone_cells
    src = np.array(
        [[0, 0], [frame_px, 0], [frame_px, frame_px], [0, frame_px]], dtype=np.float32
    )
    H = cv2.getPerspectiveTransform(src, np.asarray(quad, dtype=np.float32))
    a = quiet_zone_cells + inset_cells
    b = quiet_zone_cells + grid - inset_cells
    pts_cells = np.array([[a, a], [b, a], [b, b], [a, b]], dtype=np.float32)
    pts_px = pts_cells * cell_px
    ones = np.ones((4, 1), np.float32)
    hom = np.hstack([pts_px, ones])
    mapped = hom @ H.T
    mapped = mapped[:, :2] / mapped[:, 2:3]
    return mapped.astype(np.float32)


def rect_quad(quad: np.ndarray, frame_shape, rect) -> np.ndarray:
    """Map a rectangle of the source frame through the placed quad.

    ``quad`` are the four corners of the whole source image in the camera frame
    (what :func:`simulate` returns), ``frame_shape`` is the source ``(h, w)`` and
    ``rect`` is ``(x0, y0, x1, y1)`` in source pixels — e.g. the coded grid of a
    display canvas from :func:`optical_modem.phy.render.screen_geometry`.
    Returns the four corners of that rectangle in camera coordinates.
    """
    h, w = int(frame_shape[0]), int(frame_shape[1])
    x0, y0, x1, y1 = [float(v) for v in rect]
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)
    H = cv2.getPerspectiveTransform(src, np.asarray(quad, np.float32))
    pts = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], np.float32)
    hom = np.hstack([pts, np.ones((4, 1), np.float32)]) @ H.T
    return (hom[:, :2] / hom[:, 2:3]).astype(np.float32)


def simulate_screen(screen: np.ndarray, grid_rect, cfg: ChannelConfig,
                    rng: np.random.Generator | None = None,
                    pad: float = 0.06,
                    ) -> tuple[np.ndarray, np.ndarray]:
    """Camera capture of a *code displayed on a screen*.

    ``grid_rect`` is ``(x0, y0, x1, y1)`` of the **coded grid** (quiet zone and
    border excluded) inside ``screen`` — see
    :func:`optical_modem.phy.render.screen_geometry`.  The simulated camera
    frames that region plus ``pad``, so the code gets the camera's full
    resolution the way a user points a phone at a monitor.

    Returns ``(camera_image, grid_quad)``: ``grid_quad`` are the four corners of
    the coded grid in camera coordinates (ground truth), order TL,TR,BR,BL.
    """
    rng = np.random.default_rng(0) if rng is None else rng
    osz = cfg.out_size
    ow, oh = (int(osz[0]), int(osz[1])) if isinstance(osz, (tuple, list)) else (int(osz), int(osz))
    crop, (X0, Y0, S) = crop_square(screen, grid_rect, cfg.background, pad)
    scale = float(rng.uniform(cfg.scale_min, cfg.scale_max))
    ch_, cw_ = crop.shape[:2]
    quad = sample_quad(rng, min(ow, oh), scale, cfg.perspective, cfg.rotation,
                       center=(ow / 2.0, oh / 2.0), aspect=cw_ / ch_)
    img = warp_frame(crop, quad, (ow, oh), cfg.background)
    img = _photometry(img, cfg, rng)

    x0, y0, x1, y1 = [float(v) for v in grid_rect]
    pts = np.array([[x0 - X0, y0 - Y0], [x1 - X0, y0 - Y0],
                    [x1 - X0, y1 - Y0], [x0 - X0, y1 - Y0]], np.float32) / S
    src = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32)
    Hc = cv2.getPerspectiveTransform(src, np.asarray(quad, np.float32))
    hom = np.hstack([pts, np.ones((4, 1), np.float32)]) @ Hc.T
    return img, (hom[:, :2] / hom[:, 2:3]).astype(np.float32)
