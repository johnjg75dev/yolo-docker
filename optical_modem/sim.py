"""One place where "what the user sees on the screen" becomes "what the camera gets".

Transmit and capture are split here so that the CLI, the GUI, the benchmark and
the tests all build frames and simulated captures the same way:

* :func:`prepare_frame` renders a header+payload for a config.  If the config
  targets a display (``cfg.display``) the code is centred on a full-resolution
  black canvas — a real 1920x1080 monitor — otherwise a standalone square frame
  is produced.
* :func:`capture` runs that screen through the camera channel.  On a display
  config the simulated camera *frames the code* (see
  :func:`optical_modem.phy.channel.simulate_screen`), which is how someone
  actually points a phone at a monitor; without this an 84-cell code on a
  1920x1080 screen would be squeezed into a third of the camera frame and every
  resolution experiment would be meaningless.
"""
from __future__ import annotations

import numpy as np

from .config import ModemConfig
from .phy.channel import ChannelConfig, simulate, simulate_screen
from .phy.render import render_frame, render_screen, screen_geometry
from .protocol.frames import effective_layout


def prepare_frame(cfg: ModemConfig, header, payload: bytes) -> np.ndarray:
    """Render the transmitting display (screen canvas or square frame)."""
    if cfg.display is not None:
        return render_screen(cfg, header, payload)
    return render_frame(cfg, header, payload)


def capture(cfg: ModemConfig, screen: np.ndarray, channel: ChannelConfig,
            rng: np.random.Generator | None = None
            ) -> tuple[np.ndarray, np.ndarray]:
    """Simulate a camera capture of ``screen``; returns ``(image, grid_quad)``.

    ``grid_quad`` are the four corners of the *coded cells* (not the border) in
    camera coordinates, i.e. ground truth a receiver could only get from a
    perfect detector.
    """
    rng = np.random.default_rng(0) if rng is None else rng
    if cfg.display is None:
        return simulate(screen, channel, rng)
    geo = screen_geometry(cfg)
    return simulate_screen(screen, geo["grid"], channel, rng)


def grid_size(cfg: ModemConfig) -> int:
    return effective_layout(cfg).cells
