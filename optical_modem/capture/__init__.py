"""Paired capture station: a laptop displays codes, a phone photographs them.

This package is a *data-collection* front end for the modem.  It does not touch
the modem's protocol; it renders frames with the same renderer the transmitter
uses (:func:`optical_modem.phy.render.render_screen`) and stores the photo next
to the exact frame that was on screen, which is what a real-capture training
set needs.

Two clients join one session:

* **display** (laptop) — shows the current frame and the pairing code,
* **camera** (phone) — shows the live rear camera, shoots the frame, uploads it.

When a photo arrives the server records it, advances the session, and pushes
the new state to both clients over Server-Sent Events, so both screens move on
together without anyone pressing "next".

See ``docs/CAPTURE_APP.md`` for deployment (Docker / Coolify) and the data
layout.
"""

from .sessions import (  # noqa: F401
    FrameSpec,
    Session,
    SessionStore,
    label_photo,
)

__all__ = ["FrameSpec", "Session", "SessionStore", "label_photo"]
