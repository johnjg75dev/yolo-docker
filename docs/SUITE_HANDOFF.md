# SUITE HANDOFF — one container, the whole screen→camera modem

**Audience:** the LLM agent (or human) who has just been handed this repository on the
target server. Read this file first, then `README.md`, `docs/PROTOCOL.md`,
`docs/MODEL.md`, `docs/BENCHMARKS.md`, `docs/CAPTURE_APP.md`.

**What exists:** an optical modem (a screen shows a colour code, a camera reads it)
plus *everything around it* — a dataset builder that includes the photo-capture
tool, CPU training of the frame detector, evaluation, a model registry, and the
S2C (screen-to-camera) app itself. It all runs as **one Docker image, one process,
one volume**, and is designed to be deployed through **Coolify → Docker Compose**
on a 4-core / 8 GB / Ubuntu 26.04 VPS.

**What you are expected to do:** get it running (§3), walk the five stages once
(§6) to prove it works on this box, and then continue the work (§10 lists what is
left, in priority order). Everything before this session's last turn is verified;
§9 lists the bugs that were fixed and the regression tests that now guard them, so
**do not "fix" those again in a different way** without reading it.

---

## 0. Contents

1. Status at handoff
2. Deploy on the VPS with Coolify  (+ §2.1 state services: Postgres & Redis;
   + §2.2 **HTTPS behind Traefik — the four bugs and the fix**)
2.8 Diagnosing a deployment in one command (`scripts/check_https.py`)
3. Run it locally (no Docker)
4. Repository map
5. Environment variables
6. The five stages — runbook with copy-pasteable calls
7. Data & contracts (files, JSON shapes, API, CLI), incl. §7.5 projects = state
8. Protocol/physics facts that matter to the code  (+ §8.6 finder patterns)
9. Bug ledger — found, fixed, and now covered by tests
9.5 The guided UI (§9.5) — wizard, progress API, and the SVG visual language
10. Known limitations and the next five things to do
11. Verification checklist
12. Troubleshooting
13. House rules that hurt when ignored

---

## 1. Status at handoff

* Test suite: `python -m pytest tests -q` → **207 passed** (130 before the state +
  finder work; `tests/test_finder.py`, `tests/test_store.py`, `tests/test_bus.py`
  added 77).
* End-to-end smoke test over HTTP: `python scripts/suite_smoke.py --base ... --password ...`
  → creates a dataset, merges, trains, evaluates, promotes, transmits, decodes
  through the phone path and downloads the payload. **PASS** on a 2-core / 2 GB
  sandbox (see §11 for exact numbers).
* Verified surfaces: `/`, `/wizard`, `/login`, `/guide`, `/projects`, `/dataset`, `/train`,
  `/evaluate`, `/models`, `/s2c`, `/display`, `/camera`, `/healthz`, `/version`,
  and every API route in §7.4–§7.6.
* **State lives in a database** (Postgres 16 in compose; SQLite when you run it
  without one) and **job events go over Redis** (in-process bus as fallback).
  Both are optional at run time and both are exercised in the tests — see §2.1.
* The one thing that is *not* perfect: on deliberately hard simulated captures
  (handheld level ≥ 0.35 at 1080 px) roughly a quarter to a half of individual
  frames fail to demodulate. The transfer layer is built to cope (repeats +
  out-of-order reassembly + partial retransmission), but see §10 item 1 — better
  header robustness is the highest-value open engineering task.

Nothing in this repository is a mock. If a page or a command is described here,
it exists and was exercised.

---

## 2. Deploy on the VPS with Coolify

Target: Ubuntu 26.04, 4 vCPU, 8 GB RAM, 100 GB disk, Docker + Coolify installed.

1. **Push the repository** (or point Coolify at its git remote). There is no git
   history requirement — Coolify builds from the working tree.
2. **Coolify → + New → Docker Compose.** Compose file: `docker-compose.yml`.
3. **Environment** (Coolify → Environment Variables):

   | variable | required | meaning |
   |---|---|---|
   | `APP_PASSWORD` | **yes** | the single password for laptop *and* phone |
   | `APP_SECRET` | strongly yes | HMAC key for session cookies: `openssl rand -hex 32`. If unset, a random key is generated into `/data/secret.key` and sessions die on redeploy |
   | `COOKIE_SECURE` | no (default `auto`) | `auto` = `Secure` exactly when the browser is on https. Leave it alone: `1` breaks plain-HTTP LAN testing, `0` weakens https |
   | `TRUST_PROXY` | no (default `1`) | read `X-Forwarded-Proto`/`-Host`. Keep `1` behind Coolify; `0` only if the container is exposed directly |
   | `FORCE_HTTPS` | no (default `0`) | app-side 308 http→https. Keep `0` **while the proxy redirects** — two redirectors is how loops start |
   | `PUBLIC_URL` | no | the exact origin users type, e.g. `https://s2c.example.com`; used for links/QR codes printed in the UI |
   | `AUTOLABEL` | no (default `1`) | locate the frame in each uploaded photo with pure OpenCV (~0.5–1.5 s) |
   | `MAX_UPLOAD_MB` | no (default `24`) | phone photo size cap |
   | `OMP_NUM_THREADS`, `MKL_NUM_THREADS` | no (default `4`) | keep equal to the CPU limit, not the host's core count |
   | `YOLO_OFFLINE` | no (default `1`) | never let Ultralytics phone home for a base checkpoint; use the bundled weights |
   | `YOLO_CONFIG_DIR` | no (default `/data/.ultralytics`) | where Ultralytics caches settings, inside the volume |
   | `SUITE_PORT` | no (default `8000`) | host port mapping |
   | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | no (default `suite`) | database credentials; change the password on a public box |
   | `DATABASE_URL` | compose sets it | overrides the store URL; `postgres://…` is rewritten to the psycopg driver |
   | `REDIS_URL` | compose sets it | `redis://redis:6379/0`; unset/`SUITE_REDIS=0` → in-process bus |
   | `SUITE_DB` | `1` | `0` disables the database entirely (filesystem-only demo) |
   | `SUITE_MAX_JOBS` | `1` | concurrent background jobs |
4. **Domain + TLS.** Set a domain in Coolify and let it terminate TLS. This is not
   optional in practice: **the phone camera only works over HTTPS** (browsers
   require a secure context for `getUserMedia`). Coolify's Let's Encrypt
   integration is the path of least resistance.
5. **Deploy.** First build takes ~10–15 min (torch CPU wheels ≈ 2 GB of layers).
   The healthcheck hits `/healthz`; `start_period` is 40 s.
6. **Volumes.** Three, one per service: `suite-data` → `/data` (datasets, runs,
   models, job logs, photos, sessions), `suite-db` → Postgres data dir,
   `suite-redis` → Redis AOF. Back up `suite-data` and `suite-db`; Redis only
   holds the recent event ring and expiring locks, so it needs no backup.
   To put it on a specific disk, replace the named volume with a bind mount:
   `driver_opts: { type: none, o: bind, device: /srv/optical-suite }`.
7. **Resource limits** in compose are `cpus: 4.0`, `memory: 6g`. If training dies
   with "killed by SIGKILL — almost certainly out of memory", lower `batch` on the
   Train page first (the page's suggested batch already follows available RAM).

Sanity check after deploy:

```bash
curl -fsS https://YOUR_DOMAIN/healthz | python -m json.tool
# { "ok": true, "service": "optical-modem-suite", "root": "/data",
#   "sessions": 0, "datasets": 0, "models": 1, "active_model": "yolo26n_optical",
#   "jobs_running": 0, "password_set": true,
#   "database": { "kind": "postgresql", "url": "postgresql+psycopg://suite:***@db:5432/suite", "ok": true },
#   "bus": { "kind": "redis", "subscribers": 0, "recent": 0,
#            "url": "redis://redis:6379/0", "redis": "7.4.1" } }
```

`ok` flips to `"ok": false` if the database is unreachable, so a half-broken
deploy fails its healthcheck instead of serving pages that cannot save.

### 2.1 State services: what Postgres and Redis actually do

Compose runs three services. Only `suite` is application code; the other two
exist because a single container cannot answer "what did this box do last week?"
across a rebuild.

| service | image | holds | why it is not just files |
|---|---|---|---|
| `db` | `postgres:16-alpine` | projects (development / finished / archived), datasets, runs, models, jobs, transfers, events | queryable, transactional, survives `docker compose up --build`, one place for "which project is this dataset part of" |
| `redis` | `redis:7-alpine` | job events (`optical-suite:jobs` channel), capped event ring (`optical-suite:events`), `SET NX EX` locks, job heartbeats | fan-out to every open browser (SSE) without polling the filesystem; a "one training job at a time" lock that expires if the holder dies; heartbeats that prove a worker is alive |

Images and weights stay on the `suite-data` volume — the database holds
*metadata* (a few MB), never megabytes of photos. `$DATA_DIR/suite.db` (SQLite)
is used automatically when `DATABASE_URL` is unset, so `docker run` with no
compose still works; `SUITE_DB=0` turns persistence off for throwaway demos.

Boot reconciliation (`DB.sync_from_disk(REG)`): any dataset/run/model already on
the volume but missing from the database is imported once, idempotently, so an
existing `/data` volume from an older deployment appears in the UI instead of
looking empty. Jobs that were running when the container died are marked
`interrupted` rather than left as a spinner forever.

The Projects page (`/projects`) is the UI for this: create a project, set it to
`development` or `finished`, see how many datasets/runs it owns, and watch the
event feed. Dataset and Train pages carry a project selector that remembers your
choice, so artefacts created from a browser are filed automatically.

### 2.2 HTTPS behind Traefik / Coolify — why it broke, and the fix

The container never sees a TLS socket. Traefik terminates TLS and forwards
**plain HTTP** to uvicorn. Every scheme-dependent decision therefore has to come
from `X-Forwarded-Proto`, not from the socket. Four separate bugs came from
reading the socket/static config instead, and all four are invisible on a laptop
over http — which is precisely the "http works, https does not" report.

| # | symptom | real cause | fix (in code now) |
|---|---|---|---|
| 1 | `ERR_TOO_MANY_REDIRECTS` on https only | Starlette's trailing-slash redirect built an **absolute** URL from `scope["scheme"]` = `http`; the browser went to http, Traefik redirected back to https, forever | `ProxySchemeMiddleware` sets `scope["scheme"]` from `X-Forwarded-Proto` *and* rewrites same-host `Location` headers to **path-relative** (`/api/login`), so a redirect can never downgrade the scheme |
| 2 | login "does nothing"; every API `401` | `COOKIE_SECURE=1` → `Set-Cookie: …; Secure` sent over a plain-HTTP socket; browsers drop it silently | `COOKIE_SECURE=auto` (new default): `Secure` exactly when the browser is on https. Logout deletes the cookie with the same flags |
| 3 | after a redeploy, the old UI loads and its buttons do nothing — https only | service workers *only run in secure contexts*, so http testing never had one; `sw.js` had a hand-maintained `VERSION = "om-suite-v4"` that nobody bumped | `/sw.js` now substitutes `om-suite-<build>` where `<build>` is a hash of the code + static files (`net.build_id()`), pages carry `<meta name="om-build">`, and `common.js` reloads once when the server's build differs |
| 4 | job progress freezes behind TLS, a reload catches up | the capture SSE sent the hop-by-hop header `Connection: keep-alive`, illegal in HTTP/2, and could be buffered | removed; events now send `Cache-Control: no-cache, no-transform` + `X-Accel-Buffering: no` |

Also worth knowing: the phone camera (`getUserMedia`) and service workers require
a secure context, so **plain http can never be the production answer** — the app
tells you this itself (see `secure_context` below).

#### The Traefik labels Coolify should generate

```yaml
traefik.enable=true
traefik.http.routers.s2c.rule=Host(`s2c.example.com`)
traefik.http.routers.s2c.entrypoints=websecure
traefik.http.routers.s2c.tls=true
traefik.http.routers.s2c.tls.certresolver=letsencrypt
traefik.http.services.s2c.loadbalancer.server.port=8000

# plain http → https. permanent=true is 308: it PRESERVES the method and body.
# 301/302 silently turn the login POST into a GET, which looks like
# "the password is ignored".
traefik.http.middlewares.s2c-https.redirectscheme.scheme=https
traefik.http.middlewares.s2c-https.redirectscheme.permanent=true
traefik.http.routers.s2c-http.rule=Host(`s2c.example.com`)
traefik.http.routers.s2c-http.entrypoints=web
traefik.http.routers.s2c-http.middlewares=s2c-https
```

Traefik sends `X-Forwarded-Proto`, `X-Forwarded-For` and `X-Forwarded-Host` by
default, and the Dockerfile CMD already runs uvicorn with
`--proxy-headers --forwarded-allow-ips '*'`. If Coolify overrides the command,
keep those two flags. **Do not** also set `FORCE_HTTPS=1` while the proxy
redirects (two redirectors = loops) — that flag exists for a bare
`docker run -p 80:8000` deployment.

#### Diagnosing it in one command

```bash
# from your laptop, against the public URL
python3 scripts/check_https.py --base https://s2c.example.com --password '…'

# against the local TLS stand-in used to develop this (self-signed cert)
python3 scripts/check_https.py --base https://127.0.0.1:8443 --plain-port 8080 \
        --password demo --local
```

It walks the browser's path and prints one verdict per step: TCP → TLS
certificate → http→https redirect *and its status code* → `X-Forwarded-Proto`
→ app-vs-browser scheme → cookie flags → login + authenticated API call → SSE
actually streaming. Exit code 1 means a real problem; every failure line carries
the fix. Verified output against the recommended config:

```
ok    tcp                 127.0.0.1:8446 reachable
ok    tls                 TLSv1.3 · issuer …
ok    redirect            308 → https://127.0.0.1:8446/api/suite/diagnose
ok    scheme              app and browser both see 'https'
ok    forwarded_proto     X-Forwarded-Proto: https
ok    scheme_trusted      app sees scheme='https', browser is on 'https'
ok    redirects_relative  absolute redirects are rewritten to path-relative Location headers
ok    cookie_secure       COOKIE_SECURE=auto → cookie Secure=True (browser scheme https)
ok    secure_context      browser origin is https://127.0.0.1:8446
ok    proxy_hops          X-Forwarded-For: 127.0.0.1
ok    build               97209b98
ok    login               cookie stored (Secure=True), authenticated API 200
ok    sse                 streaming (0.01s to first byte: b'retry: 3000\n')
VERDICT: https looks correct end to end.
```

The same page is available as JSON, unauthenticated, so it works while you are
locked out of the UI:

```bash
curl -s https://s2c.example.com/api/suite/diagnose | python -m json.tool
```

```
{
  "ok": true, "scheme": "https", "scope_scheme": "https",
  "secure_context": true, "host": "s2c.example.com", "public_url": "",
  "behind_proxy": true, "trust_proxy": true, "force_https": false,
  "cookie_mode": "auto", "cookie_secure": true,
  "headers": { "x-forwarded-proto": "https", "x-forwarded-for": "203.0.113.9",
               "x-forwarded-host": "s2c.example.com", "host": "s2c.example.com" },
  "build": "97209b98",
  "checks": [ { "id": "forwarded_proto", "ok": true, "detail": "…", "fix": "…" }, … ],
  "problems": [],
  "verdict": "proxy headers look correct"
}
```

Check ids, each with a `detail` you can paste into a bug report and a `fix`:
`forwarded_proto`, `scheme_trusted`, `redirects_relative`, `cookie_secure`,
`secure_context`, `public_url`, `proxy_hops`. `/healthz` also gained `build`,
which is the quickest way to confirm a deploy actually landed:

```bash
curl -fsS https://s2c.example.com/healthz | python -c 'import json,sys;print(json.load(sys.stdin)["build"])'
```

#### Reproducing the proxy locally

`scripts/dev_tls_proxy.py` is a 200-line asyncio Traefik stand-in: terminates
TLS, forwards `X-Forwarded-*`, and can misbehave on demand.

```bash
openssl req -x509 -newkey rsa:2048 -nodes -keyout /tmp/key.pem -out /tmp/cert.pem \
    -days 2 -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

python3 scripts/dev_tls_proxy.py --listen 8443 --plain-port 8080 \
    --upstream 127.0.0.1:8000 --cert /tmp/cert.pem --key /tmp/key.pem \
    --redirect-http 308          # 308 = the recommended Traefik setting
```

Switches that reproduce each real-world failure:
`--no-forwarded-proto` (proxy drops the header), `--redirect-http 302` (POST
becomes GET), `--buffer` (a buffering proxy — SSE freezes),
`--strip-cookie-secure` (an intermediary that rewrites `Set-Cookie`).

Before the fix, `--no-forwarded-proto` produced exactly the loop:

```
$ curl -sk -X POST https://127.0.0.1:8444/api/login/ -D - -o /dev/null | grep -i location
location: http://127.0.0.1:8444/api/login      # → browser goes to http → proxy → …
```

After it:

```
location: /api/login                            # relative: scheme preserved
```

### The intended two-device use

* **Laptop:** log in, open `/s2c`, type a message, *Start transfer*. The page plays
  the frames (PNG, un-resized, full-screen button available).
* **Phone:** same URL, log in, open `/s2c`, press the camera button, photograph the
  laptop screen; the phone uploads the still, the server decodes it, and both
  devices advance automatically. The receipt shows sha1 and offers the payload as
  a download (`/api/suite/s2c/<id>/payload`).
* **Dataset capture** works the same way: laptop opens `/display` (code + pairing
  code), phone opens `/camera`, joins with the pairing code, photographs each
  planned frame; each accepted photo advances both devices.

---

## 3. Run it locally (no Docker)

```bash
cd /path/to/repo
python -m pip install -r requirements-suite.txt     # or requirements-web.txt for the capture tool only
export DATA_DIR=$PWD/.data                          # any writable directory
export APP_PASSWORD=demo APP_SECRET=demo-secret COOKIE_SECURE=0
export PYTHONPATH=$PWD
python -m uvicorn optical_modem.suite.app:app --host 127.0.0.1 --port 8200 --workers 1
```

State without Docker, three options — all exercised:

```bash
# 1. no configuration at all: SQLite at $DATA_DIR/suite.db + in-process bus
python -m uvicorn optical_modem.suite.app:app --port 8200

# 2. a real Postgres (DATABASE_URL is rewritten to the psycopg driver)
export DATABASE_URL=postgresql://suite:suite@127.0.0.1:5432/suite

# 3. no Redis installed? run the protocol-compatible stand-in and point at it
python scripts/dev_redis.py 6380 &        # fakeredis over TCP (dev/CI only)
export REDIS_URL=redis://127.0.0.1:6380/0
```

`SUITE_DB=0` / `SUITE_REDIS=0` switch each layer off; the app keeps working and
`/healthz` says so. Boot reconciles the database with whatever is already on the
volume, so pointing a fresh database at an old `$DATA_DIR` shows your existing
datasets/runs/models instead of an empty list.

Rules that are not optional:

* **exactly one uvicorn worker.** Sessions, transfers and the job registry are
  in-process state. Two workers = a phone that joins a session the display cannot
  see, and `404`s on `/api/suite/s2c/<id>/frame.png`.
* **`DATA_DIR` must be set** for anything that imports the suite outside Docker,
  including scripts that only want `suite.s2c.S2C`. The capture app raises
  `PermissionError: /data` otherwise.
* If the port is already bound (`[Errno 98] address already in use`), stop the old
  process first — killing by port with `fuser` can leave the listener behind.

---

## 4. Repository map

```
optical_modem/
  config.py            ModemConfig, DisplayProfile, modulation/ECC profiles, capacity table
  phy/render.py        render_frame / render_screen, screen_geometry()
  phy/channel.py       ChannelConfig, simulate(), sample_quad(), warp_frame(),
                       rect_quad(), simulate_screen(), grid_quad_from_frame_quad()
  phy/locate.py        Quad, order_quad, quad geometry helpers
  phy/classical.py     contour hypotheses + QuadDecoder (ring-scored demodulation)
  phy/mask.py, ecc.py  whitening masks, RS/LDPC wrappers
  protocol/finder.py   finder catalogue (10 shapes), spans, placement, scoring
  protocol/frames.py   FrameCodec: header+payload placement, FEC, interleave
                       (+ the disjointness check that makes finders non-destructive)
  protocol/packet.py   Packetizer: framing/reassembly, CRC, duplicate accounting
  receiver.py          HybridReceiver: mode = auto | yolo | classical
  detect/yolo.py       YoloFrameDetector (YOLO26-pose, 4 keypoints = grid corners)
  sim.py               prepare_frame(), capture(), crop helpers
  data/synth.py        synthetic dataset generator (the thing the dataset stage runs)
  data/train_yolo.py   CPU trainer → runs/<name>/weights/best.pt + model_card.json
  suite/app.py         THE web app: all pages + /api/suite/* + adopted capture routes
  suite/jobs.py        subprocess job runner (disk-backed, cancel, SIGKILL reporting,
                       emits job.* to the bus + records each job row in the database)
  suite/db.py          SQLAlchemy store: projects, datasets, runs, models, jobs,
                       transfers, events; sync_from_disk() boot import; SQLite/Postgres
  suite/bus.py         event bus: Redis (pub/sub, capped ring, SET NX EX locks,
                       heartbeats) with an in-process fallback
  suite/registry.py    datasets/runs/models on disk, active model, describe_env()
  suite/datasets.py    synth / captures / merge / preview
  suite/tasks.py       CLI entry points the jobs call (synth|captures|merge|info)
  suite/s2c.py         S2C engine: transmit, frame PNG, decode, loopback, payload
  suite/static/**      pages + JS + CSS (no build step, no CDN); /projects page lives here
  capture/app.py       capture station (adopted wholesale by the suite, minus “/”)
  capture/sessions.py  session plan (make_plan) — frames the stations will shoot
  capture/static/**    display + camera pages, PWA manifest, service worker, icons
scripts/
  captures_to_dataset.py   photos → YOLO-pose dataset (build())
  evaluate.py              decode-rate / corner-error evaluation, --weights aware
  suite_smoke.py           end-to-end HTTP smoke test (use this after every deploy)
models/yolo26n_optical/    bundled CPU detector + model_card.json (the default active model)
tests/                     207 tests: phy, protocol, receiver, detector, capture, synth,
                           transfer, suite, finder, store (SQLite), bus (fakeredis over TCP)
docs/                      PROTOCOL, MODEL, BENCHMARKS, CAPTURE_APP, SUITE_HANDOFF (this file)
```

---

## 5. Environment variables

Read by the suite (`optical_modem/suite/app.py`, `registry.py`):

| var | default | effect |
|---|---|---|
| `DATA_DIR` | `/data` | root of datasets/runs/models/jobs/sessions |
| `APP_PASSWORD` | *(required)* | password for `/login` and `/api/login` |
| `APP_SECRET` | random → `$DATA_DIR/secret.key` | session cookie HMAC |
| `COOKIE_SECURE` | `auto` | `auto` \| `1` \| `0`. `auto` = `Secure` when `X-Forwarded-Proto: https`, plain otherwise |
| `TRUST_PROXY` | `1` | `0` ignores `X-Forwarded-*` (only when the app faces the internet directly) |
| `FORCE_HTTPS` | `0` | `1` = the app itself 308s plain http to https. Leave `0` behind Traefik |
| `PUBLIC_URL` | *(unset)* | external origin used for links the UI prints |
| `SUITE_PYTHON` | `sys.executable` | interpreter used for jobs |
| `SUITE_MAX_JOBS` | `1` | concurrent jobs (`2` on a big box) |
| `S2C_DECODE_MODE` | `auto` | `auto` \| `yolo` \| `classical` for every S2C decode |
| `AUTOLABEL`, `MAX_UPLOAD_MB` | `1`, `24` | capture-station behaviour |
| `YOLO_CONFIG_DIR`, `YOLO_OFFLINE` | `/data/.ultralytics`, `1` | Ultralytics |
| `DATABASE_URL` | `sqlite:///$DATA_DIR/suite.db` | Postgres (`postgresql+psycopg://…`) or SQLite; a bare `postgres://` is rewritten to the psycopg driver |
| `SUITE_DB` | `1` | `0` = no database at all (filesystem-only) |
| `REDIS_URL` | *(unset)* | e.g. `redis://redis:6379/0`; unset = in-process bus |
| `SUITE_REDIS` | `1` | `0` = force the in-process bus even if `REDIS_URL` is set |

Read by `optical_modem/net.py` (proxy awareness — see §2.2 for the full story):
`COOKIE_SECURE`, `TRUST_PROXY`, `FORCE_HTTPS`, `PUBLIC_URL`.

Read by the trainer / generator: `--workers`, `--device`, plus the standard
`OMP_NUM_THREADS`/`MKL_NUM_THREADS`.

---

## 6. The five stages — runbook

All API calls need the session cookie from `POST /api/login`. The UI does exactly
these calls, so anything below can be reproduced from a browser's network tab.

```bash
BASE=https://YOUR_DOMAIN
JAR=/tmp/suite.cookies
curl -sc $JAR -X POST $BASE/api/login -H 'content-type: application/json' \
     -d '{"password":"'"$APP_PASSWORD"'"}'
```

**Every dataset/train/evaluate/promote endpoint returns before the job finishes.**
It answers `{"job": {...}, ...}`; poll `GET /api/jobs/<id>` until `live == false`.
Posting the same name while a job is running yields **409 Conflict** — that is a
feature (it stops two clicks from creating two datasets), not a bug.

### Stage 1 — create the dataset (`/dataset`)

Three sources, all optional, merged in stage 1b:

**1a-i. Synthetic** (unlimited, free, and the only way to cover 13 modulations):

```bash
curl -sb $JAR -X POST $BASE/api/suite/datasets/synth \
  -H 'content-type: application/json' \
  -d '{"name":"synth-001","train":300,"val":60,"seed":1,"level":1.0}'
```

`level` multiplies the channel severity (0.5 clean … 1.3 harsh). Measured on the
sandbox: 324 images ≈ 15 s. Output: `$DATA_DIR/datasets/synth-001/{images,labels,data.yaml}`.

Each sample is a simulated camera capture. **~40 % of samples are rendered on a
16:9 *display canvas*** (code centred on a mostly black screen) because that is what
`/s2c` actually shows; the rest are square full-frame renders. Do not remove that
split — a detector trained only on square renders fails on the S2C display (see §9.2).

**1a-ii. Photos from the capture station** (real lens, real screen, real light):

*Laptop:* `/display` → creates a session (seed, count, resolution, modulations) and
shows frame *n* plus a **pairing code**.
*Phone:* `/camera` → joins with the pairing code → photographs the screen → upload
→ both devices advance automatically.

The same flow over HTTP:

```bash
S=$(curl -sb $JAR -X POST $BASE/api/sessions -H 'content-type: application/json' \
      -d '{"seed":5,"count":12,"resolution":"1920x1080","modulations":["rgb8","rgb16t4"]}')
SID=$(echo "$S" | python -c 'import sys,json;print(json.load(sys.stdin)["id"])')
CODE=$(echo "$S" | python -c 'import sys,json;print(json.load(sys.stdin)["code"])')
curl -sb $JAR -X POST $BASE/api/sessions/$SID/join \
     -H 'content-type: application/json' -d '{"role":"camera","code":"'"$CODE"'"}'
curl -sb $JAR $BASE/api/sessions/$SID/frame.png -o frame.png
# photos must be JPEG (image/jpeg); index must equal the session's current index
curl -sb $JAR -X POST $BASE/api/sessions/$SID/shots \
     -F file=@frame.jpg -F index=0 -F device=phone -F via=upload
```

Upload responses: `{"ok":true,"shot":{...,"label":{...}},"state":{...}}`.
`400` = bad image/indices, `409` = stale index (double tap), `413` = too big.

Then turn the accepted photos into a dataset:

```bash
curl -sb $JAR -X POST $BASE/api/suite/datasets/captures \
  -H 'content-type: application/json' \
  -d '{"name":"real-001","val_frac":0.15,"verified_only":true}'
```

**1b. Merge** the sources into the one dataset you train on:

```bash
curl -sb $JAR -X POST $BASE/api/suite/datasets/merge \
  -H 'content-type: application/json' \
  -d '{"name":"train-001","sources":["synth-001","real-001"]}'
```

Merged output uses the canonical `data.yaml` (+ `flip_idx: [1,0,3,2]`,
`kpt_shape: [4,3]`). Preview a merged image with boxes drawn:

```bash
curl -sb $JAR "$BASE/api/suite/datasets/train-001/preview?split=train&index=0" -o preview.jpg
```

### Stage 2 — train (`/train`)

```bash
curl -sb $JAR $BASE/api/suite/train/defaults | python -m json.tool   # read "suggested"
curl -sb $JAR -X POST $BASE/api/suite/train -H 'content-type: application/json' -d '{
  "dataset":"train-001","name":"run-001",
  "epochs":40,"imgsz":448,"batch":8,"workers":2,"device":"cpu","model":"yolo26n-pose.pt"}'
```

* The suggested `epochs/imgsz/batch` come from `describe_env()` and **follow
  available RAM** (CPU: `448/8` on ≥6 GB, `448/4` on ≥3 GB, `320/2` below that).
  Take the suggestion the first time.
* `model` can be `yolo26n-pose.pt` (downloads from Ultralytics the first time;
  keep `YOLO_OFFLINE=0` for that one run), `yolo26s/m-pose.pt`, a path to a
  previous run's `best.pt` (fastest convergence), or nothing when
  `models/yolo26n_optical/weights/best.pt` is already in the image.
* `describe_env()` also reports disk and memory; the UI shows both.
* Progress: `GET /api/jobs/<id>?lines=40` — `progress`, `message`, `metrics`
  (`precision/recall/map50/map50_95` from the last validation pass), and `tail`.
* Cancel: `POST /api/jobs/<id>/cancel` (kills the process group).

Measured cost on the sandbox (2 vCPU, `batch 2`, `imgsz 320`): ~10 s per epoch at
324 images. On the 4-core VPS with `batch 8 / imgsz 448`, expect 45–90 s per epoch.

### Stage 3 — evaluate (`/evaluate`)

```bash
curl -sb $JAR -X POST $BASE/api/suite/runs/run-001/evaluate -H 'content-type: application/json' \
  -d '{"n":12,"imgsz":448,"levels":"0.2,0.6,1.0","dataset":"train-001"}'
curl -sb $JAR $BASE/api/suite/runs/run-001/eval | python -m json.tool
```

The report is per channel level × receiver mode (`classical`, `yolo`, `auto`) with
`decode_rate`, `detected`, `detection_rate`, `corner_err_median`,
`corner_err_p90`, `corner_err_pct_median`, `corner_err_pct_p90`, `ms_per_frame`,
`ms_mean`. `auto` must never be worse than either half — if it is, that is a bug
in `HybridReceiver.decode`.

Reading the numbers: the evaluation images are *dataset* images, i.e. deliberately
hard (levels up to 1.15), so a 30–60 % decode rate there is normal and does not
mean the modem is broken. The honest quality signal is `corner_err_pct_median`
(median corner error as a % of the frame side): under ~2 % is a good detector, and
`auto` decode rate should track `classical` closely.

### Stage 4 — promote (`/models`)

```bash
curl -sb $JAR -X POST $BASE/api/suite/models/promote -H 'content-type: application/json' \
  -d '{"run":"run-001"}'
```

Promotion copies `runs/<name>/weights/best.pt` → `models/<name>/weights/best.pt`,
writes the model card, and points `models/active.json` at it. The S2C engine picks
it up on the next request (no restart, but the cached receiver is rebuilt per
transfer). `POST /api/suite/models/<name>/activate` re-activates an older model;
`DELETE /api/suite/models/<name>` removes one (never the bundled fallback).

### Stage 5 — S2C: send something (`/s2c`)

```bash
# transmit
T=$(curl -sb $JAR -X POST $BASE/api/suite/s2c/transmit -H 'content-type: application/json' \
    -d '{"text":"hello from the screen","name":"note.txt",
         "modulation":"rgb8","ecc":"rs32","resolution":"1280x720"}')
TID=$(echo "$T" | python -c 'import sys,json;print(json.load(sys.stdin)["transfer"]["id"])')

# the frame the laptop displays (PNG, never resized: resizing a rendered code
# destroys it) and a JPEG preview for lists/thumbnails
curl -sb $JAR $BASE/api/suite/s2c/$TID/frame.png?index=0 -o frame0.png
curl -sb $JAR $BASE/api/suite/s2c/$TID/preview.jpg?index=0 -o thumb.jpg

# the phone uploads a still (any size/format OpenCV can read)
curl -sb $JAR -X POST $BASE/api/suite/s2c/$TID/receive -F file=@photo.jpg

# simulate a handheld capture, decode it, and compare corner error to ground truth
curl -sb $JAR -X POST $BASE/api/suite/s2c/$TID/loopback \
     -H 'content-type: application/json' -d '{"level":0.25,"frames":2,"seed":0}'
curl -sb $JAR -X POST $BASE/api/suite/s2c/$TID/reset        # forget received frames

# once complete: the reassembled bytes
curl -sb $JAR $BASE/api/suite/s2c/$TID/payload -OJ
```

Defaults (deliberately robustness-first, see §8.3): `cells` auto = **32** at 720p /
**40** at 1080p, `symbol_px ≥ 16`, `repeat 2`, `fps 3`, `ecc rs32`, `modulation rgb8`.
Override `cells`/`symbol_px` in the payload for more bytes per frame at the cost of
decode rate. `S2C_DECODE_MODE=classical` makes the whole page run without YOLO
(useful to prove the detector is not hiding a demodulation problem).

---

## 7. Data & contracts

### 7.1 On-disk layout (`$DATA_DIR`)

```
datasets/<name>/images/{train,val}/*.jpg
datasets/<name>/labels/{train,val}/*.txt     # YOLO-pose, one line per frame
datasets/<name>/data.yaml
datasets/<name>/manifest_{train,val}.json    # synth only: per-sample metadata
runs/<name>/weights/{best.pt,last.pt}
runs/<name>/model_card.json                  # written by data/train_yolo.py
runs/<name>/eval.json                        # written by scripts/evaluate.py
models/<name>/weights/best.pt + model_card.json
models/active.json                           # {"name","weights","promoted",...}
jobs/<job-id>/{job.json,log.txt}
sessions/<session-id>/{session.json,shots/*.jpg}
secret.key                                   # only if APP_SECRET was unset
```

### 7.2 Label format (YOLO-pose)

One line per image, 17 numbers:

```
<class> <cx> <cy> <w> <h>  k0x k0y k0vis  k1x k1y k1vis  k2x k2y k2vis  k3x k3y k3vis
```

`class 0 = optical_frame`; keypoints are the **grid** corners (quiet zone excluded)
in the transmitter's own orientation: `0=TL 1=TR 2=BR 3=BL`; `vis ∈ {0,1,2}`
(2 = visible). Coordinates are normalised to the *captured photo*, not the render.
A negative or >1 value makes Ultralytics silently drop the image, so the writer
clamps and drops degenerate samples (see §9.4).

### 7.3 Job records

`jobs/<id>/job.json` = `{id, kind, title, argv, status, created, started, finished,
returncode, meta, progress, message, metrics, error}` with
`kind ∈ {synth, captures, merge, train, evaluate, promote}` and
`status ∈ {queued, running, ok, failed, cancelled, interrupted}`. `log.txt` holds
the raw stdout+stderr. A job that was running when the container died is reported
as `interrupted`, never as still running.

### 7.4 API surface (all JSON unless noted; all require the session cookie)

Pages: `GET /`, `/login`, `/guide`, `/dataset`, `/train`, `/evaluate`, `/models`,
`/s2c`, plus the adopted capture pages `/display`, `/camera` (their `/` is replaced
by the suite dashboard).

| method & path | body / query | returns |
|---|---|---|
| `POST /api/login` | `{"password"}` | `{"ok":true}`, sets cookie |
| `GET /api/suite/status` | – | counts + `active` model |
| `GET /api/suite/env` | – | python/torch/ultralytics versions, cpu, mem, disk |
| `GET /api/suite/datasets` | – | list with `images`, `splits`, `yaml` |
| `POST /api/suite/datasets/synth` | `{name,train,val,seed,level}` | `{job,dataset}` |
| `POST /api/suite/datasets/captures` | `{name,val_frac,verified_only}` | `{job,dataset}` |
| `POST /api/suite/datasets/merge` | `{name,sources[]}` | `{job,dataset}` |
| `GET /api/suite/datasets/{name}` | – | detail + per-split counts |
| `GET /api/suite/datasets/{name}/preview` | `split,index` | `image/jpeg` (boxes drawn) |
| `DELETE /api/suite/datasets/{name}` | – | `{ok}` |
| `GET /api/suite/train/defaults` | – | `{env, memory, suggested, datasets, runs, bundled_bases, base_models, active}` |
| `POST /api/suite/train` | `{dataset,name,epochs,imgsz,batch,workers,device,model,seed}` | `{job,run}` |
| `GET /api/suite/runs` | – | runs + `active` |
| `POST /api/suite/runs/{name}/evaluate` | `{n,imgsz,levels,dataset}` | `{job}` |
| `GET /api/suite/runs/{name}/eval` | – | the report (per level × mode) |
| `GET /api/suite/models` | – | registry + cards |
| `POST /api/suite/models/promote` | `{run}` | `{active,models}` |
| `POST /api/suite/models/{name}/activate` | – | `{active,models}` |
| `DELETE /api/suite/models/{name}` | – | `{ok}` |
| `GET /api/jobs` | `kind,limit` | `{jobs:[...]}` |
| `GET /api/jobs/{id}` | `lines` | snapshot incl. `tail` |
| `POST /api/jobs/{id}/cancel` | – | snapshot |
| `GET /api/suite/s2c/options` | – | modulations, eccs, resolutions, transfers, active |
| `POST /api/suite/s2c/transmit` | `{text\|data_b64,name,modulation,ecc,resolution,cells,symbol_px,repeat,fps}` | `{transfer,result}` |
| `GET /api/suite/s2c/transfers` | – | list |
| `GET /api/suite/s2c/{id}` | – | one transfer + `result` |
| `GET /api/suite/s2c/{id}/frame.png` | `index` | PNG |
| `GET /api/suite/s2c/{id}/preview.jpg` | `index,max_px` | JPEG |
| `GET /api/suite/s2c/{id}/payload` | – | the bytes (409 until complete) |
| `POST /api/suite/s2c/{id}/receive` | multipart `file` | `{transfer,result,decode}` |
| `POST /api/suite/s2c/{id}/reset` | – | clears received frames |
| `POST /api/suite/s2c/{id}/loopback` | `{level,frames,seed}` | per-frame rows + `corner_err_median` |
| `GET /api/suite/export/manifest` | – | everything the box holds, as JSON |
| `GET /healthz`, `GET /version` | – | unauthenticated |

Capture-station routes (adopted): `GET /api/capabilities`, `POST /api/sessions`,
`GET /api/sessions`, `GET /api/sessions/{sid}`, `POST /api/sessions/{sid}/join`,
`GET /api/sessions/{sid}/frame.png`, `POST /api/sessions/{sid}/shots`,
`POST /api/sessions/{sid}/{reject,skip,seek}`, `POST /api/logout`, `GET /api/me`.

### 7.5 Projects = state (`/projects`, database-backed)

A project is the durable record of a piece of work: **development** while you are
still iterating, **finished** when it is shipped (or **archived** to park it).
Datasets, runs, models, jobs and transfers can point at one; deleting a project
*detaches* them by default, so deleting never destroys data by accident.

```bash
# create (slug is derived; status defaults to development)
curl -b cookies -X POST http://HOST:8000/api/suite/projects \
  -H 'Content-Type: application/json' \
  -d '{"slug":"v0.3-hd","title":"v0.3 HD + finder patterns","tags":["hd","finder"]}'
# -> {"project":{"id":1,"slug":"v0.3-hd","status":"development",...,"n_datasets":0}}

curl -b cookies -X PATCH http://HOST:8000/api/suite/projects/v0.3-hd \
  -H 'Content-Type: application/json' -d '{"status":"finished"}'
curl -b cookies http://HOST:8000/api/suite/projects/v0.3-hd     # + its artefacts
curl -b cookies -X DELETE 'http://HOST:8000/api/suite/projects/v0.3-hd?reassign_to=v0.4'

# every artefact endpoint takes an optional "project" slug
curl -b cookies -X POST http://HOST:8000/api/suite/datasets/synth \
  -H 'Content-Type: application/json' -d '{"name":"hd-001","project":"v0.3-hd"}'
# -> 400 {"detail":"no project 'v0.3-hd' — create it on /projects"} if it does not exist
```

| route | purpose |
|---|---|
| `GET/POST /api/suite/projects`, `GET/PATCH/DELETE /api/suite/projects/{key}` | project CRUD; `?status=development\|finished\|archived` filter |
| `GET /api/suite/state` | one call with counts, all projects/artefacts, recent events and the store/bus info (the Projects page loads only this) |
| `GET /api/suite/events?limit=&kind=` | the bus backlog (Redis list when Redis is on) |
| `GET /api/suite/progress` | the guided flow: six stages, one runnable next action (§9.5) |
| `GET /api/suite/finder/{shape}.svg`, `/api/suite/palette`, `/api/suite/runs/{name}/curve` | the data behind the visuals (§9.5.3) |
| `GET /api/events` | Server-Sent Events: backlog, then live `job.queued/started/finished/failed`, `project.*`, `dataset.upserted`, `model.promoted`, `s2c.*` — keep-alive included |

Schema (SQLAlchemy, engine-agnostic):

| table | columns that matter |
|---|---|
| `projects` | `slug` (unique), `title`, `description`, `status`, `tags`, `settings` JSON, `created`, `updated` |
| `datasets` | `name` (unique), `project_id`, `source` (`synth`/`captures`/`merge`/`disk`), `path`, `yaml`, `images`, `splits` JSON, `bytes`, `params` JSON, `status` (`building`/`ready`/`failed`) |
| `runs` | `name` (unique), `dataset`, `epochs`, `imgsz`, `batch`, `device`, `base_model`, `status`, `metrics` JSON, `wall_seconds`, `weights`, `path` |
| `models` | `name` (unique), `run`, `weights`, `card` JSON, `metrics` JSON, `active` (exactly one row) |
| `jobs` | `id`, `kind`, `title`, `status`, `progress`, `message`, `error`, `meta` JSON, `metrics` JSON, `created`/`started`/`finished`, `returncode`, `heartbeat` |
| `transfers` | `id`, `bytes`, `sha1`, `frames`, `modulation`, `ecc`, `resolution`, `finder`, `params` JSON, `complete`, `received`, `duplicates`, `failed` |
| `events` | `kind`, `subject`, `project_id`, `detail` JSON, `ts` |

Redis keys: channel `optical-suite:jobs`, list `optical-suite:events` (capped),
locks `optical-suite:lock:<name>`, heartbeats `optical-suite:hb:<job>`.

Without a Postgres to talk to, the same API is served from SQLite — the schema,
the queries and the tests are identical (`tests/test_store.py` runs on SQLite on
purpose, `tests/test_bus.py` runs the Redis path over a real socket via
`fakeredis.TcpFakeServer`).

### 7.6 CLIs

```bash
# suite subprocess entry points (what the jobs run)
python -m optical_modem.suite.tasks synth    --out DIR [--train N --val N --seed S --level L --workers W]
python -m optical_modem.suite.tasks captures --data-dir DATA --out DIR [--val-frac F --verified-only --min-score X]
python -m optical_modem.suite.tasks merge    --out DIR --source DIR [--source DIR ...] [--move]
python -m optical_modem.suite.tasks info                      # capacity table as JSON

# training / evaluation / datasets directly
python -m optical_modem.data.train_yolo --data <data.yaml> --epochs 40 --imgsz 448 --batch 8 \
       --device cpu --workers 2 --mosaic 0.3 --project runs --name run-001 --seed 0
python scripts/evaluate.py --n 25 --imgsz 448 --levels 0.2,0.6,1.0 --weights run/weights/best.pt --out run/eval.json
python scripts/captures_to_dataset.py --data-dir DATA --out DIR [--val-frac .15 --verified-only]

# the modem itself
python -m optical_modem info
python -m optical_modem render --text "hello" --resolution 1280x720 --out frame.png
python -m optical_modem sim --level 0.6 --n 20
python -m optical_modem receive photo.jpg
python -m optical_modem send --file README.md --ecc rs16
python -m optical_modem bench --out docs/BENCHMARKS.md
python -m optical_modem gui --port 8000        # standalone GUI (dev only)
```

---

## 8. Protocol / physics facts the code depends on

1. **The frame is a square code** of `cells × cells` cells, 1-cell calibration ring,
   4 header rows, then payload. `cells` (grid) and `cell_px` are independent knobs;
   capacity comes from `cells² × bits`.
2. **Never resize a rendered code.** `frame.png` is served at native resolution and
   `#video`/`<img>` use `object-fit: contain`. Downscaling destroys sub-cell
   registration. (`preview.jpg` exists only for thumbnails.)
3. **Robustness beats capacity.** With the same detector, on 12 simulated handheld
   captures (level 0.2–0.5, 1080 px): 50 cells @12 px → 7/12 frames decoded;
   36 cells @16 px → 9/12; **32 cells @18 px → 10/12**; 32 cells @18 px is therefore
   the default at 720p (payload 222 B/frame with `rgb8/rs32`). Raise `cells` when
   you need throughput and can afford retries.
4. **The display canvas is 16:9, the code inside it is square.** `simulate()` must
   preserve the source aspect ratio (`sample_quad(..., aspect=w/h)`); mapping a
   1280×720 canvas onto a square quad squeezes it 2.4:1 and no receiver can undo
   that. This was a real bug (§9.1).
5. **YOLO is trained at `imgsz=448`** and should be *run* at 448. Running this
   pose model at 640 made keypoints come back ~0.7× the right size on square
   inputs in this Ultralytics build (8.4.x); the box was correct, the keypoints
   were not. If you change `yolo_imgsz`, verify corner error on a rendered frame
   first (`python -m optical_modem.sim --level 0`).
6. **Keypoints are the grid corners, not the frame outline.** `QuadDecoder` expects
   the grid quad; `grid_quad_from_frame_quad()` converts from a frame/canvas quad and
   `rect_quad()` converts a rectangle of any source image (e.g. a display canvas
   grid rect from `screen_geometry()`).
7. **The header is RS(16) and must decode before the payload mask is known.** A
   failed header is reported as `header:ValueError`; that is a *demodulation*
   failure, not a framing bug. `ring_score` next to it tells you whether geometry
   was right (≈1.0 = the quad is correct, the bits are not).
8. **One frame < one payload is normal.** A one-frame message is encoded as
   `FRAME_START` so the receiver learns `total`; without that a single-frame
   transfer could never complete (§9.3).
9. **Capacity reference** (payload bytes per frame, `RS` profiles as in
   `docs/PROTOCOL.md`): 1280×720 `rgb8` 50 cells/12 px → 728 B; `rgb16t4` 36/16 →
   717 B; `rgb32t16` → 1067 B; 1920×1080 `rgb8` → 2020 B; `rgb32` → 3367 B.

---

### 8.6 Finder patterns (optional, never destructive)

A finder is a small high-contrast shape repeated in the inner corners (or centre
/ edges) of the code, so a detector and a classical localiser have an
unambiguous anchor at any scale and rotation. It is **optional** — `finder:
"none"` is the default and costs nothing.

The catalogue (`optical_modem/protocol/finder.py`) is a list of shapes the UI
offers, so a user picks one instead of inventing one:

| shape | mask | symbols/corner | note |
|---|---|---|---|
| `ring` | 5×5 ring | 25 | scale-invariant, survives blur best |
| `dice` | 5×5 five-blob | 25 | rotation-symmetric |
| `bullseye` | 5×5 filled ring pair | 25 | strongest radial gradient |
| `diamond`, `cross`, `x`, `bracket`, `triangle` | 5×5 | 25 | orientation hints |
| `checker` | 4×4 | 16 | cheapest, most fragile |
| `solid` | 4×4 block | 16 | maximum contrast, no structure |

Spans: `corners` (default, 4 blocks), `three` (QR-style), `center`, `edges`.
Blocks are placed against the inner corners *inside* the calibration ring, and
`auto_size()` shrinks the block to 3×3 below ~30 symbols per side so a small code
does not lose 13 % of its capacity to an optional aid.

**The no-overwrite guarantee.** `FrameCodec.__init__` carves the finder's symbols
out of the header and payload symbol lists (`header_symbols_xy()` and
`payload_symbols_xy()` skip them) and then asserts that ring / finder / header /
payload are pairwise disjoint *and* cover every symbol of the grid exactly once.
A layout that would paint a finder over data raises `ValueError` at construction
time instead of producing a frame that decodes only for some payload lengths.
`tests/test_finder.py::test_encoded_grid_still_carries_the_finder` checks the
tones survive a real encode; `test_regions_are_disjoint_and_cover_the_grid` runs
the structural check for all ten shapes.

**Cost, measured** (32 cells, `rgb8`, `rs16`, 4 header rows):

| finder | header row growth | payload symbols | body capacity | max user bytes | share |
|---|---|---|---|---|---|
| none | — | 780 | 260 | 258 | 0 % |
| 5×5 shapes (ring, dice, bullseye, …) | +0 (header band already tall enough) | 720 | 239 | 237 | 9.77 % |
| 4×4 shapes (checker, solid) | +0 | 748 | 248 | 246 | 6.25 % |

If the carved header band would be too short for the fixed RS(16) header,
`effective_layout()` grows the band one row at a time until it fits — a finder
can therefore cost a header row, but it can never truncate the header.

The receiver scores every candidate against the catalogue
(`FrameResult.finder_score`, 0–1) and uses it in the hypothesis ranking; without
a finder the field is `None` rather than a fake 0. In the S2C API the finder is a
per-transfer field (`"finder": "ring"`) and the transfer record stores it, so a
dataset/transfer history says which code style was used. The UI shows the real
cost next to the selector ("costs ≈ 9.8 % of the code").

## 9.5 The guided UI — how a user is walked through the box

The product question is not "are the endpoints there", it is "does a person who
has never seen this know what to do next". Four pieces answer it, and they all
read one server-side decision so they can never disagree.

### 9.5.1 `GET /api/suite/progress` — the only opinion about "next"

`optical_modem/suite/progress.py` derives six stages from what actually exists:

| stage | done when | the action it offers |
|---|---|---|
| `env` | torch **and** ultralytics importable | link to the environment section |
| `project` | at least one project row | link to `/projects` |
| `dataset` | any dataset has images | **POST** `/api/suite/datasets/synth` with 120+24 frames |
| `train` | a run has `weights/best.pt` | **POST** `/api/suite/train`, sized to this box's RAM |
| `verify` | a model is active | link to `/models` |
| `send` | a transfer completed with a checksum match | link to `/s2c` |

Each stage carries `done`, `current`, `blocked`, `blockers`/`blocked_by`, a
`checklist` (sub-conditions, shown as ticks) and a runnable `action`. Properties
that are regression-tested (`tests/test_progress.py`, 21 tests):

* exactly one stage is `current`; the ones after it are `blocked`, and the
  `next` stage lists `blocked_by` strings ("Train: no trained run yet");
* the action is *runnable* — `kind: post` means a real endpoint plus a complete
  payload (the Train payload is filled from the same memory-driven defaults the
  Train page suggests, so a 2 GB box is never told `batch: 8, imgsz: 448`);
* with no dataset, the train action degrades to a `link`, never a broken POST;
* the recommendation advances when the artefacts appear — the tests assert
  `project → dataset → train` as each is added.

Nothing here reads a "visited" flag, so a box that is already trained opens on
"verify", not on "welcome, let's start".

### 9.5.2 `/wizard` — the guided setup

`static/wizard.html` + `js/wizard.js`. One step at a time, with:

* a **rail** (left) listing all six stages with live detail lines and ticks;
* a **step card**: a purpose-built SVG illustration per stage, "why this
  matters", the checklist, a warning naming the later steps that are waiting,
  and the action — where possible a button that *does the thing* (generate the
  dataset, train with box-sized defaults, promote the newest run, create a
  first project), with "open the full page →" beside it for anyone who wants to
  change a parameter;
* a **live job panel** (progress bar + log tail) when the action starts a job;
* **auto-advance**: when the current step's `done` flips true, the view moves to
  the next one and says so. It re-reads `/api/suite/progress` on every
  `job.finished`/`dataset.upserted`/`model.promoted`/`s2c.complete` SSE event and
  every 6 s as a fallback, so a job that outlives the tab still ticks the step.

Keyboard: ←/→ move between steps.

### 9.5.3 Visual language: `static/js/viz.js`

No chart library, no CDN, no build step — every visual is an SVG string built
from data, so it works offline in the PWA and in the sandboxed preview iframe:

| helper | used by | shows |
|---|---|---|
| `pipeline(stages)` | dashboard, guide, wizard | screen → optics → camera → detector → FEC → file, solid green for stages that have run, dashed for waiting |
| `ring(pct)` | dashboard, wizard | completion, as a number and an arc |
| `lineChart(epochs, series)` | `/train` | training curves from the run's `results.csv` |
| `bars(items)` | `/evaluate` | decode rate per channel level |
| `finderTile`, `swatchStrip` | `/s2c` | the finder catalogue and each modulation's palette |
| `icon(name)` | nav, rail, empty states | one stroke-only icon set |

Server-side counterparts added in the same session:

| route | purpose |
|---|---|
| `GET /api/suite/finder/{shape}.svg?size=` | a shape drawn from the *same mask the transmitter uses* (`finder.SHAPES`), so the thumbnail cannot drift from the code |
| `GET /api/suite/palette?cells=&ecc=` | per modulation: bit depth, palette hexes, bytes/frame — the data behind the swatches |
| `GET /api/suite/runs/{name}/curve` | `results.csv` → `{epochs, series}`; empty (not an error) when there is no CSV |

The finder picker on `/s2c` is now visual: eleven tiles with thumbnails, the
cost shown as "≈ 9.8 % of the code", and the `<select>` kept in sync for
keyboard/accessibility.

### 9.5.4 Reviewing the visuals without a browser

`scripts/ui_preview.mjs` renders the real components with the real CSS and the
*server's* live state into one self-contained HTML file:

```bash
node scripts/ui_preview.mjs --base http://127.0.0.1:8200 --password demo \
     --out devdata/ui_preview.html      # ~78 kB, everything inlined
```

It contains the dashboard pipeline + ring, the wizard rail and a step card, all
six step illustrations, the finder contact sheet, the palette table, the training
curve and the empty state. Useful for design review, tickets, and for checking
the charts on a headless box (this sandbox has no browser).

## 9. Bug ledger (fixed this session — regression-tested; do not re-fix differently)

Each entry: symptom → root cause → fix → test that now guards it.

### 9.1 A simulated capture of a 16:9 frame was undecodable (aspect distortion)

* **Symptom:** `POST /api/suite/s2c/<id>/loopback` reported `header:ValueError` and
  a corner error of ~0.25 for every frame, at every noise level, while a pristine
  render decoded 100 %. Pictures showed the code squeezed into a tall rectangle.
* **Cause:** `phy/channel.py::sample_quad()` built a **square** destination quad
  regardless of the source's aspect, so a 1280×720 display canvas was compressed
  2.4:1 (cells became non-square). Benchmarks never caught it because they used
  square renders.
* **Fix:** `sample_quad(..., aspect=fw/fh)` (long side = `out_size * scale`);
  `simulate()` and `simulate_screen()` pass the source aspect; added
  `rect_quad()` to map a rectangle of the source through the placed quad.
* **Tests:** `tests/test_transfer.py::test_simulated_capture_keeps_the_display_aspect_ratio`,
  `::test_simulated_display_frame_decodes_at_moderate_noise`.

### 9.2 The bundled detector could not find a frame shown on the S2C display

* **Symptom:** detection on a 720p display capture returned a quad at ~half the
  true size with 0.9 confidence; decode then failed.
* **Cause:** the synthetic dataset only contained *square* full-frame renders;
  a real S2C frame is a square code inside a wide, mostly black canvas. Different
  composition, absent from training.
* **Fix:** `data/synth.py` now renders ~40 % of samples on a 16:9 display canvas
  (`SampleSpec.display`) and labels them with `rect_quad()` + `screen_geometry()`.
  Retraining through the UI on such a dataset fixed loopback decoding.
* **Where it shows up:** `train-hd`/`run-hd` in the sandbox — 8 epochs on 324 images
  reached `pose_map50_95 0.979` and corner error ~0.04 on loopback captures.

### 9.3 A one-frame transfer could never complete

* **Symptom:** 24-byte text showed "receiving" forever; `received: 0`, `duplicates: 1`.
* **Cause (two, in series):**
  1. `Packetizer.frames_for()` typed the only frame `FRAME_DATA`, so the receiver
     never learned `total` and `push()` returned `None` forever.
  2. `suite/s2c.py` inferred progress from `tr.got`, which it never incremented
     (it compared `tr.got` to itself), so every accepted frame was counted as a
     duplicate.
* **Fix:** a single-frame message is `FRAME_START` (it is its own start and end, so
  the `total + msg_crc` trailer is present); `Packetizer` now owns the counters
  (`received`, `duplicates`, `buffered`, `expected`) and `S2C` copies them; the
  counters survive the buffer reset that happens on completion.
* **Tests:** `tests/test_transfer.py::test_single_frame_transfer_completes[1,12,200]`,
  `::test_multi_frame_transfer_reassembles_out_of_order`.

### 9.4 Synth labels could contain negative coordinates

* **Symptom:** Ultralytics logged `ignoring corrupt image/label: negative label
  values [-0.13 -0.26]` and quietly trained on fewer images.
* **Cause:** the box was the hull of *all* keypoints including those off-canvas, so
  `cx/cy` went negative for frames shouldering out of view.
* **Fix:** clamp the hull to the canvas, drop degenerate views (they are counted as
  `skipped`, not `errors`, in the synth summary), and retry the channel up to twice
  when the frame lands almost entirely off-screen.
* **Tests:** `tests/test_synth.py` (label ranges, `build_dataset` summary accounting).

### 9.5 A SIGKILLed job reported an empty message

* **Symptom:** a training job "failed" in 5.7 s with `message: ""` and a log that
  simply stopped after `Starting training for 10 epochs...`.
* **Cause:** exit code −9 (kernel OOM killer) was rendered as `exit code -9`, and
  `message` was only filled from the log progress parser.
* **Fix:** `jobs._failure_reason()` translates signals (`SIGKILL` → "almost certainly
  out of memory: lower --batch or --imgsz…") and appends the last meaningful log
  line; `message` now always carries the reason for a failed job. `train/defaults`
  also sizes `batch`/`imgsz` from *available RAM*.
* **Tests:** `tests/test_synth.py::test_failure_reason_explains_sigkill`,
  `::test_failure_reason_includes_last_log_line`, `::test_failed_job_surfaces_a_message`.

### 9.6 Smaller fixes from the same sweep

* `S2C` was constructed per request → transfers vanished (`404` on `frame.png`).
  Now one module-level `ENGINE` whose `weights` are rebound from the registry on
  every request.
* `make_sample()` built `FrameLayout(grid=...)` (a `TypeError`): the layout takes
  `cells`/`cps`. Synth had produced **zero** images before this.
* `describe_env()` imported torch (~350 MB RSS in the web worker, on a box where
  training also wants that memory). Versions now come from
  `importlib.metadata`; torch is imported only when `/dev/nvidia*`-class hardware
  is plausible (`nvidia-smi` present).
* The captures-conversion job was filed under `kind="merge"`, which made the UI's
  kind filter lie.
* The dead `b64FromHexPlaceholder()` download link in `s2c.js` was replaced by a
  real endpoint: `GET /api/suite/s2c/<id>/payload` (409 until the transfer completes).

### 9.7 "Traefik forces https and then nothing works"

Four bugs with one root cause — the app believed the socket instead of
`X-Forwarded-Proto` (details and remedies in §2.2): absolute redirects built from
the http scope, `COOKIE_SECURE=1` on an http socket, a hand-versioned service
worker (service workers only exist in secure contexts, so this class of bug can
only appear on https), and `Connection: keep-alive` on the SSE stream. Regression
tests live in `tests/test_net.py` (26 cases: scheme resolution incl. multi-hop and
RFC 7239, `TRUST_PROXY=0`, cookie `auto`/`1`/`0`, `FORCE_HTTPS`, relative-Location
rewriting, `X-Forwarded-Host` → `Host`, build-id stability and stamping, and the
`diagnose` verdicts — including the "reached directly, no headers is normal" case
so a laptop deployment is not flagged). `scripts/check_https.py` covers the parts
a unit test cannot: certificate, real redirect status code, cookie actually
stored and replayed, SSE actually streaming.

---

## 10. Known limitations and the next five things to do

**1. Header robustness on hard captures (highest value).**
Symptom: `header:ValueError` with `ring_score ≈ 1.0`; roughly 25–50 % of frames are
lost at handheld level ≥ 0.35, and the geometry is provably right (corner error
~0.04, and the *bare* ground-truth quad can also fail). The header block is only
RS(16) and it must decode before anything else.
Ideas, cheapest first: (a) decode the header twice — once from the raw samples and
once after `illum_normalize`, and pick by ring/CRC; (b) use the soft-bit erasure
path for the header (it exists: `FrameCodec.read_header(..., soft=...)`, but the
callers pass `soft=None` in some paths — check `phy/classical.py`); (c) majority
vote across the `repeat` captures of the same sequence number (the phone takes 2+
photos of each frame; votes could be combined before FEC); (d) raise the header
parity to RS(32) — costs ~2 rows of payload capacity, buys real margin.
Verify with: `POST /api/suite/s2c/<id>/loopback {"level":0.4,"frames":4}` across
`seed` 0–3 — target ≥ 90 % decoded.

**2. Repeat-aware reassembly.** `repeat` is stored but frames are treated singly.
Deduplicate by `seq`, keep the best-quality copy (or combine), and report
`frames_lost` so the UI can suggest `fps`/`repeat`.
Verify: `loopback` at level 0.5 with `repeat 2` → `complete: true`.

**3. A real 2-device test on hardware.** Everything to date is simulation + HTTP.
Do it once on a phone camera: `/display` + `/camera` for dataset capture, then
`/s2c` end to end. Expect to tune `fps` (screen refresh vs shutter), exposure, and
`cells`. Record the outcome in `docs/CAPTURE_APP.md`.

**4. PWA polish.** `manifest.webmanifest` + service worker exist; verify install
prompt on Android Chrome and the "add to home screen" flow, and that the offline
shell does not cache `/api/**` (it must not: stale frames break live transfers).

**5. Throughput story.** `python -m optical_modem bench --out docs/BENCHMARKS.md`
regenerates `docs/BENCHMARKS.md`; it was last produced before the aspect fix, so re-run it and
refresh the numbers (the 720p rows will *improve*). While there, add the
`cells`-vs-decode-rate table from §8.3 so users can see the trade-off they are
tuning on the S2C page.

**6. State-layer follow-ups (done in this session, listed so you can extend them
in the same style).** Postgres holds projects/datasets/runs/models/jobs/
transfers/events; Redis carries job events, locks and heartbeats. Natural next
steps: (a) a single-writer queue in Redis (BLPOP) so a second container could
join as a worker — the lock keys (`optical-suite:lock:*`) are already there;
(b) pruning: `events` and the Redis ring grow forever, add a
`DELETE FROM events WHERE ts < …` job on boot; (c) `GET /api/suite/state`
already exports everything — a `?format=csv` would make the project history
portable; (d) Alembic, if the schema ever changes (today `create_all` is enough
because nothing has shipped yet).

Smaller, uncontroversial items: show the synth summary's `skipped` count on the
dataset page; add a "download dataset as a YOLO zip" button; let the Train page
override `seed`/`mosaic`; show `corner_err` units (fraction of the frame side) in
the evaluate table header.

---

## 11. Verification checklist (run this first on the server)

```bash
# 1. unit tests (no network, no GPU, ~100 s on 2 cores)
PYTHONPATH=. python -m pytest tests -q                # expect: 207 passed
#   finder guarantees:   pytest tests/test_finder.py -q     (37)
#   database behaviour:  pytest tests/test_store.py -q      (25, SQLite)
#   bus over a socket:   pytest tests/test_bus.py -q        (15, fakeredis)

# 2. protocol/physics sanity without a browser
python -m optical_modem info                          # capacity table
python -m optical_modem sim --level 0.3 --n 5         # TX -> camera -> RX, "% decoded"

# 3. the whole pipeline over HTTP (needs the app running + APP_PASSWORD)
python scripts/suite_smoke.py --base http://localhost:8200 --password demo \
       --synth-train 24 --synth-val 8 --train-epochs 1 --train-imgsz 320 --train-batch 2
# expect these lines (the smoke now checks the state layer too):
#   ok   healthz                 db=postgresql ok=True bus=redis
#   ok   project final5-proj   development -> finished -> development
#   ok   dataset row             source=synth status=ready project=final5-proj
#   ok   state                   counts={'projects': 4, 'datasets': 9, ...}
#   ok   transmit                   1 frames, 35 B, 32² cells @18px, 222 B/frame
#   ok   receive (photo upload)     decoded=True complete=True received=1
#   ok   payload download           35 bytes sha1_ok=True
#   PASS  ...
```

Then the state layer — four calls, all of which the Projects page makes for you:

```bash
curl -fsS -b cookies http://localhost:8200/healthz | python -m json.tool   # database.ok, bus.kind
curl -fsS -b cookies -X POST http://localhost:8200/api/suite/projects \
  -H 'Content-Type: application/json' -d '{"slug":"smoke","title":"smoke"}'
curl -fsS -b cookies http://localhost:8200/api/suite/state | python -m json.tool
curl -fsS -N -b cookies http://localhost:8200/api/events | head -20       # SSE, then jobs as they run
```

```bash
# the guided flow, in one call: six stages, one next action, nothing guessed
curl -fsS -b cookies http://localhost:8200/api/suite/progress | python -m json.tool
# then open /wizard and press the offered button — it should create the artefact
# and, once the job finishes, tick the step and move the view on.
node scripts/ui_preview.mjs --base http://localhost:8200 --password demo \
     --out /tmp/ui.html            # visual smoke test with no browser
```

`/healthz` must report `"database": {"ok": true}` and `"bus": {"kind": "redis"}`
in compose (or `"kind": "memory"` without Redis — that is the documented
fallback, not a failure). `tests/test_bus.py` proves fan-out, exactly-once
delivery, `SET NX EX` locks and heartbeats against a real Redis socket.

Reference numbers measured in this session (2 vCPU / 2 GB sandbox, `DATA_DIR`
on local disk):

| stage | settings | time | result |
|---|---|---|---|
| synth | 24 + 8 images | 3.9 s | 24/24 written, 0 errors |
| merge | 1 source | 1.5 s | 24/8 split |
| train | 1 epoch, 320 px, batch 2, from bundled weights | 8.3 s | map50 0.22 (1 epoch is a smoke, not a model) |
| train | 8 epochs, 320 px, batch 2, 324 images | ~180 s | `pose_map50_95 0.979`, `box_map50_95 0.729` |
| evaluate | n=12, levels 0.2/0.6/1.0 | 41 s | rows for classical / yolo / auto |
| promote | – | <1 s | `active` updated, no restart |
| transmit + loopback | rgb8/rs32, 720p, L0.2 | ~3 s/frame | 2/2 frames decoded, corner err 0.05 |
| phone path (PNG upload) | same transfer | 1 s | `complete: true`, `sha1_ok: true` |

If loopback reports `decoded 0/N` **with a fresh, well-trained model**, check in this
order: (1) `S2C_DECODE_MODE=classical` — if classical decodes, the detector is the
problem; (2) `GET /api/suite/status` → is the active model the one you think?;
(3) render a frame and decode it with no channel at all
(`python -m optical_modem sim --level 0`) — that path must be 100 %.

---

## 12. Troubleshooting

| symptom | cause | action |
|---|---|---|
| `404` on `/api/suite/s2c/<id>/frame.png` | more than one uvicorn worker, or the process restarted (transfers are in memory) | run with `--workers 1`; re-transmit after a restart |
| `409` from a dataset/train POST | a job of that name is still running | poll `/api/jobs/<id>`; the request returns before the job ends |
| training `failed` in seconds, log stops mid-epoch, message mentions SIGKILL | OOM | lower `batch` (suggested value follows available RAM), then `imgsz` |
| page loads but every API call `401`s and bounces to `/login` | a `Secure` cookie over plain HTTP (browser drops it silently) | `COOKIE_SECURE=auto` (the default); if you set `1` by hand, unset it |
| **`ERR_TOO_MANY_REDIRECTS` only on https** | the app answers a redirect with `http://…` (behind Traefik the socket really is http), Traefik sends it back to https → loop | fixed in code (`net.ProxySchemeMiddleware` rewrites same-host `Location` to a relative path). If it reappears, `curl -sk https://DOMAIN/api/login/ -D -` must print `location: /api/login`, never an absolute `http://` one |
| **works on http, broken on https** (buttons dead, stale pages, camera page blank) | service worker from an older deploy, or untrusted proxy headers | `python3 scripts/check_https.py --base https://DOMAIN --password …`; hard-reload once; check `COOKIE_SECURE`/`TRUST_PROXY` |
| login form "does nothing" only when the site is served over https | the proxy answers plain http with **302**, which turns the POST into a GET | use Traefik's `redirectscheme` with `permanent=true` (308 — preserves method and body) |
| `/api/net/diagnose` says `X-Forwarded-Proto: (absent)` | a second proxy in front of Traefik replaced the header | append, don't replace (`X-Forwarded-Proto: append`); or set `FORCE_HTTPS=1` and `PUBLIC_URL` |
| live updates (job progress) freeze but a reload catches up | something in the path buffers responses | the SSE endpoint already sends `X-Accel-Buffering: no` and `no-transform`; make sure no proxy middleware buffers `text/event-stream` |
| phone camera button does nothing | not a secure context | serve over HTTPS (Coolify + domain) |
| `header:ValueError` in loopback | demodulation, not framing | see §10.1; compare `S2C_DECODE_MODE=classical` |
| decode works locally, fails from the phone | photo is blurry/downscaled, or the screen shows a resized code | native-size PNG on the display, fill the phone frame, 1080p+ capture, lower `cells` |
| `ImportError: ultralytics` in the web process | you are running the capture-only image | install `requirements-suite.txt` (the suite needs torch/ultralytics) |
| `PermissionError: /data` outside Docker | `DATA_DIR` unset | `export DATA_DIR=/some/writable/dir` |
| Ultralytics tries to download a base checkpoint | `YOLO_OFFLINE=1` | set `YOLO_OFFLINE=0` for that one run, or point `model` at a local `best.pt` |

---

## 13. House rules that hurt when ignored

* **One worker.** Sessions, jobs and transfers are in-process. This is the single
  most common way to break the app.
* **The web process must stay torch-free.** Everything expensive is a subprocess
  (`suite/jobs.py`). Do not import ultralytics/torch at module scope in
  `suite/app.py`; `describe_env()` reads versions from package metadata on purpose.
* **Rendered frames are never resized** (see §8.2). `object-fit: contain`, native PNG.
* **`[Errno 98] address already in use` after a restart** means the old process is
  still listening; stop it properly (SIGTERM to the process group) instead of
  killing by port.
* **This working copy is not a git repository.** If you want diffs, `git init`
  first; otherwise keep a copy of the file you are about to rewrite.
* **Long jobs run as subprocesses of the web server**; if you restart the server
  mid-job, the job is marked `interrupted` and its output is in `jobs/<id>/log.txt`.
* **`.arena/`-style scratch directories are not persisted** in this sandbox. Put
  anything reusable in `scripts/` or `docs/`.
* **Ask before changing the protocol.** `docs/PROTOCOL.md` is the contract; a
  change there invalidates every trained model and every benchmark.
* **The finder is carved, never painted.** Finder symbols are removed from the
  header/payload lists in `FrameCodec.__init__`, which then asserts the regions
  are disjoint and exhaustive. Do not "simplify" that by writing finder colours
  after the payload — the resulting bug only shows up at some payload lengths.
* **The database is metadata, not storage.** Photos, weights, job logs and
  rendered frames stay on `/data`; only rows go in Postgres. Adding a BLOB column
  would make backups slow and the volume pointless.
* **Nothing may depend on Redis or Postgres being up.** `db.py`/`bus.py` both
  degrade (SQLite / in-process) and `_emit()` swallows store errors: a broken
  database must never fail a training run or a transfer.
* **One source of truth for "what is next".** `suite/progress.py` decides; the
  dashboard rail, the wizard, the nav ticks and the guide all render its answer.
  Do not compute a stage's done-ness in a page — that is how a UI starts lying
  about a box that is already trained.
* **Visuals are SVG strings, never a chart library.** The PWA must work offline
  and the preview iframe has no network; `viz.js` is the only place that draws.
* **One source of truth for "is this done"**: artefact *status* is written by the
  job that did the work (`JobManager._settle_artefact`), not by the endpoint that
  queued it — so closing the browser cannot leave a dataset `building` forever.

---

*Written at the end of the session that built the suite, extended by the session
that added the state layer (Postgres + Redis) and finder patterns. Everything in
§6 and §11 was executed on 2026-10-22 in a 2-core / 2 GB sandbox with `DATA_DIR` on
local disk: `pytest tests -q` → 207 passed; `scripts/suite_smoke.py` → PASS with
`db=sqlite ok=True bus=redis` (Postgres/Redis themselves were verified through
`tests/test_store.py` / `tests/test_bus.py`, since this sandbox has no Postgres or
Redis daemon to run against).
If you change a number in this file, run the command that produces it.*
