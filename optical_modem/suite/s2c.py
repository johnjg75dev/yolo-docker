"""The S2C ("screen-to-camera") software: transmit bytes over a screen.

This is what the model is *for*.  One device shows a sequence of frames, another
points a camera at it, and the bytes come out the far side.

* **Transmit** — a message or file is chunked by :class:`Packetizer`, each chunk
  is rendered as a frame on a screen-sized canvas, and the browser plays them at
  a chosen rate (with repeats, so a missed exposure costs nothing).
* **Receive** — every camera still is decoded by the *active* detector +
  the shared demodulator/FEC, and the chunks are reassembled out of order, with
  duplicates ignored.  The result is byte-identical or the transfer is not done.
* **Loopback** — the same path with the camera replaced by the channel
  simulator: it answers "does the model I just trained actually decode frames?"
  in a few seconds, without picking up a phone.

State is per transfer id and lives in memory (a transfer is a live session, not
an archive).  Frames are rendered on demand and cached, so a 40-frame transfer
costs a few PNGs of memory, not a directory of them.
"""
from __future__ import annotations

import hashlib
import re
import io
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..config import ModemConfig
from ..protocol.packet import FRAME_DATA, DecodedFrame, FrameHeader, Packetizer
from ..sim import prepare_frame

#: how many transfers to keep in memory at once
MAX_TRANSFERS = 8
#: how many rendered frame PNGs to keep per transfer
MAX_FRAME_CACHE = 64


@dataclass
class Transfer:
    id: str
    name: str
    data: bytes
    cfg: ModemConfig
    params: dict
    headers: list[FrameHeader] = field(default_factory=list)
    bodies: list[bytes] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    pngs: "OrderedDict[int, bytes]" = field(default_factory=OrderedDict)
    # receive side
    rx: Packetizer | None = None
    got: int = 0
    dups: int = 0
    fails: int = 0
    last_error: str = ""
    message: bytes | None = None
    log: list[dict] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.headers)

    def info(self) -> dict:
        d = self.cfg.describe()
        return {
            "id": self.id,
            "name": self.name,
            "bytes": len(self.data),
            "sha1": hashlib.sha1(self.data).hexdigest()[:12],
            "frames": self.count,
            "repeat": self.params.get("repeat", 1),
            "modulation": self.params["modulation"],
            "ecc": self.params["ecc"],
            "resolution": self.params["resolution"],
            "cell_px": d.get("cell_px") or self.cfg.cell_px,
            "cells": self.cfg.layout.grid if hasattr(self.cfg, "layout") else None,
            "payload_per_frame": int(self.cfg.describe().get("max_user_payload", 0) - 4),
            "frame_px": [self.cfg.display.width, self.cfg.display.height]
            if self.cfg.display else [self.cfg.frame_px, self.cfg.frame_px],
            "finder": self.cfg.describe().get("finder"),
            "capacity_lost_pct": (
                None if not self.cfg.layout.finder_map() else
                round(100.0 * self.cfg.layout.finder_cost()
                      / max(1, self.cfg.layout.n_symbols), 1)),
            "rate_fps": float(self.params.get("fps", 3.0)),
            "created": self.created,
            "received": self.got,
            "duplicates": self.dups,
            "failed": self.fails,
            "complete": self.message is not None,
            "last_error": self.last_error,
        }

    def result(self) -> dict:
        if self.message is None:
            return {"complete": False, "received": self.got, "frames": self.count,
                    "duplicates": self.dups, "failed": self.fails,
                    "last_error": self.last_error}
        text = None
        if self.params.get("text"):
            try:
                text = self.message.decode("utf-8")
            except UnicodeDecodeError:
                text = None
        return {"complete": True, "received": self.got, "frames": self.count,
                "duplicates": self.dups, "failed": self.fails,
                "bytes": len(self.message),
                "sha1": hashlib.sha1(self.message).hexdigest()[:12],
                "sha1_ok": hashlib.sha1(self.message).hexdigest() ==
                           hashlib.sha1(self.data).hexdigest(),
                "text": text,
                "name": self.name}


#: cell counts that keep each cell big enough to survive a phone photo.  Bigger
#: grids carry more bytes per frame but decode *much* worse: measured over 12
#: simulated handheld captures (level 0.2-0.5, 1080 px) a 50-cell/12 px frame
#: decoded 7/12 while a 32-cell/18 px frame decoded 10/12 with the same model.
ROBUST_CELLS = {"720": 32, "1080": 40}


def robust_cells(resolution: str) -> int:
    """The grid size the S2C page starts from for a given screen."""
    try:
        height = int(str(resolution).lower().split("x")[-1])
    except ValueError:
        height = 720
    return ROBUST_CELLS["1080"] if height > 800 else ROBUST_CELLS["720"]


def make_config(modulation: str = "rgb8", ecc: str = "rs32",
                resolution: str = "1280x720", cells: int | None = None,
                symbol_px: int = 16, finder: str = "none",
                finder_span: str = "corners") -> ModemConfig:
    """A screen-sized modem config, fitted to the requested resolution.

    ``finder`` is an optional :mod:`optical_modem.protocol.finder` shape
    (``ring``, ``dice``, ``bullseye``, ``checker``, ``diamond``, ``cross``,
    ``x``, ``bracket``, ``triangle``, ``solid``, or ``none``).
    """
    if cells is None:
        cells = robust_cells(resolution)
    cfg = ModemConfig.for_resolution(resolution, modulation=modulation, ecc=ecc,
                                     cells=cells, symbol_px=symbol_px,
                                     margin_px=32, quiet_zone=2)
    layout = cfg.layout
    if finder and str(finder).lower() not in ("none", "", "off"):
        from ..protocol.finder import get_shape

        get_shape(finder)                      # fail fast on a bad name
        layout = type(layout)(cells=layout.cells, cps=layout.cps, ring=layout.ring,
                              header_rows=layout.header_rows,
                              finder=str(finder).lower(), finder_span=finder_span)
    return ModemConfig(
        modulation=cfg.modulation, ecc=cfg.ecc, layout=layout,
        cell_px=cfg.cell_px, quiet_zone_cells=cfg.quiet_zone_cells,
        mask_family=cfg.mask_family, display=cfg.display,
    ).fitted()


class S2C:
    """Transmit/receive transfers; one instance per process."""

    def __init__(self, weights: str | None = None, decode_mode: str = "auto",
                 max_transfers: int = MAX_TRANSFERS):
        self.weights = weights
        self.decode_mode = decode_mode
        self.max_transfers = max_transfers
        self._transfers: "OrderedDict[str, Transfer]" = OrderedDict()
        self._lock = threading.RLock()
        self._receiver = None
        self._receiver_key: tuple | None = None

    # ---- transmit --------------------------------------------------------
    def transmit(self, text: str | None = None, data: bytes | None = None,
                 name: str = "", modulation: str = "rgb8", ecc: str = "rs32",
                 resolution: str = "1280x720", cells: int | None = None,
                 symbol_px: int = 16, repeat: int = 2, fps: float = 3.0,
                 max_frames: int = 64, finder: str = "none",
                 finder_span: str = "corners") -> Transfer:
        if data is None:
            data = (text or "").encode("utf-8")
        if not data:
            raise ValueError("nothing to send")
        cfg = make_config(modulation, ecc, resolution, cells, symbol_px,
                          finder=finder, finder_span=finder_span)
        pkt = Packetizer(cfg)
        headers, bodies = [], []
        for hdr, body in pkt.frames_for(data):
            headers.append(hdr)
            bodies.append(body)
            if len(headers) >= max_frames:
                break
        if not headers:
            raise ValueError("payload produced no frames")
        tid = time.strftime("%H%M%S") + "-" + secrets.token_hex(3)
        tr = Transfer(
            id=tid, name=name or "message.txt", data=data, cfg=cfg,
            params={"text": text is not None, "modulation": modulation,
                    "ecc": ecc, "resolution": resolution, "cells": cells,
                    "symbol_px": symbol_px, "repeat": int(repeat),
                    "fps": float(fps), "finder": cfg.layout.finder,
                    "finder_span": cfg.layout.finder_span},
            headers=headers, bodies=bodies,
        )
        tr.rx = Packetizer(cfg)
        with self._lock:
            self._transfers[tid] = tr
            while len(self._transfers) > self.max_transfers:
                self._transfers.popitem(last=False)
        return tr

    def get(self, tid: str) -> Transfer:
        with self._lock:
            tr = self._transfers.get(tid)
        if tr is None:
            raise KeyError(tid)
        return tr

    def list(self) -> list[dict]:
        with self._lock:
            return [tr.info() for tr in reversed(self._transfers.values())]

    def payload(self, tid: str) -> tuple[bytes, str]:
        """The reassembled payload plus a safe filename — the phone's download.

        The receive side never learns the original filename (only the bytes are
        in the frames), so ``name`` is whatever the sender typed, sanitised.
        """
        tr = self.get(tid)
        if tr.message is None:
            raise LookupError("transfer is not complete yet")
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", tr.name or "received.bin").strip("._")
        if not name:
            name = "received.bin"
        return bytes(tr.message), name

    def frame_png(self, tid: str, index: int) -> bytes:
        tr = self.get(tid)
        index = int(index) % max(1, tr.count)
        with self._lock:
            hit = tr.pngs.get(index)
            if hit is not None:
                tr.pngs.move_to_end(index)
                return hit
        import cv2

        img = prepare_frame(tr.cfg, tr.headers[index], tr.bodies[index])
        ok, buf = cv2.imencode(".png", img)
        if not ok:                            # pragma: no cover
            raise RuntimeError("png encode failed")
        png = buf.tobytes()
        with self._lock:
            tr.pngs[index] = png
            while len(tr.pngs) > MAX_FRAME_CACHE:
                tr.pngs.popitem(last=False)
        return png

    def frame_jpeg(self, tid: str, index: int, max_px: int = 1400) -> bytes:
        """Small preview of a frame (playback uses PNG, previews use this)."""
        import cv2

        tr = self.get(tid)
        img = prepare_frame(tr.cfg, tr.headers[int(index) % tr.count],
                            tr.bodies[int(index) % tr.count])
        h, w = img.shape[:2]
        if max(h, w) > max_px:
            k = max_px / max(h, w)
            img = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        return buf.tobytes() if ok else b""

    # ---- receive ---------------------------------------------------------
    def _rx(self, tr: Transfer):
        """A receiver bound to this transfer's *config* (locked, cached).

        Two fixes over the previous version:

        * the read-modify-write of the single cache slot was unguarded, so two
          concurrent decodes could both miss and both construct a receiver --
          and the last writer won while the loser's model stayed resident;
        * the key included ``tr.id`` even though the receiver depends only on the
          frame geometry, weights and mode. Alternating between two transfers
          (laptop transmits, phone receives) therefore rebuilt the receiver on
          every single frame, and each build runs ``YOLO(weights)`` -- a model
          load from disk plus a torch init.

        Keying on the configuration makes the alternation free, and the lock
        makes the cache safe under the thread-pool concurrency the API uses.
        """
        from ..receiver import HybridReceiver

        cfg = tr.cfg
        layout = cfg.layout
        key = (cfg.modulation, cfg.ecc, cfg.cell_px, layout.cells, layout.cps,
               layout.ring, layout.header_rows, layout.finder,
               layout.finder_span, self.weights, self.decode_mode)
        with self._lock:
            cached = self._receiver
            if cached is not None and self._receiver_key == key:
                return cached
            receiver = HybridReceiver(cfg, mode=self.decode_mode,
                                      yolo_weights=self.weights,
                                      yolo_imgsz=448)
            self._receiver = receiver
            self._receiver_key = key
            return receiver

    def decode(self, tr: Transfer, img: np.ndarray) -> dict:
        """Decode one camera still and feed it to the transfer's reassembler."""
        rx = self._rx(tr)
        t0 = time.perf_counter()
        report = rx.decode(img)
        dt = (time.perf_counter() - t0) * 1000
        entry: dict[str, Any] = {"ms": round(dt, 1), "mode": report.mode,
                                 "detections": len(report.detections),
                                 "ok": False}
        best = report.best()
        if best is not None and best.header is not None:
            entry["frame_type"] = int(best.header.frame_type)
            entry["seq"] = int(best.header.seq)
            entry["ring_score"] = round(float(best.ring_score), 3)
        if report.ok and best is not None:
            decoded = DecodedFrame(header=best.header, payload=best.payload,
                                   crc_ok=True, fec_ok=True,
                                   n_corrected=best.n_corrected,
                                   quality=best.quality)
            with self._lock:
                message = tr.rx.push(decoded)
                if message is not None:
                    tr.message = message
                # the reassembler owns the counters: it is the only thing that
                # knows whether this *sequence number* was already held
                tr.got = tr.rx.received
                tr.dups = tr.rx.duplicates
                tr.log.append(entry)
                tr.log[:] = tr.log[-40:]
            entry["ok"] = True
            entry["received"] = tr.got
            entry["complete"] = tr.message is not None
        else:
            with self._lock:
                tr.fails += 1
                tr.last_error = (best.reason if best is not None else "no frame found")
                tr.log.append(entry)
                tr.log[:] = tr.log[-40:]
        return {"transfer": tr.info(), "result": tr.result(), "decode": entry}

    def receive_bytes(self, tid: str, raw: bytes) -> dict:
        import cv2

        tr = self.get(tid)
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("not an image")
        return self.decode(tr, img)

    def reset_rx(self, tid: str) -> dict:
        tr = self.get(tid)
        with self._lock:
            tr.rx = Packetizer(tr.cfg)
            tr.got = tr.fails = tr.dups = 0
            tr.message = None
            tr.log.clear()
        return tr.result()

    # ---- loopback (no phone needed) --------------------------------------
    def loopback(self, tid: str, level: float = 0.35, frames: int = 4,
                 seed: int = 0) -> dict:
        """Render -> simulate a camera -> decode, for the first frames.

        This is the check the guide asks for after training: it exercises the
        *active* model, the demodulator and the FEC, and reports corner error
        against ground truth as well as whether the bytes survive.
        """
        from ..phy.channel import (ChannelConfig, grid_quad_from_frame_quad, rect_quad,
                                   simulate)
        from ..phy.render import screen_geometry

        tr = self.get(tid)
        n = max(1, min(int(frames), tr.count))
        rng = np.random.default_rng(seed)
        rows = []
        for i in range(n):
            frame = prepare_frame(tr.cfg, tr.headers[i], tr.bodies[i])
            ch = ChannelConfig.handheld(rng, level)
            # a phone photo, not a thumbnail: the benchmark numbers this check is
            # compared against were measured at 1080 px on the long edge
            ch.out_size = 1080
            cam, quad = simulate(frame, ch, rng)
            # ground truth: where the *coded cells* ended up in the camera frame.
            # On a display canvas the code is a square in the middle of a wide
            # screen, so the canvas rectangle has to be mapped, not `frame_px`
            if tr.cfg.display is not None:
                gt = rect_quad(quad, frame.shape[:2], screen_geometry(tr.cfg)["grid"])
            else:
                gt = grid_quad_from_frame_quad(quad, tr.cfg.frame_px, tr.cfg.cell_px,
                                               tr.cfg.quiet_zone_cells,
                                               tr.cfg.layout.grid)
            rx = self._rx(tr)
            t0 = time.perf_counter()
            report = rx.decode(cam)
            ms = (time.perf_counter() - t0) * 1000
            best = report.best()
            det = report.detections[0] if report.detections else None
            side = float(np.linalg.norm(gt[0] - gt[1]))
            corner = (float(np.linalg.norm(np.asarray(det.quad.pts) - gt, axis=1).mean())
                      / side) if det is not None else None
            # ``payload`` is the frame body with its CRC already stripped, so an
            # exact comparison against the body that was transmitted is the
            # strongest possible statement: the bytes really came back
            payload_ok = bool(report.ok and best is not None
                              and best.payload == tr.bodies[i])
            if report.ok and best is not None:
                with self._lock:
                    tr.rx.push(DecodedFrame(header=best.header, payload=best.payload,
                                            crc_ok=True, fec_ok=True,
                                            n_corrected=best.n_corrected,
                                            quality=best.quality))
                    tr.got = tr.rx.received
                    tr.dups = tr.rx.duplicates
            rows.append({
                "frame": i, "ok": bool(report.ok),
                "payload_matches": payload_ok,
                "corner_err": None if corner is None else round(corner, 3),
                "detections": len(report.detections),
                "ring_score": round(float(best.ring_score), 3) if best else None,
                "finder_score": (None if best is None or best.finder_score is None
                                 else round(float(best.finder_score), 3)),
                "ms": round(ms, 1), "mode": report.mode,
                "reason": "" if report.ok else (best.reason if best else "no frame"),
            })
        complete = tr.message is not None
        return {
            "frames": rows,
            "decoded": sum(1 for r in rows if r["ok"]),
            "corner_err_median": (float(np.nanmedian([r["corner_err"] for r in rows
                                                      if r["corner_err"] is not None]))
                                  if any(r["corner_err"] is not None for r in rows) else None),
            "complete": complete,
            "sha1_ok": (tr.result().get("sha1_ok") if complete else False),
            "received": tr.got,
            "total_frames": tr.count,
            "weights": self.weights,
        }
