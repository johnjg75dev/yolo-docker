"""FastAPI application for the paired capture station (display + phone camera).

Endpoints
---------
GET  /                          landing page (login, pick a role)
GET  /display                   laptop: shows the frame to photograph
GET  /camera                    phone: live camera, shoot + upload
GET  /manifest.webmanifest      PWA manifest
GET  /sw.js                     service worker (app shell only)

POST /api/login                 {"password": ...} -> signed cookie
POST /api/logout
GET  /api/me                    am I authenticated, which role am I using
GET  /api/sessions              list sessions (resume an unfinished one)
POST /api/sessions              create one (display side)
GET  /api/sessions/{sid}        snapshot
POST /api/sessions/{sid}/join   {"role": "display"|"camera", "code": ...}
GET  /api/sessions/{sid}/frame.png?index=N     the ideal frame (cached)
POST /api/sessions/{sid}/shots  multipart: file + index + device  -> advance
POST /api/sessions/{sid}/reject undo the last shot
POST /api/sessions/{sid}/skip   skip the current frame
POST /api/sessions/{sid}/seek   {"index": N}
GET  /api/sessions/{sid}/export dataset manifest (frames + shot metadata)
GET  /api/sessions/{sid}/events Server-Sent Events: live state for both clients

The app is deliberately single-process: session state lives in memory and is
persisted to ``DATA_DIR`` after every mutation, and SSE subscribers are
in-process queues.  ``uvicorn --workers >1`` would break both.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from contextlib import closing

from fastapi import (Depends, FastAPI, File, Form, HTTPException, Request, Response,
                     UploadFile, WebSocket, WebSocketDisconnect)
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .. import net
try:
    from ..suite.logging_config import configure as _configure_logging, get_logger as _get_logger
    _configure_logging()
    log = _get_logger("capture.app")
except Exception:  # pragma: no cover
    import logging
    log = logging.getLogger("capture.app")
from ..config import MODULATIONS
from .sessions import (DEFAULT_RESOLUTION, Session, SessionStore, label_photo)

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"

# --------------------------------------------------------------------------- #
# configuration (env, all optional except on a public server: set the password)
# --------------------------------------------------------------------------- #
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()
if os.environ.get("REQUIRE_PASSWORD", "0").lower() in ("1", "true", "yes", "on") and not APP_PASSWORD:
    raise RuntimeError("APP_PASSWORD must be configured when REQUIRE_PASSWORD is enabled")
COOKIE_NAME = "om_capture"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30          # 30 days
AUTOLABEL = os.environ.get("AUTOLABEL", "1") not in ("0", "false", "no")
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "24")) * 1024 * 1024


def _secret() -> bytes:
    """HMAC key: ``APP_SECRET`` if set, else a generated one persisted in /data.

    Persisting it means a container restart does not log every device out.
    """
    env = os.environ.get("APP_SECRET", "").strip()
    if env:
        return env.encode()
    path = DATA_DIR / "secret.key"
    if path.exists():
        return path.read_bytes().strip()
    key = secrets.token_hex(32).encode()
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(key)
        path.chmod(0o600)
    except OSError:                     # read-only /data: fall back to memory
        pass
    return key


SECRET = _secret()
STORE = SessionStore(DATA_DIR)
app = FastAPI(title="Optical modem capture station", docs_url=None,
              redoc_url=None, openapi_url=None)
# behind Traefik / Coolify / nginx: trust the forwarded scheme and keep every
# redirect relative, so https can never be downgraded into a redirect loop
net.install(app)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

#: naive per-IP login throttle (a personal deployment, not a public service)
_ATTEMPTS: dict[str, list[float]] = {}


# --------------------------------------------------------------------------- #
# auth helpers
# --------------------------------------------------------------------------- #
def _sign(value: str) -> str:
    mac = hmac.new(SECRET, value.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{value}.{mac}"


def _revocation_store():
    """Durable per-token logout revocations; never store bearer tokens."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA_DIR / "auth-revocations.sqlite3", timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS revoked (digest TEXT PRIMARY KEY, expires REAL NOT NULL)")
    return db


def _token_revoked(token: str) -> bool:
    digest = hashlib.sha256(token.encode()).hexdigest()
    with closing(_revocation_store()) as db:
        return db.execute("SELECT 1 FROM revoked WHERE digest=? AND expires>?", (digest, time.time())).fetchone() is not None


def _revoke_token(token: str) -> None:
    digest = hashlib.sha256(token.encode()).hexdigest()
    # Tokens have an absolute expiry, so old entries can be removed safely.
    expires = float(token.rsplit(".", 1)[0]) + COOKIE_MAX_AGE
    with closing(_revocation_store()) as db, db:
        db.execute("DELETE FROM revoked WHERE expires<=?", (time.time(),))
        db.execute("INSERT OR REPLACE INTO revoked (digest, expires) VALUES (?, ?)", (digest, expires))


def _auth_candidate(request: Request) -> str:
    return (request.cookies.get(COOKIE_NAME, "") or request.headers.get("x-om-token", "")
            or request.query_params.get("_tok", ""))


def _verify(token: str) -> bool:
    if not token or "." not in token:
        return False
    value, mac = token.rsplit(".", 1)
    expect = hmac.new(SECRET, value.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(mac, expect):
        return False
    try:
        issued = float(value)
    except ValueError:
        return False
    age = time.time() - issued
    return 0 <= age < COOKIE_MAX_AGE and not _token_revoked(token)


def _password_ok(given: str) -> bool:
    if not APP_PASSWORD:
        # no password configured: local/dev use only (the UI says so)
        return True
    return hmac.compare_digest(given.strip(), APP_PASSWORD)


async def require_auth(request: Request) -> None:
    cand = _auth_candidate(request)
    if not _verify(cand):
        log.debug("auth required (no/invalid cookie) path=%s", request.url.path)
        raise HTTPException(401, "login required")


def require_session(sid: str) -> Session:
    sess = STORE.get(sid)
    if sess is None:
        raise HTTPException(404, "unknown session")
    return sess


# --------------------------------------------------------------------------- #
# pages
# --------------------------------------------------------------------------- #
def _page(name: str) -> HTMLResponse:
    path = STATIC / name
    if not path.exists():                # pragma: no cover
        raise HTTPException(500, f"missing {name}")
    return HTMLResponse(net.stamp_build(path.read_text()), headers={
        "Cache-Control": "no-store",
        "Service-Worker-Allowed": "/",
    })


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return _page("index.html")


@app.get("/display", response_class=HTMLResponse)
def display_page() -> HTMLResponse:
    return _page("display.html")


@app.get("/camera", response_class=HTMLResponse)
def camera_page() -> HTMLResponse:
    return _page("camera.html")


@app.get("/manifest.webmanifest")
def manifest() -> Response:
    return Response((STATIC / "manifest.webmanifest").read_text(),
                    media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker() -> Response:
    """The service worker, versioned by the build.

    The cache name must change whenever an asset does, or an installed PWA keeps
    serving the previous release's JavaScript forever — and because service
    workers only run in a secure context, that stale-shell bug shows up **only
    over https** ("it works on http but not behind Traefik").
    """
    body = (STATIC / "sw.js").read_text().replace("__BUILD__", net.build_id())
    return Response(body, media_type="application/javascript",
                    headers={"Cache-Control": "no-store",
                             "Service-Worker-Allowed": "/"})


@app.get("/api/net/diagnose")
def net_diagnose(request: Request) -> dict:
    """Public: what this process can tell about the proxy in front of it.

    Deliberately unauthenticated — it is the endpoint you hit when login does not
    work, so it must not require a login.  It exposes no secrets (no password, no
    cookie value, no paths).
    """
    return net.diagnose(request)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "data_dir": str(DATA_DIR),
            "password_set": bool(APP_PASSWORD), "sessions": len(STORE.list())}


@app.get("/api/capabilities")
def capabilities() -> dict:
    """What a new session may choose, and whether a password is configured."""
    from ..config import DisplayProfile

    displays = []
    for label in ("1280x720", "1920x1080", "2560x1440", "3840x2160"):
        d = DisplayProfile.parse(label)
        displays.append({"label": label, "width": d.width, "height": d.height})
    return {
        "modulations": list(MODULATIONS),
        "displays": displays,
        "defaults": {"count": 100, "resolution": DEFAULT_RESOLUTION},
        "password_set": bool(APP_PASSWORD),
        "autolabel": AUTOLABEL,
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
    }


# --------------------------------------------------------------------------- #
# auth endpoints
# --------------------------------------------------------------------------- #
@app.post("/api/login")
async def login(request: Request, response: Response, payload: dict) -> dict:
    ip = request.client.host if request.client else "?"
    now = time.time()
    hits = [t for t in _ATTEMPTS.get(ip, []) if now - t < 60.0]
    if len(hits) >= 10:
        log.warning("login rate-limited ip=%s", ip)
        raise HTTPException(429, "too many attempts, wait a minute")
    if not _password_ok(str(payload.get("password", ""))):
        _ATTEMPTS[ip] = hits + [now]
        log.warning("login FAILED ip=%s", ip)
        raise HTTPException(401, "wrong password")
    _ATTEMPTS.pop(ip, None)
    token = _sign(str(now))
    log.info("login OK ip=%s", ip)
    # Secure is derived from the *browser's* scheme (X-Forwarded-Proto), not from
    # the plain-HTTP socket this process sees: behind Traefik that socket is
    # always http, and a Secure cookie sent over plain http is silently dropped
    # by the browser — which looks exactly like "the password is wrong".
    response.set_cookie(COOKIE_NAME, token, max_age=COOKIE_MAX_AGE,
                        httponly=True, samesite="lax",
                        secure=net.cookie_secure(request))
    return {"ok": True, "password_set": bool(APP_PASSWORD), "token": token}


@app.post("/api/logout")
async def logout(request: Request, response: Response) -> dict:
    token = _auth_candidate(request)
    if _verify(token):
        _revoke_token(token)
    response.delete_cookie(COOKIE_NAME, secure=net.cookie_secure(request),
                           httponly=True, samesite="lax")
    return {"ok": True}


@app.get("/api/me", dependencies=[Depends(require_auth)])
async def me(request: Request) -> dict:
    return {"authenticated": True, "role": request.query_params.get("role"),
            "password_set": bool(APP_PASSWORD)}


# --------------------------------------------------------------------------- #
# sessions
# --------------------------------------------------------------------------- #
@app.get("/api/sessions", dependencies=[Depends(require_auth)])
async def list_sessions() -> dict:
    return {"sessions": STORE.list()}


@app.post("/api/sessions", dependencies=[Depends(require_auth)])
async def create_session(payload: dict | None = None) -> dict:
    payload = payload or {}
    count = int(payload.get("count", 100))
    if not 1 <= count <= 5000:
        raise HTTPException(400, "count must be 1..5000")
    resolution = str(payload.get("resolution", DEFAULT_RESOLUTION))
    mods = payload.get("modulations") or None
    eccs = payload.get("eccs") or None
    sess = await run_in_threadpool(
        STORE.create, str(payload.get("label", "")), count, resolution,
        payload.get("seed"), mods, eccs)
    STORE.touch_role(sess, "display")
    return sess.snapshot(role="display")


@app.get("/api/sessions/lookup", dependencies=[Depends(require_auth)])
async def lookup(code: str) -> dict:
    """Resolve a pairing code to a session id (what the phone types in)."""
    wanted = code.strip().upper()
    for entry in STORE.list():
        sess = STORE.get(entry["id"])
        if sess is not None and hmac.compare_digest(sess.code.upper(), wanted):
            return {"session_id": sess.id, "label": sess.label,
                    "index": sess.index, "count": sess.count,
                    "done": sess.index >= sess.count}
    raise HTTPException(404, "no session with that code")


@app.get("/api/sessions/{sid}", dependencies=[Depends(require_auth)])
async def get_session(sid: str, role: str | None = None) -> dict:
    sess = require_session(sid)
    if role in ("display", "camera"):
        STORE.touch_role(sess, role)
    return sess.snapshot(role=role)


@app.post("/api/sessions/{sid}/join", dependencies=[Depends(require_auth)])
async def join(sid: str, payload: dict) -> dict:
    """Claim a role.  The camera needs the pairing code shown on the display."""
    sess = require_session(sid)
    role = str(payload.get("role", "")).lower()
    if role not in ("display", "camera"):
        raise HTTPException(400, "role must be display or camera")
    if role == "camera":
        code = str(payload.get("code", "")).strip().upper()
        if not hmac.compare_digest(code, sess.code.upper()):
            raise HTTPException(403, "wrong session code")
    STORE.touch_role(sess, role)
    snapshot = sess.snapshot(role=role)
    STORE.publish(sid, snapshot)
    return snapshot


@app.get("/api/sessions/{sid}/frame.png", dependencies=[Depends(require_auth)])
async def frame_png(sid: str, index: int | None = None) -> Response:
    sess = require_session(sid)
    idx = sess.index if index is None else int(index)
    if not 0 <= idx < sess.count:
        raise HTTPException(404, "frame out of range")
    data = await run_in_threadpool(STORE.frame_png, sess, idx)
    return Response(data, media_type="image/png", headers={
        # frames are immutable per (session, index)
        "Cache-Control": "public, max-age=31536000, immutable",
    })


@app.post("/api/sessions/{sid}/shots", dependencies=[Depends(require_auth)])
async def upload_shot(sid: str, file: UploadFile, index: int = Form(...),
                      device: str = Form(""), via: str = Form(""),
                      width: int = Form(0), height: int = Form(0),
                      ) -> dict:
    sess = require_session(sid)
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty upload")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"over {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    if not 0 <= index < sess.count:
        raise HTTPException(400, "index out of range")
    if index != sess.index:
        # double tap, or a slow upload racing another device: refuse instead of
        # filing the photo against a frame that is no longer on screen
        raise HTTPException(409, f"expected index {sess.index}")

    img = await run_in_threadpool(_decode_upload, raw)
    spec = sess.spec(index)
    label = None
    if AUTOLABEL:
        label = await run_in_threadpool(_label_image, img, spec)
    meta = {
        "device": device[:120], "via": via[:40],
        "width": int(width) or int(img.shape[1]),
        "height": int(height) or int(img.shape[0]),
        "label": label, "session": sid,
    }
    record = await run_in_threadpool(STORE.add_shot, sess, index, raw, meta, "jpg")
    return {"ok": True, "shot": {k: v for k, v in record.items() if k != "spec"},
            "state": sess.snapshot(role="camera")}


def _decode_upload(raw: bytes) -> "np.ndarray":
    """Validate and decode a phone upload (raises 400 on anything unusable)."""
    import cv2
    import numpy as np

    if raw[:2] != b"\xff\xd8":
        raise HTTPException(400, "not a JPEG — the phone should upload image/jpeg")
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "image could not be decoded")
    if min(img.shape[:2]) < 32:
        raise HTTPException(400, "image too small")
    return img


def _label_image(img, spec) -> dict:
    """Locate the frame in an already-decoded upload (pure OpenCV)."""
    try:
        out = label_photo(img, spec)
    except Exception as exc:            # pragma: no cover - defensive
        return {"found": False, "error": f"{type(exc).__name__}: {exc}"}
    out["image_size"] = [int(img.shape[1]), int(img.shape[0])]
    return out


@app.post("/api/sessions/{sid}/peek", dependencies=[Depends(require_auth)])
async def peek_frame(sid: str, file: UploadFile = File(...),
                     width: int = Form(0), height: int = Form(0)) -> dict:
    """Probe a candidate photo: does it contain the current frame's code?

    Does NOT store the shot. Used by the phone's autonomous capture mode to
    decide whether to fire the shutter. Returns a compact verdict so the
    phone can poll cheaply (downscaled JPEGs are fine).
    """
    sess = require_session(sid)
    if sess.done:
        return {"found": False, "reason": "session complete"}
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty upload")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"over {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    try:
        img = await run_in_threadpool(_decode_upload, raw)
    except HTTPException:
        raise
    spec = sess.spec(sess.index)
    try:
        out = await run_in_threadpool(label_photo, img, spec)
    except Exception as exc:        # pragma: no cover - defensive
        return {"found": False, "error": f"{type(exc).__name__}: {exc}"}
    found = bool(out.get("found"))
    verified = bool(out.get("verified"))
    return {
        "found": found,
        "verified": verified,
        "score": float(out.get("score", 0.0) or 0.0),
        "candidates": int(out.get("candidates", 0) or 0),
        "index": int(sess.index),
        "auto_ok": bool(found and verified),
    }


@app.post("/api/sessions/{sid}/reject", dependencies=[Depends(require_auth)])
async def reject(sid: str) -> dict:
    sess = require_session(sid)
    return await run_in_threadpool(STORE.reject_last, sess)


@app.post("/api/sessions/{sid}/skip", dependencies=[Depends(require_auth)])
async def skip(sid: str) -> dict:
    sess = require_session(sid)
    return await run_in_threadpool(STORE.skip, sess)


@app.post("/api/sessions/{sid}/seek", dependencies=[Depends(require_auth)])
async def seek(sid: str, payload: dict) -> dict:
    sess = require_session(sid)
    return await run_in_threadpool(STORE.seek, sess, int(payload.get("index", 0)))


@app.get("/api/sessions/{sid}/export", dependencies=[Depends(require_auth)])
async def export(sid: str) -> JSONResponse:
    sess = require_session(sid)
    data = await run_in_threadpool(STORE.export, sess)
    return JSONResponse(data)


@app.get("/api/sessions/{sid}/events", dependencies=[Depends(require_auth)])
async def events(sid: str, request: Request, role: str | None = None):
    """Server-Sent Events: the whole session state after every change.

    Snapshots (not deltas) are sent on connect and after every mutation, so a
    client that reconnects through a flaky mobile link cannot end up out of
    sync.  A comment line every 15 s keeps proxies from closing the stream.
    """
    sess = require_session(sid)
    if role in ("display", "camera"):
        STORE.touch_role(sess, role)

    async def stream():
        queue = STORE.subscribe(sid)
        try:
            first = sess.snapshot(role=role)
            yield _sse("state", first)
            last_ping = time.time()
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    item = None
                if item is not None:
                    yield _sse(item["event"], item["data"])
                    last_ping = time.time()
                elif time.time() - last_ping > 14.0:
                    yield ": ping\n\n"
                    last_ping = time.time()
        finally:
            STORE.unsubscribe(sid, queue)

    # `Connection` is a hop-by-hop header and is illegal in HTTP/2 (which is what
    # Traefik speaks to a browser): proxies may drop the whole response.  The
    # no-transform hint plus X-Accel-Buffering is what actually keeps a buffering
    # proxy from holding the stream.
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform",
                                      "X-Accel-Buffering": "no"})


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.websocket("/ws/sessions/{sid}")
async def ws_session(websocket: WebSocket, sid: str, role: str | None = None):
    token = websocket.cookies.get(COOKIE_NAME, "")
    if not _verify(token):
        await websocket.close(code=4401)
        return
    try:
        sess = require_session(sid)
    except HTTPException:
        await websocket.close(code=4404)
        return
    if role in ("display", "camera"):
        STORE.touch_role(sess, role)

    await websocket.accept()
    queue = STORE.subscribe(sid)
    try:
        await websocket.send_json({"event": "state", "data": sess.snapshot(role=role)})
        last_ping = time.time()
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                item = None
            if item is not None:
                await websocket.send_json({"event": item["event"], "data": item["data"]})
                last_ping = time.time()
            elif time.time() - last_ping > 14.0:
                await websocket.send_json({"event": "ping", "data": {}})
                last_ping = time.time()
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass
    finally:
        STORE.unsubscribe(sid, queue)


# --------------------------------------------------------------------------- #
# data export helpers used by scripts/captures_to_dataset.py
# --------------------------------------------------------------------------- #
@app.get("/api/sessions/{sid}/shots/{name}", dependencies=[Depends(require_auth)])
async def shot_file(sid: str, name: str) -> FileResponse:
    sess = require_session(sid)
    safe = Path(name).name
    path = STORE.session_dir(sess.id) / "shots" / safe
    if not path.exists():
        raise HTTPException(404, "no such shot")
    media = "image/jpeg" if path.suffix in (".jpg", ".jpeg") else "application/json"
    return FileResponse(path, media_type=media)


@app.get("/api/qr.svg", dependencies=[Depends(require_auth)])
async def qr_svg(data: str, scale: int = 8, dark: str = "#000000",
                 light: str = "#ffffff") -> Response:
    """QR code as SVG, for the pairing link the phone scans off the laptop.

    ``segno`` is a pure-Python dependency of the web image; if it is missing the
    display page simply falls back to typing the session code.
    """
    try:
        import io

        import segno
    except ImportError:                 # pragma: no cover
        raise HTTPException(503, "segno not installed")
    scale = max(2, min(24, int(scale)))
    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="svg", scale=scale,
                                     dark=dark, light=light, border=2)
    return Response(buf.getvalue(), media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=600"})
