# Engineering Audit — `yolo-docker` (Optical Modem Suite)

**Date:** 2026-10-29 · **Branch:** `arena/d6b4426e-yolo-docker` @ `503440e`
**Scope:** 7,909 statements of Python across `optical_modem/`, plus the React/Vite
frontend, Dockerfile, and `docker-compose.yml`.

**Method:** full read of every non-generated source file, plus an executed test
suite (`276 passed, 13 skipped`), a coverage run, a CPU profile of the decode
path, and six purpose-built proof-of-concept scripts against a live uvicorn
server. Every finding marked **MEASURED** was reproduced in this session; the
rest are static findings verified by reading the code.

**Baseline health:** the test suite passes and the DSP/protocol layers are
genuinely well covered (`protocol/` 90–100%, `phy/channel.py` 99%). The problems
below are concentrated in the **web/serving layer**, which is where the newest
code lives and where coverage is thinnest (`suite/app.py` 57%, `suite/s2c.py`
50%, `suite/datasets.py` 12%, `suite/tasks.py` 0%).

---

## Phase 1 — Critical Fixes & Code Health

### Severity summary

| # | Issue | Severity | Evidence |
|---|-------|----------|----------|
| 1 | SSE streams exhaust the thread pool → full app DoS | **HIGH** | MEASURED |
| 2 | Unauthenticated reflected XSS in `finder/{shape}.svg` | **HIGH** | MEASURED (PoC) |
| 3 | Unvalidated `cells` param → superlinear CPU DoS | **HIGH** | MEASURED |
| 4 | Login throttle bypassed by `X-Forwarded-For` + unbounded leak | **HIGH** | MEASURED |
| 5 | `S2C._rx()` unlocked single-slot cache → YOLO reload storm | **HIGH** | static |
| 6 | Blocking I/O on the event loop | **HIGH** | static + measured |
| 7 | `Registry.disk()` / `quality.inspect()` on every poll | **MEDIUM** | MEASURED |
| 8 | `refine_rotation` re-warps 5× for the same theta | **MEDIUM** | MEASURED (78%) |
| 9 | Paused job (SIGSTOP) wedges the pipeline forever | **MEDIUM** | static |
| 10 | Missing CSP / frame-ancestors / HSTS headers | **MEDIUM** | static |
| 11 | Auth silently disabled outside Docker | **MEDIUM** | MEASURED (PoC) |
| 12 | Bearer token in query string + `sessionStorage` | **MEDIUM** | MEASURED (PoC) |
| 13 | Dead code in `FrameLayout.ring_values` | **LOW** | static |
| 14 | TOCTOU in `upload_shot` index check | **LOW** | static |
| 15 | Path validation duplicated three ways | **LOW** | static |
| 16 | Documentation drift (207 vs 289 tests) | **LOW** | MEASURED |
| 17 | ~17 MB of binaries tracked in git | **LOW** | MEASURED |

---

### H-1 · SSE streams exhaust the thread pool — full application DoS

**Location:** `optical_modem/suite/app.py:719` (`event_stream`)

**The issue.** `event_stream` blocks a **thread-pool worker for up to 15 seconds
per connection** waiting on a `queue.Queue`:

```python
event = await run_in_threadpool(q.get, True, 15.0)
```

FastAPI runs every `def` (non-`async`) endpoint on that same AnyIO thread pool,
whose default limit is **40 threads**. `useEventStream` is mounted in `Layout`
*and* in every page that uses it, so a single browser tab holds **2 streams**.
Twenty open tabs — or twenty phones with the PWA installed — saturate the pool,
and *every* sync endpoint (`/api/suite/status`, `/api/jobs`, `/api/suite/datasets`,
`/api/suite/models`, …) queues behind them.

**MEASURED** against a live uvicorn instance on this branch:

```
baseline /api/me:                        5 ms
open SSE streams:                        45
/api/me with 45 SSE streams open:        3 ms     (async handler — unaffected)
/api/jobs with 45 SSE streams open:  11887 ms     (sync handler — BLOCKED)
```

Note the split: `async def` handlers stayed fast because they never touch the
thread pool. That is the signature of exactly this bug, and it means the app
fails *partially and confusingly* before it fails completely — some buttons keep
working while the dashboard hangs.

**The fix.** Never park a worker thread on an idle queue. Subscribe the queue to
the event loop instead, keeping the blocking wait only as the mechanism that
already exists inside `LocalBus` (a plain callback invoked from `publish`):

```python
# BEFORE — app.py:700-727
q: _queue.Queue = _queue.Queue(maxsize=200)

def on_event(event: dict) -> None:
    try:
        q.put_nowait(event)
    except _queue.Full:
        pass

unsubscribe = BUS.subscribe(on_event)

async def stream():
    try:
        backlog = "".join(fmt(e) for e in BUS.recent(20))
        yield "retry: 3000\n\n" + backlog
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await run_in_threadpool(q.get, True, 15.0)   # <-- holds a thread
            except _queue.Empty:
                yield ": keep-alive\n\n"
                continue
            yield fmt(event)
    finally:
        unsubscribe()
```

```python
# AFTER — an asyncio.Queue fed from the bus callback: zero threads held
q: asyncio.Queue = asyncio.Queue(maxsize=200)
loop = asyncio.get_running_loop()

def on_event(event: dict) -> None:
    try:
        loop.call_soon_threadsafe(q.put_nowait, event)
    except RuntimeError:            # loop already closed
        pass

unsubscribe = BUS.subscribe(on_event)

async def stream():
    try:
        yield "retry: 3000\n\n" + "".join(fmt(e) for e in BUS.recent(20))
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(q.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            yield fmt(event)
    finally:
        unsubscribe()
```

Two supporting changes:

1. **Cap concurrent streams** so a client cannot open hundreds anyway — an
   `asyncio.Semaphore(32)` around the handler, returning `429` when exhausted.
2. **Collapse to one stream per tab.** `useEventStream` should hand out a single
   shared `EventSource` (module-level refcount) instead of one per mounted
   component. That halves the natural ceiling pressure immediately.

**Priority: fix immediately.**

---

### H-2 · Unauthenticated reflected XSS in the finder thumbnail endpoint

**Location:** `optical_modem/suite/app.py:480-517` (`finder_thumb`)

**The issue.** `fg` and `bg` are attacker-controlled strings interpolated
**unescaped** into SVG attributes. The endpoint is explicitly unauthenticated
(its docstring says so). `size` is typed `int` so FastAPI coerces it safely, but
the colour parameters are raw.

```python
svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" '
       f'height="{size}" viewBox="0 0 {size} {size}">'
       f'<rect width="{size}" height="{size}" fill="{bg}" rx="8"/>'   # <-- bg unescaped
       ...)
```

**MEASURED** — a live request on this branch:

```
GET /api/suite/finder/ring.svg?bg=%23fff%22%20onload%3D%22alert(document.domain)
→ 200 OK
<svg ...><rect width="72" height="72" fill="#fff" onload="alert(document.domain)" rx="8"/>
```

The attribute is escaped and an `onload` handler is injected. Rendered through
an `<img>` tag the payload is inert, but an attacker who links a victim straight
to the SVG URL gets a **top-level SVG document, which executes script** in the
site's origin — with the session cookie in scope. Combined with the session
token also being readable from `sessionStorage` (H-12), that is full account
takeover from one link.

**The fix.** Never build markup from user input. Either validate colours against
a strict pattern, or escape them:

```python
# BEFORE
@app.get("/api/suite/finder/{shape}.svg")
def finder_thumb(shape: str, size: int = 72, fg: str = "#e9f2f7",
                 bg: str = "#0b0d10") -> Response:
```

```python
# AFTER — reject anything that is not a colour literal
import re
_COLOR = re.compile(r"^#[0-9a-fA-F]{3,8}$|^[a-zA-Z]{3,20}$")

def _css_color(value: str, default: str) -> str:
    value = (value or "").strip()
    return value if _COLOR.match(value) else default

@app.get("/api/suite/finder/{shape}.svg")
def finder_thumb(shape: str, size: int = 72, fg: str = "#e9f2f7",
                 bg: str = "#0b0d10") -> Response:
    fg, bg = _css_color(fg, "#e9f2f7"), _css_color(bg, "#0b0d10")
    size = max(8, min(int(size), 512))          # bound the loop below too
```

Also add `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'`
to SVG responses (see M-10) so a future interpolation mistake is not exploitable.

**Priority: fix immediately.**

---

### H-3 · Unvalidated `cells` parameter → trivial CPU exhaustion

**Location:** `optical_modem/suite/app.py:522-544` (`palette_table`)

**The issue.** `cells` is passed straight into `FrameLayout(cells=int(cells))`
with no bound. Frame construction is superlinear in `cells`.

**MEASURED** on this branch (one authenticated GET each):

```
GET /api/suite/palette?cells=32     -> (200, 3801 bytes,      21 ms)
GET /api/suite/palette?cells=512    -> (200, 3849 bytes,    2568 ms)
GET /api/suite/palette?cells=4096   -> timed out after 30 s
```

Each of those requests permanently occupies a thread-pool worker. Per H-1 the
pool has 40 threads, so 40 concurrent requests of this shape take the entire
application down — and unlike H-1 this needs no SSE, just a logged-in user (or
anyone at all, if `APP_PASSWORD` is unset per M-11).

**The fix.** Bound every integer that sizes work. FastAPI's `Query` already does
this for the endpoints that got it right (`dataset_quality_report` uses
`Query(0, ge=0)`); `palette_table` simply missed it:

```python
# BEFORE
def palette_table(cells: int = 32, ecc: str = "rs16") -> dict:
```

```python
# AFTER
from fastapi import Query

def palette_table(cells: int = Query(32, ge=8, le=256),
                  ecc: str = Query("rs16", pattern="^(none|rs8|rs16|rs32|rs64|ldpc12|ldpc23)$")) -> dict:
```

Audit the rest of the surface for the same omission while you are there:
`recent_events(limit)`, `jobs(kind, limit)`, `run_curve(limit)` all take
unbounded ints (lower impact, but the same class of bug). A single shared
`BoundedInt = Annotated[int, Query(ge=..., le=...)]` type alias keeps this from
regressing.

**Priority: fix immediately.**

---

### H-4 · Login throttle bypassed via `X-Forwarded-For`, and an unbounded leak

**Location:** `optical_modem/capture/app.py:108`, `275-283`

**The issue.** Two problems in one six-line block.

```python
_ATTEMPTS: dict[str, list[float]] = {}      # line 108

ip = request.client.host if request.client else "?"
now = time.time()
hits = [t for t in _ATTEMPTS.get(ip, []) if now - t < 60.0]
if len(hits) >= 10:
    raise HTTPException(429, "too many attempts, wait a minute")
if not _password_ok(...):
    _ATTEMPTS[ip] = hits + [now]            # line 280 — never removed on failure
```

1. **Bypass.** uvicorn runs with `--proxy-headers --forwarded-allow-ips '*'`
   (Dockerfile `CMD`, `docker-compose.yml`), so `request.client.host` **is** the
   client-supplied `X-Forwarded-For` value. An attacker rotates that header and
   the 10-per-minute limit never engages.
2. **Leak.** An entry is only pruned when *that same IP* attempts again, and only
   popped on success. An IP that fails once and never returns keeps its slot
   forever — unbounded, attacker-controlled growth.

**MEASURED** against a live uvicorn instance with proxy headers enabled:

```
1) entries retained in _ATTEMPTS after 300 one-shot failures from 300 distinct IPs: 300
2) 30 rapid wrong-password attempts, XFF rotated each time -> 429 count:  0/30   (bypassed)
3) 30 rapid wrong-password attempts, IP fixed             -> 429 count: 20/30   (works)
```

With a single shared password and no per-user accounts (M-12), an unthrottled
login endpoint is an online brute-force oracle.

**The fix.** Use a bounded, self-evicting store; add global as well as per-IP
limiting; add a small artificial delay. The project already depends on Redis for
production (and degrades cleanly to in-process), so express the limit through the
existing `bus` abstraction rather than a bare dict:

```python
# BEFORE
_ATTEMPTS: dict[str, list[float]] = {}

ip = request.client.host if request.client else "?"
now = time.time()
hits = [t for t in _ATTEMPTS.get(ip, []) if now - t < 60.0]
if len(hits) >= 10:
    log.warning("login rate-limited ip=%s", ip)
    raise HTTPException(429, "too many attempts, wait a minute")
if not _password_ok(str(payload.get("password", ""))):
    _ATTEMPTS[ip] = hits + [now]
```

```python
# AFTER — bounded LRU + global cap + constant-ish work
import asyncio
from collections import OrderedDict

_MAX_TRACKED_IPS = 10_000
_ATTEMPTS: "OrderedDict[str, list[float]]" = OrderedDict()
_ATTEMPTS_LOCK = asyncio.Lock()

def _throttled(ip: str) -> bool:
    now = time.time()
    hits = [t for t in _ATTEMPTS.get(ip, []) if now - t < 60.0]
    if hits:
        _ATTEMPTS[ip] = hits
        _ATTEMPTS.move_to_end(ip)
    else:
        _ATTEMPTS.pop(ip, None)
    while len(_ATTEMPTS) > _MAX_TRACKED_IPS:      # bound the leak
        _ATTEMPTS.popitem(last=False)
    return len(hits) >= 10 or len(_ATTEMPTS) > 2_000   # per-IP + global
```

Three more things to change alongside it:

* **Do not trust `X-Forwarded-For` for security decisions** unless the peer is a
  known proxy. `net.trust_forwarded()` is a global on/off switch; add
  `TRUSTED_PROXY_CIDRS` (default `10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`) and
  only honour the forwarded IP when `request.client.host` is inside it.
* **Move the throttle into a dependency** shared by `/api/login` so a future
  second login route cannot forget it.
* **Log the authenticated outcome with the resolved IP** — right now a successful
  login and a throttled one are both just `login OK`/`login rate-limited`, with
  no record of the real source.

**Priority: fix immediately.**

---

### H-5 · `S2C._rx()` has no lock and a single cache slot → YOLO reload storm

**Location:** `optical_modem/suite/s2c.py:280-290`

**The issue.** The receiver cache is one slot, keyed by `(transfer_id, weights)`,
and is read-modify-written **without holding `self._lock`**:

```python
def _rx(self, tr: Transfer):
    key = (tr.id, self.weights)
    if self._receiver is None or self._receiver_key != key:
        self._receiver = HybridReceiver(tr.cfg, mode=self.decode_mode,
                                        yolo_weights=self.weights,
                                        yolo_imgsz=448)
        self._receiver_key = key
    return self._receiver
```

Constructing a `HybridReceiver` runs `YOLO(weights)` (`detect/yolo.py:57`),
which loads the model from disk and initialises torch. Per `AGENTS.md` that is
**~350 MB RSS**. Consequences:

* Two transfers decoded in alternation — the normal case when a laptop transmits
  and a phone receives, or two people use `/s2c` — **reload the model on every
  single frame**.
* Because there is no lock, two concurrent requests can both observe a miss and
  both build a receiver; the last writer wins and the loser's model is garbage.
* `/s2c` is the flagship feature, and `S2C.decode` runs in the thread pool, so
  this is also a fast route to the thread-pool exhaustion of H-1.

**The fix.** Guard the cache and key it by configuration, not by transfer id —
the receiver does not actually depend on the transfer, only on `cfg` + weights +
mode. That makes alternating transfers free:

```python
# BEFORE
def _rx(self, tr: Transfer):
    key = (tr.id, self.weights)
    if self._receiver is None or self._receiver_key != key:
        self._receiver = HybridReceiver(...)
        self._receiver_key = key
    return self._receiver
```

```python
# AFTER — locked, and keyed on what the receiver actually depends on
def _rx(self, tr: Transfer):
    key = (tr.cfg.modulation, tr.cfg.ecc, tr.cfg.cell_px,
           tr.cfg.layout.cells, tr.cfg.layout.cps, tr.cfg.layout.ring,
           tr.cfg.layout.header_rows, self.weights, self.decode_mode)
    with self._lock:
        if self._receiver is None or self._receiver_key != key:
            self._receiver = HybridReceiver(tr.cfg, mode=self.decode_mode,
                                            yolo_weights=self.weights,
                                            yolo_imgsz=448)
            self._receiver_key = key
        return self._receiver
```

Optionally make it a small LRU (`OrderedDict`, 3 entries) so a couple of
configurations can stay warm. **Priority: fix immediately.**

---

### H-6 · Blocking I/O on the event loop

**Location:** `optical_modem/suite/app.py:1225` (`start_training`), `:1313`
(`extend_run`), `:1359` (`resume_run`); `optical_modem/capture/app.py:147-159`
(`_verify` → `_token_revoked`).

**The issue.** Several `async def` handlers perform synchronous filesystem and
database work directly:

```python
# app.py:1225 — async def, but every line below blocks the whole event loop
async def start_training(payload: dict) -> dict:
    yaml_path = _require_yaml(dataset)          # disk stat
    ds_path = _dataset_path(dataset)
    _recover_dataset_edits(ds_path)             # walks .edit-journal, takes a lock
    ...
    device = "0" if describe_env().get("cuda") else "cpu"   # reads /proc/meminfo
```

Meanwhile `_recover_dataset_edits` takes `dataset_crop._LOCK`, which the crop
endpoints hold for the duration of an image rewrite — so one user cropping a
photo can stall every request in the process.

The worst instance is the **auth path**, because it runs on literally every
authenticated request. `_verify` → `_token_revoked` opens a **new SQLite
connection and runs a query per request**:

```python
# capture/app.py:127-131
def _token_revoked(token: str) -> bool:
    digest = hashlib.sha256(token.encode()).hexdigest()
    with closing(_revocation_store()) as db:          # sqlite3.connect(...) — blocking
        return db.execute("SELECT 1 FROM revoked WHERE digest=? AND expires>?",
                          (digest, time.time())).fetchone() is not None
```

**MEASURED**: 20 authenticated requests → 20 SQLite connections opened. Under
concurrent load that is 20 connection setups serialised on the event loop, on
top of SQLite's write locking.

**The fix.** Two independent changes.

*(a) Make revocation checks cheap and non-blocking.* Keep the revocation list in
memory, bucketed by expiry, and consult SQLite only for tokens whose bucket has
not been swept:

```python
# BEFORE — capture/app.py:127
def _token_revoked(token: str) -> bool:
    digest = hashlib.sha256(token.encode()).hexdigest()
    with closing(_revocation_store()) as db:
        return db.execute(...).fetchone() is not None
```

```python
# AFTER — one connection on boot, then an in-memory set (revocations are rare)
_REVOKED: set[str] = set()
_REVOKED_LOCK = threading.Lock()

def _load_revocations() -> None:
    with closing(_revocation_store()) as db:
        rows = db.execute("SELECT digest FROM revoked WHERE expires>?",
                          (time.time(),)).fetchall()
    with _REVOKED_LOCK:
        _REVOKED.clear()
        _REVOKED.update(r[0] for r in rows)

def _token_revoked(token: str) -> bool:
    digest = hashlib.sha256(token.encode()).hexdigest()
    with _REVOKED_LOCK:
        return digest in _REVOKED
```

Call `_load_revocations()` once at startup and again from `_revoke_token` (which
already holds the write path). If you would rather not change the storage model,
the minimum viable fix is to make the dependency synchronous so Starlette
dispatches it to the thread pool:

```python
# BEFORE
async def require_auth(request: Request) -> None:
    cand = _auth_candidate(request)
    if not _verify(cand):
        raise HTTPException(401, "login required")

# AFTER — FastAPI runs sync dependencies in the thread pool
def require_auth(request: Request) -> None:
    cand = _auth_candidate(request)
    if not _verify(cand):
        raise HTTPException(401, "login required")
```

*(b) Move the blocking bodies of the async handlers into the thread pool:*

```python
# BEFORE
async def start_training(payload: dict) -> dict:
    yaml_path = _require_yaml(dataset)
    ds_path = _dataset_path(dataset)
    _recover_dataset_edits(ds_path)
    ...
    job = _start_job(...)

# AFTER
async def start_training(payload: dict) -> dict:
    def _prepare() -> tuple[Path, Path]:
        yaml_path = _require_yaml(dataset)
        ds_path = _dataset_path(dataset)
        _recover_dataset_edits(ds_path)
        return yaml_path, ds_path
    yaml_path, ds_path = await run_in_threadpool(_prepare)
    ...
    job = await run_in_threadpool(_start_job, ...)
```

**Priority: fix immediately** — H-1 and H-6 compound, since a thread-starved
pool and a blocked loop produce the same symptom by different routes.

---

### M-7 · Every dashboard poll re-walks the entire data volume

**Location:** `optical_modem/suite/registry.py:64-79` (`disk`), `:116-132`
(`list_datasets`), `optical_modem/suite/quality.py:16` (`inspect`); driven from
`frontend/src/components/Layout.tsx:51` (8 s), `frontend/src/pages/Home.tsx:54`
(6 s), `frontend/src/pages/Datasets.tsx:107` (1.5 s), `Jobs.tsx:37` (2.5 s).

**The issue.** `Registry.disk()` runs `os.walk` over datasets, runs, models and
sessions and `stat()`s every file. `quality.inspect()` **SHA-256s every image**.
Both are called from request handlers whose results the UI discards and refetches
on a timer. `Layout` polls `/suite/progress` and `/suite/status` every 8 s;
`Home` adds three more endpoints every 6 s. Each `/suite/status` call also makes
five separate database round-trips (`DB.list_projects()` three times, `counts()`,
…).

**MEASURED** on a modest 12,000-file dataset of empty files:

```
files: 12000
list_datasets():    40 ms
disk() os.walk:     34 ms
quality.inspect(): 936 ms
```

Those numbers are with **zero-byte** files. With real 200 KB JPEGs,
`quality.inspect` reads and hashes ~1.2 GB — tens of seconds — and it runs on the
Datasets page while a 1.5 s poll is active.

**The fix.** Three changes, in order of value:

1. **Make quality inspection a job, not a request.** It is already a pure
   function over a directory; run it through the existing `JobManager` and cache
   the result keyed by `(dataset, max(mtime))`, and serve the cached report.
2. **Cache `disk()`** with a short TTL — nothing needs sub-second accuracy:

```python
# BEFORE — registry.py:64
def disk(self) -> dict:
    ...
    used = 0
    for path in (self.datasets, self.runs, self.models, self.sessions):
        for dirpath, _dirs, files in os.walk(path):
            for name in files:
                used += os.path.getsize(os.path.join(dirpath, name))
    return {"free_bytes": free, "total_bytes": total, "used_bytes": used}
```

```python
# AFTER
_DISK_TTL = 30.0

def disk(self) -> dict:
    now = time.time()
    if self._disk_cache and now - self._disk_cache[0] < self._DISK_TTL:
        return {**self._disk_cache[1], "cached": True}
    ...                                   # existing walk
    out = {"free_bytes": free, "total_bytes": total, "used_bytes": used}
    self._disk_cache = (now, out)
    return {**out, "cached": False}
```

3. **Collapse the poll endpoints.** `_stage_state()` and `progress()` overlap
   heavily; one `/api/suite/state` response (which already exists at
   `app.py:659`) can serve both the dashboard rail and the nav. That halves
   requests per tick before any server-side work.

**Priority: high — do this in the same sprint as H-1.**

---

### M-8 · `refine_rotation` repeats an identical warp five times

**Location:** `optical_modem/phy/classical.py:514-552` (`refine_rotation`), inner loop at `:547-552`

**The issue.** The inner loop varies only `(ox, oy)` — `theta` is constant — but
`evaluate()` re-runs `getRotationMatrix2D` + `warpAffine` + `cv2.integral` for
every offset anyway:

```python
for theta in np.linspace(base_theta - span, base_theta + span, n):
    for ox, oy in ((0.0, 0.0), (0.06, 0.0), (-0.06, 0.0),
                   (0.0, 0.06), (0.0, -0.06)):
        obj, integ = evaluate(theta, best[0] + ox, best[1] + oy, best[2])
```

With `rot_stages=((1.2,5),(0.4,3))` that is `8 thetas × 5 offsets + 1 = 41`
evaluations per attempt, of which only **9** do distinct work.

**MEASURED** on an 864×864×3 canonical image (36 cells × 24 px/cell):

```
warpAffine 2.69 ms, cv2.integral 4.50 ms  -> 7.19 ms per evaluate()
  current : 41 x 7.19 = 295 ms per attempt
  hoisted : ~9 x 7.19 =  65 ms per attempt  (78% saved)
```

End-to-end, a decode on this branch takes **1.41 s** for a 720 px frame
(`contours` is only 46 ms — the remaining ~1.36 s is the ring/pose search).
`refine_rotation` accounts for 541 ms of it. Fixing this is roughly a **35–40 %
end-to-end decode speedup for a ~10-line change**, and it directly raises the
frame rate the `/s2c` camera scanner can sustain.

**The fix.** Compute the rotated image and its integral once per theta:

```python
# BEFORE — classical.py:545-552 (inner loop)
for span, n in rot_stages:
    base_theta = best[3]
    for theta in np.linspace(base_theta - span, base_theta + span, n):
        for ox, oy in ((0.0, 0.0), (0.06, 0.0), (-0.06, 0.0),
                       (0.0, 0.06), (0.0, -0.06)):
            obj, integ = evaluate(theta, best[0] + ox, best[1] + oy, best[2])
            if obj < best[4]:
                best = (best[0] + ox, best[1] + oy, best[2], float(theta), obj)
                best_integ = integ
```

```python
# AFTER — rotate + integrate once per theta, then score the 5 offsets
for span, n in rot_stages:
    base_theta = best[3]
    for theta in np.linspace(base_theta - span, base_theta + span, n):
        if abs(theta) < 1e-9:
            integ = integral_of(canon)
        else:
            M = cv2.getRotationMatrix2D(ctr, float(theta), 1.0)
            rot = cv2.warpAffine(canon, M, (w, h), flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_REPLICATE)
            integ = integral_of(rot)
        for ox, oy in ((0.0, 0.0), (0.06, 0.0), (-0.06, 0.0),
                       (0.0, 0.06), (0.0, -0.06)):
            obj, _ = self._ring_cost(integ, cells, K, margin, mod, rr, cc,
                                     rcol, cal, best[0] + ox, best[1] + oy,
                                     best[2])
            if obj < best[4]:
                best = (best[0] + ox, best[1] + oy, best[2], float(theta), obj)
                best_integ = integ
```

Add a regression test asserting the returned `(dx, dy, scale, theta)` is byte-
identical to the current implementation for a fixed seed — the optimisation must
not move the optimum, only reach it faster.

**Priority: high** — this is the single cheapest performance win in the codebase
and it lands on the product's headline feature.

---

### M-9 · A paused job wedges the pipeline indefinitely

**Location:** `optical_modem/suite/app.py:1546-1563` (`pause_job`)

**The issue.** Pause sends `SIGSTOP` to the child's process group:

```python
os.killpg(os.getpgid(proc.pid), signal.SIGSTOP)
```

Because `start_new_session=True`, that stops the child only — but the job's
`_monitor` thread stays blocked in `proc.wait()`, so the job remains `live`
forever. With `SUITE_MAX_JOBS=1` (the documented default for a 4-core box) **one
paused job blocks every subsequent job** with no timeout and no auto-resume.
`cancel()` sets `status=CANCELLED`, then waits 10 s for a process that cannot
handle `SIGTERM` while stopped, before falling through to `SIGKILL` — so cancel
works, but only after a 10-second dead wait per cancellation.

**The fix.** Track pause state explicitly and bound it:

```python
# BEFORE
@app.post("/api/jobs/{jid}/pause")
def pause_job(jid: str) -> dict:
    ...
    os.killpg(os.getpgid(proc.pid), signal.SIGSTOP)
    with JOBS._lock:
        job_.message = "paused"
```

```python
# AFTER
PAUSE_GRACE_SECONDS = 900          # 15 min: pause is for freeing CPU briefly

@app.post("/api/jobs/{jid}/pause")
def pause_job(jid: str) -> dict:
    ...
    os.killpg(os.getpgid(proc.pid), signal.SIGSTOP)
    with JOBS._lock:
        job_.message = "paused"
        job_.paused_at = time.time()          # new field on Job
        JOBS._save(job_)
    # a stopped process cannot handle SIGTERM: resume first, then cancel
    ...
```

and in `JobManager.cancel`, resume before signalling:

```python
# BEFORE — jobs.py, cancel()
if proc is not None and proc.poll() is None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except OSError:
        proc.terminate()

# AFTER
if proc is not None and proc.poll() is None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGCONT)   # a stopped proc ignores SIGTERM
    except OSError:
        pass
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except OSError:
        proc.terminate()
```

Also have `JobManager.list()` (or the poller) auto-resume any job paused longer
than `PAUSE_GRACE_SECONDS`, and surface "paused" in `describe()` so the UI can
show it rather than reporting a live job that is making no progress.

---

### M-10 · Missing security response headers

**Location:** `optical_modem/suite/app.py:188-194` (`_security_headers`)

**The issue.** Only two headers are set:

```python
response.headers.setdefault("Referrer-Policy", "no-referrer")
response.headers.setdefault("X-Content-Type-Options", "nosniff")
```

Absent: **Content-Security-Policy**, **frame-ancestors**, **HSTS**. The app
serves user-uploaded JPEGs, generated SVG (H-2), and a PWA shell — a CSP is the
control that would have contained the H-2 XSS rather than leaving it exploitable.

**The fix.**

```python
# BEFORE
@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response
```

```python
# AFTER
_SVG_PATH = re.compile(r"\.svg$")

@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
        "connect-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    if net.is_secure(request):
        response.headers.setdefault("Strict-Transport-Security",
                                    "max-age=31536000; includeSubDomains")
    if _SVG_PATH.search(request.url.path):
        response.headers["Content-Security-Policy"] = \
            "default-src 'none'; style-src 'unsafe-inline'"
    return response
```

Note `X-Frame-Options` is redundant with `frame-ancestors` for modern browsers
but covers older ones; the inline `style-src` is needed because the SPA and the
SVGs use inline styles. Verify against the PWA shell and the `/display` and
`/camera` pages before shipping — those are the two pages most likely to need a
directive relaxed.

---

### M-11 · Authentication is silently disabled outside Docker

**Location:** `optical_modem/capture/app.py:80-84`, `:154-157`

**The issue.** The password check short-circuits when `APP_PASSWORD` is empty:

```python
def _password_ok(given: str) -> bool:
    if not APP_PASSWORD:
        # no password configured: local/dev use only (the UI says so)
        return True
    return hmac.compare_digest(given.strip(), APP_PASSWORD)
```

`REQUIRE_PASSWORD=1` is set in the **Dockerfile** `ENV`, which is what makes this
safe in the shipped image. But the documented local workflow in `AGENTS.md` runs
uvicorn directly:

```bash
python -m uvicorn optical_modem.suite.app:app --host 127.0.0.1 --port 8200 --workers 1
```

with `REQUIRE_PASSWORD` unset → default `"0"` → **no guard at all**. And the
frontend actively exploits it (`useAuth.tsx:38`: if `password_set` is false, call
`api.login("")`).

**MEASURED** with `APP_PASSWORD` unset:

```
1) login with no APP_PASSWORD configured -> 200 True
   token issued: 1793276816.1116498.2ce96e7624518309cea11 ...
```

Any `uvicorn --host 0.0.0.0` on a LAN — the natural way to test the phone camera
workflow — publishes a wide-open instance. The failure mode is silent: the UI
renders normally, just without a login prompt.

**The fix.** Fail closed unless the operator explicitly opts into open mode, and
make the opt-in conditional on a loopback bind:

```python
# BEFORE — capture/app.py:80
if os.environ.get("REQUIRE_PASSWORD", "0").lower() in ("1", "true", "yes", "on") and not APP_PASSWORD:
    raise RuntimeError("APP_PASSWORD must be configured when REQUIRE_PASSWORD is enabled")

# AFTER
ALLOW_ANONYMOUS = os.environ.get("ALLOW_ANONYMOUS", "").lower() in ("1", "true", "yes", "on")
if not APP_PASSWORD and not ALLOW_ANONYMOUS:
    raise RuntimeError(
        "APP_PASSWORD is required. Set it to a strong value, or set "
        "ALLOW_ANONYMOUS=1 to run an explicitly open, loopback-only dev instance.")
```

Then bind-check in the same place, and have `/api/me` report
`"auth_mode": "anonymous"` so the UI can show a persistent banner instead of
silently auto-logging in. Update `AGENTS.md` to export `ALLOW_ANONYMOUS=1` in
the dev snippet.

---

### M-12 · Bearer token in the query string, and in `sessionStorage`

**Location:** `optical_modem/capture/app.py:142-145` (`_auth_candidate`);
`frontend/src/api/client.ts:26-40`, `:45-55`

**The issue.** The token is accepted from three places and stored in
`sessionStorage`:

```python
def _auth_candidate(request: Request) -> str:
    return (request.cookies.get(COOKIE_NAME, "") or request.headers.get("x-om-token", "")
            or request.query_params.get("_tok", ""))
```

The `_tok` **query parameter** puts a 30-day bearer credential into: proxy access
logs, browser history, and (absent `Referrer-Policy`) the `Referer` header. The
frontend appends it to every URL when the API is cross-origin
(`resolveApiUrl(..., true)` — used for `eventStreamUrl`, `datasetImageUrl`,
`s2cFrameUrl`, `exportManifestUrl`, and more), so on a split-origin deployment
*every image request* carries the token in the URL.

Separately, `sessionStorage` is readable by any script on the origin, so it
offers no protection against XSS — which is exactly what H-2 provides. The
cookie is already `HttpOnly`; the JS-readable copy undoes that.

**MEASURED**:

```
2) ?_tok=<token> accepted -> 200 {'authenticated': True, ...}
```

**The fix.** Prefer the `HttpOnly` cookie; keep the header for non-browser
clients; drop the query parameter.

```python
# BEFORE — capture/app.py:142
def _auth_candidate(request: Request) -> str:
    return (request.cookies.get(COOKIE_NAME, "") or request.headers.get("x-om-token", "")
            or request.query_params.get("_tok", ""))
```

```python
# AFTER — no credential in the URL (it leaks to logs, history and Referer)
def _auth_candidate(request: Request) -> str:
    return request.cookies.get(COOKIE_NAME, "") or request.headers.get("x-om-token", "")
```

On the client, stop persisting the token at all when the API is same-origin:

```ts
// BEFORE — client.ts:26-40
export function getAuthToken(): string {
  try { return sessionStorage.getItem(TOKEN_STORAGE_KEY) || ""; } catch { return ""; }
}
export function setAuthToken(tok?: string | null) {
  try {
    if (tok) sessionStorage.setItem(TOKEN_STORAGE_KEY, tok);
    else sessionStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {}
}
```

```ts
// AFTER — the HttpOnly cookie already authenticates; keep the token in memory
let _token: string | null = null;
const CROSS_ORIGIN = (() => {
  try { return new URL(BASE, window.location.href).origin !== window.location.origin; }
  catch { return false; }
})();

export function getAuthToken(): string {
  return CROSS_ORIGIN ? (_token ?? "") : "";
}
export function setAuthToken(tok?: string | null) {
  _token = CROSS_ORIGIN && tok ? tok : null;   // memory only, never storage
}
```

If cross-origin is genuinely required, mint a **short-lived** (e.g. 10-minute)
token for URL-bearing requests rather than reusing the 30-day session token.

Note: the `_validate_mutation_origin` middleware (`app.py:169`) plus the
`X-OM-Token` custom header does mean CSRF is adequately mitigated today — cross
origin requests with a custom header require a CORS preflight, which
`CORS_ORIGINS` gates. Credit where due; this is well handled.

---

### L-13 · Dead code in `FrameLayout.ring_values`

**Location:** `optical_modem/config.py:636-641`

The first loop's result is unconditionally overwritten two lines later:

```python
step = 1
while np.gcd(step, n) != 1:      # <-- computed, then discarded
    step += 1
step = max(1, (n // 3) | 1)      # <-- unconditional overwrite
while np.gcd(step, n) != 1:
    step += 1
```

Delete the first three lines. This is the ring-colour assignment for every
transmitted frame, so clarity here matters more than usual.

---

### L-14 · TOCTOU in the capture upload index check

**Location:** `optical_modem/capture/app.py:387-405`

```python
if index != sess.index:
    raise HTTPException(409, f"expected index {sess.index}")
...
img = await run_in_threadpool(_decode_upload, raw)     # <-- awaits
...
record = await run_in_threadpool(STORE.add_shot, sess, index, raw, meta, "jpg")
```

The check and the use are separated by two `await`s. Another device (or a double
tap) can advance `sess.index` in between; `add_shot` then files the photo against
a stale index. `add_shot` re-reads `sess.index` but only to decide whether to
advance — it never revalidates the caller's `index`.

**Fix:** hold `STORE._lock` across the check-and-insert, or pass the expected
index into `add_shot` and re-validate there under the lock:

```python
# AFTER — re-validate inside the lock that protects the mutation
def add_shot(self, sess, index, image_bytes, meta, ext="jpg", expected_index=None):
    with self._lock:
        if expected_index is not None and expected_index != sess.index:
            raise ValueError(f"expected index {sess.index}, got {expected_index}")
        ...
```

---

### L-15 · Path validation duplicated three ways

The same "is this name safe, and inside the dataset?" logic is written out
longhand in three places with different rules:

* `app.py:242` `_safe_name` (rejects `\`, control chars, `..`, >120 chars)
* `app.py:254` `_dataset_path` (symlink + `is_relative_to` check)
* `crop.py:20` `paths()` (re-checks split, filename, and containment again)
* `curate.py:19-30` (a fourth variant, for selections)

They have drifted: `_safe_name` caps at 120 chars, while `curate_dataset`
(`app.py:797`) checks `len(dest_name) > 120` *and* calls `_safe_name` — two
slightly different implementations of one rule. `dataset_image` (`app.py:935`)
calls `_dataset_path(name)` three times in one handler.

**Fix:** one module, `suite/paths.py`, exporting `safe_component(name)` and
`resolve_within(root, *parts)`. Every handler calls it; the containment rule
lives in exactly one place and can be tested once.

---

### L-16 · Documentation drift

`AGENTS.md` states:

> `make test` # python -m pytest -q **(207 tests**, ~100 s on 2 cores)
> tests/ **207 unit tests**

**MEASURED** on this branch: **276 passed, 13 skipped** (289 collected), 104 s.

Update the counts, and consider generating them — a stale test count in the file
an AI agent reads first is how "the tests pass" quietly stops meaning anything.

---

### L-17 · ~17 MB of binaries tracked in git

**MEASURED** — largest tracked files:

```
7,878,574  yolo26n-pose.pt
5,903,086  models/yolo26n_optical/weights/best.pt
1,333,903  docs/img/frame_detected_zoom.png
  810,995  docs/img/frame_recovered.png
  773,236  .config/Ultralytics/Arial.ttf
  740,976  docs/img/frame_detected.png
  735,168  docs/img/frame_capture.png
  603,109  devdata/synth_finders.png
```

Every `git clone` pays ~17 MB for files that almost never change and that the
Docker build copies in separately anyway. Move model weights to **Git LFS** or a
GitHub release asset (the Dockerfile already `COPY`s them, so it needs a
download step either way), and drop `.config/Ultralytics/Arial.ttf` — a
rendered-font cache that should never have been committed (add `.config/` to
`.gitignore`).

---

## Phase 2 — Architecture & Engineering Improvements

### What is genuinely good

It is worth being explicit, because the fix list above can read as if nothing
works. Several parts of this codebase are better than typical:

* **`net.py` is exemplary.** A single module owns proxy-scheme inference, cookie
  `Secure` derivation, redirect rewriting, build stamping and self-diagnosis,
  and it is written as an explanation of the failure modes it prevents. The
  `_relative_location` rewrite — which makes redirect loops structurally
  impossible — is the right call.
* **Subprocess isolation for heavy work is correct.** `suite/jobs.py` keeps
  torch out of the web process, writes atomic `job.json` files, reconstructs
  human-readable failure reasons (`_failure_reason` naming the OOM killer is a
  genuinely thoughtful touch), and degrades honestly to `interrupted` on restart.
* **The two-file edit journal in `crop.py`** is a real crash-safety design, not
  an afterthought, and it is exercised by tests.
* **Path-traversal defences are thorough and consistently applied** across
  `dataset_image`, `dataset_label`, `export_dataset_zip`, `curate.py` and
  `crop.py` — symlinks rejected, `is_relative_to` enforced, filenames checked
  against `Path(name).name == name`. I found no traversal bug.
* **CSRF** is adequately handled by the `Origin` check plus the custom
  `X-OM-Token` header, without bolting on a token framework.
* **Test coverage of the DSP core is strong**: `protocol/` 90–100%,
  `phy/channel.py` 99%, `phy/demod.py` 93%.

The issues below are mostly about the **serving layer growing faster than the
discipline that built the physical layer**.

### 2.1 Architecture & design patterns

**`suite/app.py` is 2,133 lines with 575 uncovered statements.** It mixes six
responsibilities: SPA routing, auth adoption, dataset CRUD, keypoint/crop
editing, job control, S2C orchestration, and the optical lab. Split it into
FastAPI `APIRouter` modules mirroring the existing page structure —
`suite/routers/{datasets,jobs,models,s2c,projects,lab}.py` — each with its own
dependency list. The capture-app adoption hack (`_adopt`, `app.py:213-220`) is
clever but fragile: it copies route objects while silently dropping the source
app's middleware, so a middleware added to `capture/app.py` will not apply in the
suite. Prefer `app.include_router(capture_app.router)` with an explicit prefix.

**Dual source of truth for artefacts.** `Registry` reads the filesystem;
`Store` reads Postgres/SQLite; `sync_from_disk` reconciles them at boot. Every
new field must be added in both, and `list_datasets` (`app.py:736`) merges the
two views by hand. Pick one: make the database authoritative and treat disk as a
cache, or (simpler, and truer to the "you can copy a run to another machine"
philosophy in the docstrings) keep disk authoritative and use the database only
for ownership and history — which is what `Store` mostly does today. Then write
down that decision at the top of `db.py`, because the current docstring says
"the filesystem stays the source of truth for *bytes*" but not for metadata.

**`gui/app.py` (360 lines) is a parallel implementation.** It has its own
`make_config`, its own `_STATE` receiver cache, and its own `HybridReceiver`
lifecycle — this is the DRY violation that produced H-5 in two places. Either
delete it now that the suite has `/lab` (which duplicates its `simulate` and
`decode` endpoints), or reduce it to a thin shim over `suite/s2c.py`.

**Polling is the default everywhere.** SSE exists (`/api/events`) but every page
*also* polls on a 1.5–8 s timer, so the server does the work of both. Once H-1 is
fixed, delete the `setInterval` calls in `Layout`, `Home`, `Datasets`, `Jobs`,
`Evaluate`, `Train`, `Capture`, and `Wizard` and let `useEventStream` drive
refreshes. That is ~10 timers across 8 files, all removable once the stream is
trustworthy.

### 2.2 Testing & observability

**Coverage (MEASURED, `pytest --cov=optical_modem`): 68% overall.**

| Module | Stmts | Miss | Cover | Note |
|---|---:|---:|---:|---|
| `suite/app.py` | 1344 | 575 | **57%** | largest file, least tested |
| `suite/s2c.py` | 228 | 115 | **50%** | loopback (359-417) & receive (294-330) untested |
| `suite/bus.py` | 212 | 110 | **48%** | all of `RedisBus` (135-264) untested |
| `suite/registry.py` | 328 | 169 | **48%** | `promote`/`activate`/`delete_model` untested |
| `suite/datasets.py` | 138 | 122 | **12%** | the whole generation pipeline |
| `suite/tasks.py` | 61 | 61 | **0%** | subprocess entry points |
| `suite/worker.py` | 53 | 53 | **0%** | |
| `cli.py` / `bench.py` | 425 | 425 | **0%** | |
| `detect/yolo.py` | 79 | 79 | **0%** | needs a torch marker, not silence |
| `receiver.py` | 76 | 22 | 71% | |

The dangerous gap is **`registry.promote` / `registry.activate` at 0%** — the
model-promotion path, whose failure is silent and only shows up later as a bad
receiver. Priority order for new tests:

1. `RedisBus` against a fake — the class is 110 uncovered statements and it is
   the production path in `docker-compose.yml`. A `fakeredis`-style stub makes
   this testable in CI with no service.
2. `registry.promote` / `activate` / `delete_model`, including the "active model
   deleted" branch (`registry.py:413-423`).
3. `suite/s2c.py` `loopback` and `receive_bytes` — the flagship feature's
   verification path.
4. `suite/tasks.py` `synth` / `captures` / `merge` — smoke-level, asserting the
   output has the right shape.

**Add a regression test for H-1.** It is the highest-severity bug here and it has
no test:

```python
def test_sse_streams_do_not_exhaust_the_thread_pool():
    """Open more SSE streams than the pool has threads; sync endpoints must stay fast."""
```

**Observability.** `suite/logging_config.py` exists and the app logs sensibly at
boot, on login, and on job transitions — good. Missing:

* **No request metrics.** No latency histogram, no status-code counter, no
  in-flight gauge. Add a middleware emitting `http_request_duration_seconds` and
  expose `/metrics` (Prometheus text format) — it is ~40 lines and would have
  made H-1 visible in seconds rather than after a profiling detour.
* **No thread-pool saturation signal.** After fixing H-1, export
  `anyio.to_thread.current_default_thread_limiter().total_tokens` minus
  `borrowed_tokens` as a gauge. This is the metric that predicts the next
  instance of this class of bug.
* **No structured logging.** `logging_config.py` writes human-readable lines;
  add a JSON formatter behind a `LOG_FORMAT=json` env var so Coolify/Loki can
  query by `job_id` or `transfer_id`.
* **No trace ids.** A training job spans an HTTP request, a job record, a
  subprocess and a log file with no shared correlation id.
* `print()` to stdout is still used for error paths in five places
  (`app.py:103`, `:837`, `:1662`, `:1741`, and `jobs.py`) where `log.*` is
  already imported — these bypass the log formatter entirely.

### 2.3 Developer experience

**There is no CI.** No `.github/`, no workflows, nothing. For a project whose
failure modes are "training OOMs on a 4-core box" and "the PWA serves a stale
shell", that is the single highest-leverage DX investment. Minimum viable
pipeline:

```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12", cache: pip }
      - run: pip install -r requirements-suite.txt pytest-cov
      - run: pytest -q --cov=optical_modem --cov-fail-under=70
  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "20", cache: npm, cache-dependency-path: frontend/package-lock.json }
      - run: npm ci && npm run build
        working-directory: frontend
```

**No linting or formatting.** `AGENTS.md` says "No lint/format config exists
(no ruff, pre-commit, or pyproject)" and treats that as a convention to match.
It is a liability: there is no `pyproject.toml` at all, so dependencies are
unpinned (`torch>=2.2`, `ultralytics>=8.4`) and a fresh build can silently pick
up a breaking ultralytics release. Add:

* `pyproject.toml` with `[project]` metadata, and pin upper bounds on
  `ultralytics` (~8.4), `fastapi` (~0.115), `sqlalchemy` (~2.0).
* `ruff` with a ruleset limited to real bugs to start (`E`, `F`, `B`, `UP`,
  `ASYNC`, `S`, `PTH`) — run `ruff check --select=E,F,B,UP,ASYNC,S,PTH` once and
  fix what it finds; adopt format later.
* `mypy` on `optical_modem/suite/` only. The codebase already uses modern
  annotations (`dict | None`, `Mapped[]`) consistently; only `suite/app.py`
  and `suite/db.py` would need work.
* `pre-commit` with `ruff`, `ruff-format` (on new files only), and
  `pytest -q -x` on changed tests.

**Frontend gaps.**

* `frontend/e2e/studio.spec.ts:4` logs in with `password: ''` — the E2E suite
  only runs against a password-less instance, and `scripts/suite_smoke.py` is
  not wired into pytest or CI at all.
* `lucide-react: ^1.64.0` in `package.json` is an unusual major for that package;
  verify it resolves to the intended release and pin it exactly.
* `frontend/e2e/server.py` exists but is not referenced by
  `playwright.config.ts`; document how E2E is meant to run, or wire it up.
* `vite.config.ts` `navigateFallbackDenylist` uses the v4 name while the plugin
  is pinned to `^0.20.1` — check it is not silently ignored, or stale-shell bugs
  over HTTPS will return.

**Ops polish.**

* `redis` runs `--appendonly yes` with `--maxmemory-policy allkeys-lru`. LRU
  eviction of keys that are also in the AOF causes the AOF to grow and diverge
  from the dataset. Use `noeviction` for the lock/heartbeat keyspace, or split
  job events onto a separate instance from locks.
* `docker-compose.yml` `worker` profile sets `SUITE_RUN_JOBS=worker`, but
  `worker.py` never reads that variable — it is documentation-only. Either wire
  it up or remove it to stop implying the worker reads its config from there.
* Add a `depends_on: condition: service_healthy` for `suite` → already present —
  good. But the `db` healthcheck uses `pg_isready` without `-h`, which is
  correct inside the container; no change needed.

---

## Phase 3 — New Feature Proposals & Roadmap

The product's core value is: **train a frame detector on your own screens and
cameras, then move real bytes through the air gap.** Everything below is ordered
by (value × confidence) ÷ effort.

### Quick wins (days, no architectural change)

| # | Feature | Why | Where |
|---|---|---|---|
| Q1 | **Decode telemetry panel** | `report.timings` already splits `yolo`/`contours`/`total`, and `ring_score`/`finder_score`/`quality`/`n_corrected` are already returned — they are just not shown. Surfaces *why* a frame failed instantly instead of after a `docker logs` dig. | `frontend/src/pages/Lab.tsx`, `S2C.tsx` |
| Q2 | **`?cells=` bounds + shared `BoundedInt`** | H-3, but as a project-wide convention so it cannot regress. | `suite/app.py` |
| Q3 | **Auto-resume paused jobs after N minutes** | Closes M-9's operational trap with ~10 lines. | `suite/jobs.py`, `suite/app.py:1546` |
| Q4 | **Fix the doc counts + add `ALLOW_ANONYMOUS`** | Closes L-16 and M-11; both are confusion multipliers for the next engineer *and* the next agent. | `AGENTS.md`, `capture/app.py` |
| Q5 | **`/metrics` endpoint** | Makes the next H-1-class bug visible before it is a user report. | new `suite/metrics.py` |
| Q6 | **Build-id drift banner** | `net.stamp_build` already stamps every page with `__BUILD__`; the client never compares it. One `fetch('/version')` on visibility change → "this tab is stale, reload". | `client.ts`, `Layout.tsx` |
| Q7 | **Retrain the bundled detector on v0.3 frames** | `AGENTS.md` already flags this: the shipped `yolo26n-pose.pt` was trained on the v0.2 renderer and "localises but decodes poorly". This is a *job to run*, not code to write. | `make train` + promote |

### Major strategic features

#### S1 · Adaptive link layer (closed-loop modulation & ECC)

**Problem.** The modem picks one modulation, ECC, grid and frame rate for the
whole transfer and never revisits it. Real conditions — phone handshake, screen
brightness, ambient light, distance — change *during* a transfer. The receiver
already measures everything needed to adapt (`quality`, `ring_score`,
`n_corrected`) and `config.choose_ecc(quality)` already exists
(`config.py:432`) but is **never called** by the transfer path.

**What it is.** A per-transfer controller: after each decoded frame, feed
`(quality, ring_score, n_corrected, decode_ms)` into a small state machine that
steps modulation up when the margin is large and down when it is thin, and
signals the sender to re-render the remaining frames. Report the achieved
goodput curve in the UI.

**Implementation.**

* New `optical_modem/protocol/adaptive.py`: `LinkState` + `suggest(cfg, samples)`
  returning a new `ModemConfig` (or `None` when the current one is optimal).
* `suite/s2c.py`: `Transfer.link = LinkState()`; update in `S2C.decode`
  (`:292-330`) after each successful and failed frame; when the suggestion
  changes, re-chunk the *remaining* payload via a fresh `Packetizer`.
* `suite/app.py`: `POST /api/suite/s2c/{tid}/adapt` to apply a suggestion, and
  return `link: {quality, suggested, history}` from `s2c_state` (`:1717`) so the
  UI can render the curve without polling.
* Frontend: a goodput sparkline in `S2C.tsx` showing bits/frame over time with
  the adaptation points marked.

**Dependencies:** none — everything is in-tree. **Effort:** ~1 week.
**Risk:** re-chunking mid-transfer needs care around sequence numbers; keep
`Packetizer` monotonic and treat a re-chunk as a new run of `seq` values with the
frame header carrying the new `mod_id`/`ecc_id` (it already does).

This is the highest-value feature on the list: it converts the product from
"works if you chose the right settings" to "works", and it directly attacks the
25–50% hard-frame loss that `AGENTS.md` §10 calls the top open problem.

#### S2 · Dataset flywheel: active learning from failed captures

**Problem.** The pipeline is *synthesize → train → evaluate → hope*. Frames that
fail in the field — the ones the receiver actually got wrong — are thrown away.
The highest-value training data is precisely the data the current model cannot
decode, and today nobody collects it.

**What it is.** Every failed capture (S2C receive, capture-station shot, lab
decode) is written to a **`hard-negatives`** dataset with its telemetry attached.
The Datasets page surfaces "frames your model cannot decode" as a first-class
view, and one click queues a fine-tune that mixes them into the training set at a
higher weight.

**Implementation.**

* `suite/registry.py`: `hard_negatives_dir()` alongside `dataset_dir()`.
* `suite/s2c.py:~330` (the `else` branch of `decode`) and
  `capture/app.py:447` (`peek_frame` failures): persist the image plus
  `{transfer_id, ring_score, quality, n_corrected, reason, cfg_hash}` as a JSON
  sidecar — the same sidecar format `capture/sessions.py` already writes, so
  `scripts/captures_to_dataset.py` needs only a new source flag.
* New endpoint `GET /api/suite/hard-negatives` returning failures bucketed by
  `reason` (`header:ValueError`, `no frame found`, `crc`, …), plus
  `POST /api/suite/hard-negatives/{dataset}/distill` which merges them into a
  dataset via the existing `merge` job.
* `data/train_yolo.py`: a `--hard-negative-weight` flag that oversamples those
  images.

**Dependencies:** none. **Effort:** ~1.5 weeks. **Why now:** it turns the
existing (excellent) telemetry from diagnostics into training signal, and it is
the only feature here that makes the model better every time someone uses the
product.

#### S3 · Model evaluation harness & regression gate

**Problem.** `registry.promote()` has **0% test coverage** and no acceptance
criteria. Promoting a model is one click, and nothing stops you promoting one
that is worse. `scripts/evaluate.py` exists and writes `eval.json`, but nothing
compares two runs or blocks a regression.

**What it is.** A standard, repeatable benchmark — fixed seed, fixed level
sweep, fixed frame count — plus a comparison view: "candidate vs active, by
channel level, corner error and decode rate", and an optional gate that refuses
promotion when the candidate is worse on any tracked metric.

**Implementation.**

* `scripts/evaluate.py`: add `--suite standard` fixing `(n=50, levels=0.0,0.2,0.35,0.5,0.7,1.0, seed=0)`;
  emit a schema-versioned `eval.json` with `corner_err_p50/p90`, `decode_rate`,
  `payload_ok_rate`, `median_ms` per level.
* `suite/registry.py`: `compare(a, b) -> dict` reading two `eval.json` files.
* `suite/app.py`: `GET /api/suite/models/compare?a=…&b=…`, and a
  `?gate=1` parameter on `POST /api/suite/models/promote` that returns `409`
  with the failing metric when the candidate regresses.
* Frontend: a small comparison table on `/models` with per-level rows and
  green/red deltas.
* Tests: this is the natural home for the `registry.promote` / `activate` tests
  flagged in §2.2.

**Dependencies:** none. **Effort:** ~1 week. This is the prerequisite for
trusting S2 — without it you cannot tell whether an active-learning fine-tune
actually helped.

#### S5 · Separate worker containers (horizontal job execution)

**Problem.** The suite runs jobs **in the web process**. That was the right
trade-off for a 4-core VPS, and it is enforced deliberately —
`app.py:112-114` raises `RuntimeError` if `SUITE_RUN_JOBS` is anything but
`inprocess`. But it means:

* a training job competes with the web UI for the same 4 cores and the same
  5 GB memory limit, so the dashboard slows to a crawl during every run;
* you cannot scale execution independently of serving — the only lever is
  "make the one container bigger";
* `SUITE_MAX_JOBS=1` serialises everything, so a 40-minute training run blocks a
  10-second dataset merge.

The scaffolding for the fix already exists and is **wired to nothing**:
`docker-compose.yml` ships a `worker` service behind the
`experimental-worker` profile, `suite/worker.py` has a polling loop,
`bus.acquire/release` provides TTL locks, `JobRecord.heartbeat` has a column,
and `Store.mark_interrupted(older_than)` can reap dead workers' jobs. The
missing piece — the reason it is marked experimental — is that **nothing
prevents two processes from spawning the same job**. `JobManager._load()`
rehydrates every `jobs/*/job.json`, both processes call `_spawn`, and the job
runs twice against one log file and one output directory.

**What it is.** A claim protocol that makes job execution single-owner, so N
worker containers can run against the same volume and database:

* the web process **enqueues only** (writes `status=queued`, never spawns);
* a worker **claims** a queued job with an atomic, expiring lock, runs it, and
  heartbeats;
* a **reaper** returns jobs whose heartbeat went stale (worker crashed, OOM-killed,
  container rescheduled) to `queued` — or marks them `interrupted` after a
  retry budget;
* `/healthz` and `/api/suite/status` report **queue depth + worker fleet health**,
  so a stalled queue is visible rather than silent.

**Implementation.**

* New `optical_modem/suite/queue.py`:
  * `claim(job_id, worker_id, ttl)` — `bus.acquire(f"job:{job_id}", ttl)` when
    Redis is present; otherwise a **conditional `UPDATE`** on `JobRecord`
    (`WHERE status='queued'`) so SQLite deployments get real mutual exclusion
    too. This matters: the audit's whole "degrades to SQLite" story must survive.
  * `heartbeat(job_id, worker_id)`, `release(job_id, worker_id)`,
    `reap(older_than, max_retries)`.
* `suite/jobs.py`: `JobManager(run_inprocess=...)` gains `claim_jobs=True` /
  `worker_id`. `start()` no longer calls `_spawn` when enqueue-only;
  `_spawn` is preceded by a successful `claim()` and followed by a `release()`.
  `adopt_from_store()` must skip jobs another worker owns.
* `suite/worker.py`: replace the `time.sleep(2.0)` scan-poll with claim/heartbeat
  and a reaper pass; emit `worker.claimed` / `worker.lost` bus events.
* `suite/app.py`: remove the `RuntimeError` gate; add `GET /api/suite/workers`
  (fleet: id, last heartbeat, current job, jobs completed).
* `docker-compose.yml`: promote `worker` out of `profiles:`, add
  `deploy.replicas` and a `SUITE_WORKER_ID` (default the container hostname), and
  move the CPU/memory limits so the **web** container gets the small profile
  (1 CPU / 1.5 GB) and workers get the training budget (4 CPU / 5 GB).

**Dependencies:** none — Redis is already a compose service and `LocalBus.acquire`
already implements TTL locks in-process. **Effort:** ~1.5 weeks, of which most is
the reaper's correctness under "worker dies mid-epoch with a 4 GB output file".
**Risk:** the highest on this list. Ship it behind `SUITE_RUN_JOBS=worker` with
`inprocess` remaining the default until the reaper has a chaos test (kill `-9` a
worker mid-job and assert the job is requeued exactly once and never double-run).

**Sequencing note:** S5 must land *before* S1/S2, because both of those make jobs
longer and more numerous — which is exactly the load that the in-process executor
handles worst.

#### S4 · Multi-user accounts & per-project sharing

**Problem.** One shared `APP_PASSWORD`, no identity, no roles. Any logged-in user
can delete any dataset, any model, any project. There is no attribution in the
audit trail — `Event` rows record `project_id` but nobody.

**What it is.** Real accounts with roles (`owner` / `operator` / `viewer`),
per-project membership, API keys for the capture station (replacing the shared
password), and a revocable session list.

**Implementation.**

* `suite/db.py`: new `User` and `ApiKey` models; add `user_id` to `Event` and
  `actor` to the audit trail. `ProjectMember` for per-project grants.
* `capture/app.py`: replace `_password_ok` with `authenticate()` returning a
  `(user, session)` pair; make the token carry a **session id** rather than just
  a timestamp, so sessions can be listed and revoked individually — the
  `_revocation_store` machinery (H-6) generalises directly.
* New `suite/deps.py` with `require_role("operator")` composed into the
  `dependencies=[…]` lists that already exist on every route — the current
  `Depends(require_auth)` pattern makes this a mechanical change.
* Frontend: a `/settings/access` page; keep `useAuth` as the single source.

**Dependencies:** `passlib[bcrypt]` (or `argon2-cffi`) for password hashing —
currently there is no hashing at all because the password is compared directly,
meaning `APP_PASSWORD` must be stored in plaintext in the environment. **That is
worth fixing regardless of whether S4 ships:** accept a
`APP_PASSWORD_HASH` (salted hash) so the plaintext secret never sits in an env
var or a compose file.

**Effort:** ~3 weeks. **Priority:** do this before any multi-tenant or public
deployment; not needed for single-operator use.

### API & integration opportunities

| # | Integration | Value | Notes |
|---|---|---|---|
| I1 | **WebRTC / MJPEG live preview** | The `/s2c` scanner currently POSTs a full JPEG and waits ~1.4 s per frame (M-8). A `MediaStream` → server pipeline with client-side pre-filtering (blur/exposure check before upload) roughly halves wasted round-trips. | Build on the existing `getUserMedia` code in `S2C.tsx:195`; skip frames that fail a cheap sharpness gate. |
| I2 | **Webhook / event sink** | `BUS` already publishes `job.*`, `dataset.*`, `model.*`, `s2c.*`. A `WEBHOOK_URL` subscriber lets users pipe training completion into Slack/Discord/n8n — the single most requested thing for long CPU jobs. | ~60 lines in `suite/bus.py`; sign payloads with `APP_SECRET`. |
| I3 | **ONNX / TFLite export** | `AGENTS.md` targets a 4-core CPU VPS; ONNX Runtime is 2–4× faster than the PyTorch path and enables ARM deployment (Raspberry Pi receiver, Android). Add `POST /api/suite/models/{name}/export?format=onnx` using `ultralytics`' built-in exporter, and have `receiver.py` prefer an `.onnx` sidecar. | `detect/yolo.py` gains an `onnxruntime` backend behind the same `detect()` signature. |
| I4 | **S3 / R2 dataset sync** | Datasets and runs are the only state that is expensive to lose and awkward to move. `rclone`-style sync of `/data/datasets` and `/data/models` to object storage, exposed as a job. | Reuse `JobManager` + `tasks.py`; needs `boto3`. |
| I5 | **Public read-only API** | A scoped, revocable API key that can read `/api/suite/status`, `/progress`, and transfer metadata — for dashboards and the `scripts/` tooling. | Falls out of S4's `ApiKey` model. |
| I6 | **`coolify.yaml` / one-click template** | Every deployment doc in this repo says "Coolify → New → Docker Compose". Shipping an actual template removes the last manual step and the class of `TRUST_PROXY`/`COOKIE_SECURE` misconfigurations that `net.diagnose` exists to catch. | Zero code; highest ratio of delight to effort on this list. |

### Suggested sequence

1. **Week 1 — stop the bleeding.** H-1, H-2, H-3, H-4, H-5, H-6. Add the SSE
   regression test and `/metrics`. Get CI green.
2. **Week 2 — make it fast.** M-7, M-8, M-9, M-10, M-11, M-12. Delete the
   polling timers. Run Q7 (retrain the bundled detector).
3. **Weeks 3-4 — make it learn.** S3 (eval harness, with the missing
   `registry.promote` tests), then S2 (hard negatives), then S1 (adaptive link).
4. **Week 5+ — grow up.** S4 (accounts) and I3 (ONNX) in parallel; Q1–Q6 and
   I1/I2/I6 as filler.

---

## Appendix — reproducing the measurements

All figures above were produced on branch `arena/d6b4426e-yolo-docker`,
Python 3.11, deps from `requirements-suite.txt` minus torch/ultralytics (CPU-only
sandbox; the torch-free path is exercised throughout).

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-suite.txt pytest-cov

# test suite + coverage
DATA_DIR=/tmp/omcov APP_PASSWORD=demo APP_SECRET=s COOKIE_SECURE=0 \
PYTHONPATH=$(pwd) .venv/bin/python -m pytest -q --cov=optical_modem --cov-report=term-missing
# -> 276 passed, 13 skipped; TOTAL 7909 2513 68%

# H-1  SSE thread-pool starvation (needs a live uvicorn, not TestClient)
#      open 45 EventSource connections, then time a sync endpoint -> ~11.9 s

# H-2  reflected XSS
curl -s "http://host/api/suite/finder/ring.svg?bg=%23fff%22%20onload%3D%22alert(1)"
# -> <rect ... fill="#fff" onload="alert(1)" rx="8"/>

# H-3  unbounded cells
#      /api/suite/palette?cells=32   ->    21 ms
#      /api/suite/palette?cells=512  ->  2568 ms
#      /api/suite/palette?cells=4096 ->  timeout (>30 s)

# H-4  throttle bypass (uvicorn --proxy-headers --forwarded-allow-ips='*')
#      30 attempts, XFF rotated -> 0/30 rate-limited
#      30 attempts, IP fixed    -> 20/30 rate-limited
#      300 distinct IPs, 1 failure each -> 300 entries retained forever

# H-6  blocking auth
#      20 authenticated requests -> 20 sqlite connections

# M-7  poll cost on a 12,000-file dataset
#      list_datasets() 40 ms · disk() 34 ms · quality.inspect() 936 ms

# M-8  decode profile (720 px frame, classical path)
#      1410 ms total; refine_rotation = 541 ms
#      warpAffine 2.69 ms + cv2.integral 4.50 ms per evaluate(); 41 calls/attempt, 9 needed
```

One caveat on H-4: `TestClient` does **not** apply uvicorn's
`ProxyHeadersMiddleware`, so it always reports `client.host` as `"testclient"`
and the XFF bypass will not reproduce under it. Test against a real server, as
the script does.
