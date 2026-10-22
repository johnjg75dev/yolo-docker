"""Sessions, frame plans and on-disk storage for the capture station.

Design notes
------------
*A session is a plan.*  Everything the station shows is derived from
``(seed, index)``: the modulation, the ECC, the grid, the payload and the
rendered image.  Nothing has to be stored to replay it, which means a restart
( or a second browser) rebuilds exactly the same sequence of frames, and the
frame that a photo corresponds to is always known server-side.

*Photos are the payload.*  Each accepted shot is written next to the ideal
frame it photographed, with a JSON sidecar describing the frame and (if the
classical detector found one) a quad in *photo* coordinates.  That is enough to
emit a YOLO-pose dataset with :mod:`scripts.captures_to_dataset`.

Threading: FastAPI serves requests on a single event loop, but the render and
the auto-labelling are CPU work, so they are called from a threadpool.  The
store therefore guards its state with a lock and keeps subscriptions in plain
lists of asyncio queues (created and drained on the event loop).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import string
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..config import MODULATIONS, ModemConfig, get_modulation
from ..phy.render import render_screen
from ..protocol.packet import FRAME_DATA, FrameHeader

#: modulations used by the default curriculum, easiest first.  The plan filters
#: this list against the display resolution (a patterned mode needs more pixels
#: per cell than a colour mode and may not fit).
DEFAULT_MODULATIONS = ("rgb8", "gray4", "rgb16", "rgb32", "rgb8t4", "rgb16t4",
                       "rgb32t4", "rgb32t16")
DEFAULT_ECCS = ("rs16", "rs32", "rs64")
DEFAULT_RESOLUTION = "1920x1080"
#: grid sizes sampled by the default curriculum (cells per side)
DEFAULT_GRID_CHOICES = (32, 36, 40, 48, 54, 58, 64, 72)

_ALPHABET = string.ascii_uppercase.replace("I", "").replace("O", "") + "23456789"


# --------------------------------------------------------------------------- #
# one frame of the plan
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FrameSpec:
    """A fully determined frame: config, payload and header."""

    index: int
    modulation: str
    ecc: str
    resolution: str
    cells: int
    cell_px: int
    payload_len: int
    seed: int
    mask_family: int = 1

    # ---- construction ----------------------------------------------------
    def config(self) -> ModemConfig:
        cfg = ModemConfig.for_resolution(
            self.resolution, modulation=self.modulation, ecc=self.ecc,
            cells=self.cells, quiet_zone=2,
        )
        return ModemConfig(
            modulation=cfg.modulation, ecc=cfg.ecc, layout=cfg.layout,
            cell_px=self.cell_px, quiet_zone_cells=cfg.quiet_zone_cells,
            mask_family=self.mask_family,
            display=cfg.display,
        ).fitted()

    def payload(self) -> bytes:
        rng = np.random.default_rng(self.seed)
        n = int(self.payload_len)
        if rng.random() < 0.2 and n >= 8:
            # a fifth of the frames carry human-readable text, the rest random
            # bytes: real captures should not all look like noise
            words = ("optical modem frame capture calibration whitening ring "
                     "reed solomon interleave payload header lattice").split()
            text = " ".join(rng.choice(words, size=max(2, n // 6))).encode()
            return (text + bytes(rng.integers(0, 256, n, dtype=np.uint8)))[:n]
        return bytes(rng.integers(0, 256, n, dtype=np.uint8))

    def header(self, payload: bytes | None = None) -> FrameHeader:
        cfg = self.config()
        data = self.payload() if payload is None else payload
        return FrameHeader(
            mod_id=cfg.mod().mod_id,
            ecc_id=cfg.ecc_profile().ecc_id,
            frame_type=FRAME_DATA,
            seq=self.index & 0xFFFF,
            length=len(data) + 2,   # + CRC16 appended by the frame codec
        )

    def render(self) -> np.ndarray:
        """The screen image the laptop displays (BGR uint8, display-sized)."""
        cfg = self.config()
        payload = self.payload()
        return render_screen(cfg, self.header(payload), payload, cfg.display)

    # ---- description -----------------------------------------------------
    def geometry(self) -> dict:
        from ..phy.render import screen_geometry

        return screen_geometry(self.config())

    def describe(self) -> dict:
        cfg = self.config()
        d = cfg.describe()
        return {
            "index": self.index,
            "modulation": self.modulation,
            "ecc": self.ecc,
            "resolution": self.resolution,
            "cells": self.cells,
            "cell_px": self.cell_px,
            "payload_bytes": self.payload_len,
            "seed": self.seed,
            "mask_family": self.mask_family,
            "mod_id": cfg.mod().mod_id,
            "ecc_id": cfg.ecc_profile().ecc_id,
            "bits_per_cell": cfg.mod().bits_per_cell,
            "geometry": self.geometry(),
            "capacity": {k: d.get(k) for k in
                         ("max_user_payload", "frame_px", "symbol_grid")},
        }


def _feasible_modulations(resolution: str, requested) -> list[str]:
    """Drop modulations whose grid cannot be drawn at this resolution."""
    out = []
    for name in requested:
        if name not in MODULATIONS:
            continue
        cfg = ModemConfig.for_resolution(resolution, modulation=name, ecc="rs16")
        mod = get_modulation(name)
        if cfg.cell_px >= mod.min_px_per_cell and cfg.describe()["cells"] >= 8:
            out.append(name)
    return out or ["rgb8"]


def make_plan(seed: int, count: int, resolution: str = DEFAULT_RESOLUTION,
              modulations=None, eccs=None,
              grid_choices=DEFAULT_GRID_CHOICES) -> list[FrameSpec]:
    """Deterministically build the frame list for a session."""
    mods = _feasible_modulations(resolution, modulations or DEFAULT_MODULATIONS)
    eccs = [e for e in (eccs or DEFAULT_ECCS)]
    specs: list[FrameSpec] = []
    for index in range(int(count)):
        rng = np.random.default_rng((int(seed) << 8) ^ index)
        spec = None
        for _attempt in range(12):
            mod = str(rng.choice(mods))
            ecc = str(rng.choice(eccs))
            cells = int(rng.choice(grid_choices))
            cfg = ModemConfig.for_resolution(resolution, modulation=mod,
                                             ecc=ecc, cells=cells)
            cell_px = int(cfg.cell_px)
            if cell_px < get_modulation(mod).min_px_per_cell:
                continue
            cfg = cfg.fitted()
            cap = int(cfg.describe()["max_user_payload"])
            if cap < 16:
                continue
            # mostly short payloads, sometimes a full frame
            if rng.random() < 0.25:
                n = cap - int(rng.integers(0, 8))
            else:
                n = int(rng.integers(16, max(17, min(cap, 384))))
            spec = FrameSpec(
                index=index, modulation=mod, ecc=ecc, resolution=resolution,
                cells=int(cfg.describe()["cells"]), cell_px=int(cfg.cell_px),
                payload_len=int(n), seed=int(rng.integers(1, 2**31 - 1)),
                mask_family=1,
            )
            break
        if spec is None:            # pragma: no cover - defensive fallback
            cfg = ModemConfig.for_resolution(resolution, modulation="rgb8")
            spec = FrameSpec(index, "rgb8", "rs16", resolution,
                             int(cfg.describe()["cells"]), int(cfg.cell_px),
                             64, int(rng.integers(1, 2**31 - 1)))
        specs.append(spec)
    return specs


# --------------------------------------------------------------------------- #
# photo labelling (classical CV only -- no torch in the web image)
# --------------------------------------------------------------------------- #
def _shrink(img: np.ndarray, max_pixels: int) -> tuple[np.ndarray, float]:
    """Downscale for the search; returns ``(image, scale)`` in photo pixels."""
    import cv2

    h, w = img.shape[:2]
    if h * w <= max_pixels:
        return img, 1.0
    k = (max_pixels / float(h * w)) ** 0.5
    small = cv2.resize(img, (max(32, int(round(w * k))), max(32, int(round(h * k)))),
                       interpolation=cv2.INTER_AREA)
    return small, w / float(small.shape[1])


def label_photo(img: np.ndarray, spec: FrameSpec, max_candidates: int = 3,
                refine_top: int = 3, max_pixels: int = 4_000_000) -> dict:
    """Find the frame in a phone photo; returns a label dict.

    Two stages, because the two cheap objectives are strong where the other is
    weak:

    1. **Contours -> quads.**  The receiver's localisation front end
       (:mod:`optical_modem.phy.locate`) proposes quads around each contour, as
       both "this contour is the dark border ring" and "this contour is the
       bright plate", on the raw photo and on a contrast-equalised copy.
       Candidates are ranked by how well the *known* calibration ring inside the
       frame predicts the measured colours there (``QuadDecoder``'s ring score).
       The ring is 1 cell wide, so a quad that is a whole cell out samples
       payload where ring should be and scores badly — this is what rules out
       the otherwise-equally-plausible one-cell shifts.
    2. **Gradient refinement.**  The best few quads are then fitted on the
       cell-lattice gradient energy (:func:`locate.refine_quad_energy`), which
       is what buys the tenth-of-a-cell accuracy a detector label wants, and
       re-scored on the ring.

    Nothing here decodes the payload: a label is a quad plus the measured ring
    fit, so a photo the demodulator could not read is still a usable detector
    training sample.  ``verified`` says the ring fit was near perfect, i.e. the
    quad is confidently the frame (and not, say, a laptop bezel).
    """
    from ..phy import locate
    from ..phy.classical import QuadDecoder
    from ..phy.locate import Quad

    cfg = spec.config()
    cells = int(spec.cells)
    qz = int(cfg.quiet_zone_cells)
    work, scale = _shrink(img, max_pixels)
    outs: dict[str, Any] = {"scale": float(scale)}

    import cv2

    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY) if work.ndim == 3 else work
    preps = {"raw": work}
    if gray is not work:
        # the equalised copy rescues frames shot against a bright wall or under
        # a shadow, where the raw threshold picks up the wrong contour
        preps["eq"] = cv2.cvtColor(cv2.createCLAHE(2.0, (8, 8)).apply(gray),
                                   cv2.COLOR_GRAY2BGR)
    else:
        preps["eq"] = cv2.createCLAHE(2.0, (8, 8)).apply(gray)

    seeds: list[Quad] = []
    n_cand = 0
    for name, prep in preps.items():
        for cand in locate.detect_candidates(prep, cells, qz)[:max_candidates]:
            n_cand += 1
            pts = np.asarray(cand.pts, np.float32)
            cs = float(getattr(cand, "score", 0.0) or 0.0)
            outer = locate.quad_from_outer(pts, cells, qz)
            seeds.append(Quad(outer, cs, name))
            seeds.append(Quad(locate.quad_from_border(pts, cells, qz), cs, name))
            for refined in list(locate.refine_quad(prep, outer, cells, qz))[:2]:
                seeds.append(Quad(np.asarray(refined, np.float32), cs, name))
    outs["candidates"] = n_cand
    if not seeds:
        return {**outs, "found": False, "reason": "no contours"}

    dec = QuadDecoder(cfg)
    mod = cfg.mod()
    scored = dec.score_hypotheses(work, seeds, mod)     # best ring fit first
    best = scored[0]

    tried = []
    for att in scored[:max(1, refine_top)]:
        quad = np.asarray(att.quad.pts, np.float32)
        fixed = np.asarray(locate.refine_quad_energy(work, quad, cells), np.float32)
        refined = dec.score_hypotheses(work, [Quad(fixed, att.quad.score, "refined")],
                                       mod)[0]
        energy = float(locate.align_energy(work, fixed, cells))
        tried.append({"ring": float(refined.score), "energy": energy,
                      "source": att.quad.source})
        if refined.score > best.score + 1e-6:
            best = refined
        if best.score >= 0.995 and len(tried) >= 1:
            break

    quad = np.asarray(best.quad.pts, np.float32) * scale
    return {
        **outs,
        "found": True,
        "verified": bool(best.score >= 0.85),
        "quad": [[float(x), float(y)] for x, y in quad],
        "score": float(best.score),
        "energy": float(locate.align_energy(work, np.asarray(best.quad.pts, np.float32),
                                            cells)),
        "source": best.quad.source,
        "tried": tried,
    }


# --------------------------------------------------------------------------- #
# session state
# --------------------------------------------------------------------------- #
@dataclass
class Session:
    """One capture session: a plan, a cursor, and the accepted shots."""

    id: str
    label: str
    seed: int
    resolution: str
    created: float
    plan: list[FrameSpec]
    code: str
    index: int = 0
    status: str = "live"            # live | done
    shots: list[dict] = field(default_factory=list)
    roles: dict = field(default_factory=dict)   # role -> last seen timestamp
    last_event: float = 0.0

    # ---- derived ---------------------------------------------------------
    @property
    def count(self) -> int:
        return len(self.plan)

    @property
    def current(self) -> FrameSpec | None:
        if 0 <= self.index < len(self.plan):
            return self.plan[self.index]
        return None

    def spec(self, index: int) -> FrameSpec:
        return self.plan[int(index)]

    def accepted(self) -> list[dict]:
        return [s for s in self.shots if not s.get("rejected")]

    def snapshot(self, role: str | None = None) -> dict:
        cur = self.current
        return {
            "id": self.id,
            "label": self.label,
            "code": self.code,
            "status": self.status,
            "index": self.index,
            "count": self.count,
            "done": self.index >= self.count or self.status == "done",
            "seed": self.seed,
            "resolution": self.resolution,
            "accepted": len(self.accepted()),
            "roles": {k: bool(v) for k, v in self.roles.items()},
            "current": cur.describe() if cur is not None else None,
            "frame_url": (f"/api/sessions/{self.id}/frame.png?index={cur.index}"
                          if cur is not None else None),
            "next_frame_url": (f"/api/sessions/{self.id}/frame.png"
                               f"?index={self.index + 1}"
                               if self.index + 1 < self.count else None),
            "last_shot": (self.accepted()[-1] if self.accepted() else None),
            "role": role,
            "server_time": time.time(),
        }

    # ---- persistence -----------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label, "seed": self.seed,
            "resolution": self.resolution, "created": self.created,
            "code": self.code, "index": self.index, "status": self.status,
            "shots": self.shots, "roles": self.roles,
            "plan": [s.__dict__ for s in self.plan],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        return cls(
            id=d["id"], label=d.get("label", ""), seed=int(d["seed"]),
            resolution=d.get("resolution", DEFAULT_RESOLUTION),
            created=float(d.get("created", time.time())),
            plan=[FrameSpec(**s) for s in d["plan"]], code=d["code"],
            index=int(d.get("index", 0)), status=d.get("status", "live"),
            shots=list(d.get("shots", [])), roles=dict(d.get("roles", {})),
        )


class SessionStore:
    """Sessions + their files, persisted under ``root``.

    ``root/sessions/<id>/session.json`` is the authority; frames and photos sit
    beside it (``frames/0007.png``, ``shots/0007_1.jpg`` + ``.json``).
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        (self.root / "sessions").mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}
        self._queues: dict[str, list[asyncio.Queue]] = {}
        self._load()

    # ---- disk ------------------------------------------------------------
    def _load(self) -> None:
        for path in sorted((self.root / "sessions").glob("*/session.json")):
            try:
                data = json.loads(path.read_text())
                sess = Session.from_dict(data)
            except Exception:            # pragma: no cover - corrupt file
                continue
            self._sessions[sess.id] = sess

    def session_dir(self, sid: str) -> Path:
        return self.root / "sessions" / sid

    def _write_json(self, path: Path, payload: dict | list) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=1))
        os.replace(tmp, path)

    def persist(self, sess: Session) -> None:
        self._write_json(self.session_dir(sess.id) / "session.json",
                         sess.to_dict())

    # ---- lifecycle -------------------------------------------------------
    def create(self, label: str = "", count: int = 100,
               resolution: str = DEFAULT_RESOLUTION, seed: int | None = None,
               modulations=None, eccs=None) -> Session:
        with self._lock:
            sid = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
            seed = int(seed if seed is not None else secrets.randbelow(2**31 - 1))
            code = "".join(secrets.choice(_ALPHABET) for _ in range(6))
            plan = make_plan(seed, count, resolution, modulations, eccs)
            sess = Session(id=sid, label=label or time.strftime("%Y-%m-%d %H:%M"),
                           seed=seed, resolution=resolution, created=time.time(),
                           plan=plan, code=code)
            self._sessions[sid] = sess
            self.persist(sess)
            return sess

    def get(self, sid: str) -> Session | None:
        with self._lock:
            return self._sessions.get(sid)

    def list(self) -> list[dict]:
        with self._lock:
            out = [{"id": s.id, "label": s.label, "created": s.created,
                    "index": s.index, "count": s.count, "status": s.status,
                    "accepted": len(s.accepted()), "resolution": s.resolution}
                   for s in self._sessions.values()]
        return sorted(out, key=lambda d: -d["created"])

    def delete(self, sid: str) -> bool:
        with self._lock:
            sess = self._sessions.pop(sid, None)
        if sess is None:
            return False
        import shutil

        shutil.rmtree(self.session_dir(sid), ignore_errors=True)
        return True

    # ---- look / feel -----------------------------------------------------
    def frame_png(self, sess: Session, index: int) -> bytes:
        """PNG of plan frame ``index``, rendered once and cached on disk."""
        import cv2

        path = self.session_dir(sess.id) / "frames" / f"{int(index):04d}.png"
        if path.exists():
            return path.read_bytes()
        spec = sess.spec(index)
        img = spec.render()
        path.parent.mkdir(parents=True, exist_ok=True)
        ok, buf = cv2.imencode(".png", img)
        if not ok:                      # pragma: no cover
            raise RuntimeError("png encode failed")
        path.write_bytes(buf.tobytes())
        return path.read_bytes()

    # ---- shots -----------------------------------------------------------
    def add_shot(self, sess: Session, index: int, image_bytes: bytes,
                 meta: dict, ext: str = "jpg") -> dict:
        """Store one photo, label it, advance the cursor, notify subscribers."""
        with self._lock:
            shots_dir = self.session_dir(sess.id) / "shots"
            shots_dir.mkdir(parents=True, exist_ok=True)
            attempt = 1 + sum(1 for s in sess.shots
                              if s["index"] == index and not s.get("rejected"))
            stem = f"{int(index):04d}_{attempt}"
            img_path = shots_dir / f"{stem}.{ext}"
            img_path.write_bytes(image_bytes)

            label = meta.pop("label", None)
            record = dict(meta)
            record.update({
                "index": int(index), "attempt": attempt,
                "file": str(img_path.relative_to(self.session_dir(sess.id))),
                "bytes": len(image_bytes),
                "received": time.time(),
                "spec": sess.spec(index).describe(),
                "label": label,
                "rejected": False,
            })
            self._write_json(shots_dir / f"{stem}.json", record)
            sess.shots.append({k: record[k] for k in
                               ("index", "attempt", "file", "bytes",
                                "received", "rejected")})
            sess.shots[-1]["label_found"] = bool(label and label.get("found"))
            sess.shots[-1]["label_score"] = (label or {}).get("score")

            if sess.index == index:
                sess.index = min(sess.count, index + 1)
            if sess.index >= sess.count:
                sess.status = "done"
            sess.last_event = time.time()
            self.persist(sess)
            snapshot = sess.snapshot()
        self.publish(sess.id, snapshot, event="shot")
        return record

    def reject_last(self, sess: Session) -> dict:
        """Undo: drop the newest accepted shot and step back."""
        with self._lock:
            accepted = [s for s in sess.shots if not s.get("rejected")]
            if not accepted:
                return sess.snapshot()
            last = accepted[-1]
            last["rejected"] = True
            sess.index = int(last["index"])
            sess.status = "live"
            sess.last_event = time.time()
            self.persist(sess)
            snapshot = sess.snapshot()
        self.publish(sess.id, snapshot, event="reject")
        return snapshot

    def skip(self, sess: Session) -> dict:
        with self._lock:
            if sess.index < sess.count:
                sess.index += 1
                if sess.index >= sess.count:
                    sess.status = "done"
                sess.last_event = time.time()
                self.persist(sess)
            snapshot = sess.snapshot()
        self.publish(sess.id, snapshot, event="skip")
        return snapshot

    def seek(self, sess: Session, index: int) -> dict:
        with self._lock:
            sess.index = int(max(0, min(sess.count, index)))
            sess.status = "done" if sess.index >= sess.count else "live"
            sess.last_event = time.time()
            self.persist(sess)
            snapshot = sess.snapshot()
        self.publish(sess.id, snapshot, event="seek")
        return snapshot

    def touch_role(self, sess: Session, role: str) -> None:
        with self._lock:
            sess.roles[role] = time.time()

    # ---- pub/sub ---------------------------------------------------------
    def subscribe(self, sid: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=16)
        with self._lock:
            self._queues.setdefault(sid, []).append(q)
        return q

    def unsubscribe(self, sid: str, q: asyncio.Queue) -> None:
        with self._lock:
            lst = self._queues.get(sid, [])
            if q in lst:
                lst.remove(q)
            if not lst:
                self._queues.pop(sid, None)

    def publish(self, sid: str, payload: dict, event: str = "state") -> None:
        """Fan a snapshot out to every subscriber of ``sid``."""
        with self._lock:
            queues = list(self._queues.get(sid, []))
        for q in queues:
            try:
                q.put_nowait({"event": event, "data": payload})
            except asyncio.QueueFull:     # pragma: no cover - slow client
                try:
                    q.get_nowait()
                    q.put_nowait({"event": event, "data": payload})
                except Exception:
                    pass

    # ---- export ----------------------------------------------------------
    def export(self, sess: Session) -> dict:
        """Everything a dataset builder needs, without the images themselves."""
        shots = []
        for path in sorted((self.session_dir(sess.id) / "shots").glob("*.json")):
            try:
                shots.append(json.loads(path.read_text()))
            except Exception:            # pragma: no cover
                continue
        return {
            "session": {k: v for k, v in sess.to_dict().items() if k != "plan"},
            "frames": [s.describe() for s in sess.plan],
            "shots": shots,
        }
