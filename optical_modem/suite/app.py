"""The suite app: one container, one process, the whole pipeline.

`optical_modem.capture.app` is a complete capture station on its own; this app
*adopts* its routes (all of them except its landing page) and puts the rest of
the pipeline around it:

    /            dashboard + "what to do next"
    /guide       the walkthrough (environment -> dataset -> train -> verify -> S2C)
    /dataset     dataset creation: synthetic, real captures, merge, inspect
    /display     capture station: show a code            (adopted)
    /camera      capture station: photograph codes       (adopted)
    /train       training runs, live logs
    /evaluate    evaluation of a run
    /models      model registry + "promote to active"
    /s2c         the S2C software: send a file over the screen, receive it
    /api/suite/* the endpoints those pages use
    /api/jobs/*  job status, logs, cancel

Design notes
------------
* **Adoption, not duplication.**  The capture app's route objects are appended to
  this app's router (skipping its ``/``), so there is exactly one implementation
  of sessions, auth and the SSE stream.  ``/static`` still serves the capture
  assets; suite assets live under ``/suite-assets``.
* **Single worker.**  Sessions, jobs and transfers are in-process state.  Run
  with ``--workers 1`` (the container command does).
* **CPU work never runs here.**  Synthesis/training/eval are subprocesses via
  :class:`~optical_modem.suite.jobs.JobManager`; the S2C decode runs in the
  threadpool.
"""
from __future__ import annotations

import base64
import io
import ipaddress
import json
import os
import tempfile
import signal
import time
import zipfile
from pathlib import Path
from PIL import Image
from typing import Any

from .logging_config import configure as _configure_logging, get_logger as _get_logger
_configure_logging()
log = _get_logger("suite.app")

from fastapi import (Depends, FastAPI, File, Form, HTTPException, Query, Request,
                     Response, UploadFile)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, StreamingResponse)
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.background import BackgroundTask

from .. import net
from ..capture import app as capture_app
from ..capture.app import MAX_UPLOAD_BYTES, require_auth  # noqa: F401  (re-exported)
from . import datasets as dsets
from . import crop as dataset_crop
from . import curate as dataset_curate
from . import quality as dataset_quality
from . import progress as progress_mod
from .bus import get_bus
from .db import get_store
from .jobs import JobManager, JobCapacityError
from .registry import Registry, describe_env
from .s2c import S2C, make_config

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
PROJECT_ROOT = HERE.parents[1]
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data")).resolve()
AUTOLABEL = capture_app.AUTOLABEL

REG = Registry(DATA_DIR, project_root=PROJECT_ROOT)
#: Durable state (projects, artefacts, job history).  Postgres in Docker; SQLite
#: beside the data in a local run; ``SUITE_DB=0`` disables it entirely.
DB = get_store()
#: Messaging (job events, expiring locks).  Redis when REDIS_URL is set, else an
#: in-process bus that behaves the same for the single-worker topology.
BUS = get_bus()
log.info("suite boot data_dir=%s db=%s bus=%s run_jobs=%s max_jobs=%s", DATA_DIR, "on" if DB is not None else "off", type(BUS).__name__, os.environ.get("SUITE_RUN_JOBS", "inprocess"), os.environ.get("SUITE_MAX_JOBS", "1"))
_job_mode = os.environ.get("SUITE_RUN_JOBS", "inprocess").lower()
if _job_mode not in ("inprocess", "inline", "1", "true", "yes"):
    raise RuntimeError("SUITE_RUN_JOBS must be inprocess; external worker mode is not supported safely")
JOBS = JobManager(DATA_DIR, project_root=PROJECT_ROOT,
                  python=os.environ.get("SUITE_PYTHON") or None,
                  max_concurrent=int(os.environ.get("SUITE_MAX_JOBS", "1")),
                  run_inprocess=True,
                  env={**os.environ, "DATA_DIR": str(DATA_DIR),
                       "YOLO_OFFLINE": os.environ.get("YOLO_OFFLINE", "1"),
                       "PYTHONPATH": str(PROJECT_ROOT)},
                  store=DB, bus=BUS)
if DB is not None:                       # import artefacts that predate the DB
    try:
        DB.sync_from_disk(REG)
        JOBS.adopt_from_store()
    except Exception as exc:             # never let a bad DB stop the app booting
        print(f"[suite] database sync skipped: {exc}", flush=True)
        log.warning("database sync skipped: %s", exc)

S2C_MAX_FRAMES = int(os.environ.get("S2C_MAX_FRAMES", "64"))


#: ONE S2C engine per process: transfers live in it, so creating a fresh engine
#: per request would lose every transfer the moment the next request arrived.
ENGINE = S2C(weights=None, decode_mode=os.environ.get("S2C_DECODE_MODE", "auto"))


def _s2c() -> S2C:
    """The S2C engine, re-bound to whatever model is currently active.

    Rebinding is cheap: the receiver is cached per (transfer, weights) pair, so
    promoting a new model takes effect on the next decode without dropping the
    transfers that are in flight.
    """
    ENGINE.weights = REG.active_weights()
    return ENGINE


app = FastAPI(title="Optical modem suite", docs_url=None, redoc_url=None,
              openapi_url=None)

@app.exception_handler(JobCapacityError)
async def _job_capacity_error(request: Request, exc: JobCapacityError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=409)


API_HOST = os.environ.get("API_HOST", "").strip().lower()


@app.middleware("http")
async def _api_host_gate(request: Request, call_next):
    """When API_HOST is set, only that host may serve /api/*, /healthz, /version.

    Lets the frontend domain stop exposing the API surface while keeping the
    container's own healthcheck (loopback/private peer) working.
    """
    if API_HOST:
        path = request.url.path
        if path.startswith("/api") or path in ("/healthz", "/version"):
            host = request.headers.get("x-forwarded-host",
                                     request.headers.get("host", "")).split(",")[0].strip().lower()
            host = host.split(":")[0]
            if host != API_HOST:
                peer = request.client.host if request.client else ""
                try:
                    pa = ipaddress.ip_address(peer)
                    internal = pa.is_loopback or pa.is_private or pa.is_reserved
                except ValueError:
                    internal = False
                if not (internal and path in ("/healthz", "/version")):
                    return JSONResponse({"detail": "not found"}, status_code=404)
    return await call_next(request)
# Cross-origin credentialed access is opt-in; same-origin requests need no CORS.
_cors_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
@app.middleware("http")
async def _validate_mutation_origin(request: Request, call_next):
    # Browsers send Origin for state-changing requests; non-browser clients may
    # omit it. Same-origin or explicitly configured cross-origin callers only.
    origin = request.headers.get("origin")
    if origin and request.method.upper() not in ("GET", "HEAD", "OPTIONS"):
        own_origin = f"{net.client_scheme(request)}://{net.client_host(request)}"
        allowed = {value.rstrip("/").lower() for value in _cors_origins}
        if origin.rstrip("/").lower() != own_origin.lower() and origin.rstrip("/").lower() not in allowed:
            return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
    return await call_next(request)


# TLS terminates at the proxy (Traefik): derive the browser's scheme from
# X-Forwarded-Proto and keep redirects relative, so https cannot be downgraded
# into a redirect loop (see optical_modem/net.py)
net.install(app)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


@app.middleware("http")
async def _no_cache_sw(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.endswith("sw.js") or path.endswith("registerSW.js"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

app.mount("/suite-assets", StaticFiles(directory=str(STATIC)), name="suite-assets")

# --------------------------------------------------------------------------- #
# adopt the capture station (everything but its landing page)
# --------------------------------------------------------------------------- #
_ADOPT_SKIP = {"/", "/healthz"}


def _adopt(target: FastAPI, source: FastAPI, skip: set[str]) -> None:
    for route in source.router.routes:
        path = getattr(route, "path", None)
        if path in skip:
            continue
        target.router.routes.append(route)


_adopt(app, capture_app.app, _ADOPT_SKIP)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _page(name: str) -> HTMLResponse:
    path = STATIC / name
    if not path.exists():                 # pragma: no cover
        raise HTTPException(500, f"missing {name}")
    return HTMLResponse(net.stamp_build(path.read_text()),
                        headers={"Cache-Control": "no-store"})


def _python_argv() -> list[str]:
    """The interpreter jobs run in (``SUITE_PYTHON`` lets you swap in a venv)."""
    import sys

    return [os.environ.get("SUITE_PYTHON") or sys.executable, "-u"]


def _safe_name(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 120 or name.startswith(".") or Path(name).name != name or "\\" in name or any(ord(c) < 32 for c in name):
        raise HTTPException(400, "Invalid dataset or run name")
    return name


def _split(split: str) -> str:
    if split not in ("train", "val", "test"):
        raise HTTPException(400, "Invalid dataset split")
    return split


def _dataset_path(name: str) -> Path:
    _safe_name(name)
    p = REG.dataset_dir(name)
    if p.is_symlink() or not p.resolve().is_relative_to(REG.datasets.resolve()):
        raise HTTPException(400, "Invalid dataset path")
    if not p.is_dir():
        raise HTTPException(404, f"no dataset {name}")
    return p


def _dataset_not_busy(name: str) -> None:
    for job in JOBS.list(limit=10000):
        if job.live and (job.meta.get("dataset") == name or name in (job.meta.get("sources") or [])):
            raise HTTPException(409, f"Dataset is in use by {job.title}; wait for the job to finish")


def _start_job(*args, **kwargs):
    # Serialize job registration with editor operations in the supported
    # single-web-process deployment. Live jobs then keep their inputs read-only.
    with dataset_crop._LOCK:
        return JOBS.start(*args, **kwargs)


def _recover_dataset_edits(dataset: Path) -> None:
    journal = dataset / ".edit-journal"
    if journal.exists():
        for ready in journal.glob("*/*/ready"):
            dataset_crop.recover(dataset, ready.parent.parent.name, ready.parent.name)


def _require_yaml(name: str) -> Path:
    p = _dataset_path(name)
    y = Registry.yaml_of(p)
    if y is None:
        raise HTTPException(400, f"dataset {name} has no data.yaml")
    return y


def _stage_state() -> dict:
    """Everything the dashboard needs, in one call."""
    datasets = REG.list_datasets()
    runs = REG.list_runs()
    models = REG.list_models()
    active = REG.active()
    sessions = capture_app.STORE.list()
    shots = sum(s.get("accepted", 0) for s in sessions)
    trained = [r for r in runs if r["has_weights"]]
    best_metric = None
    for m in trained:
        v = (m.get("metrics") or {}).get("box_map50_95")
        if v is not None and (best_metric is None or v > best_metric):
            best_metric = v
    return {
        "env": describe_env(),
        "disk": REG.disk(),
        "datasets": datasets,
        "runs": runs,
        "models": models,
        "active": active,
        "capture": {"sessions": len(sessions), "shots": shots,
                    "verified": sum(s.get("accepted", 0) for s in sessions)},
        "jobs": [j.as_dict() for j in JOBS.list(limit=8)],
        "best_box_map50_95": best_metric,
        "paths": {"root": str(DATA_DIR), "datasets": str(REG.datasets),
                  "runs": str(REG.runs), "models": str(REG.models)},
        "stages": _stages(datasets, trained, active),
        "state": {
            "enabled": DB is not None,
            "database": DB.info() if DB is not None else {"kind": "disabled"},
            "bus": BUS.info(),
            "projects": len(DB.list_projects()) if DB is not None else 0,
            "development": len(DB.list_projects(status="development"))
            if DB is not None else 0,
            "finished": len(DB.list_projects(status="finished"))
            if DB is not None else 0,
        },
    }


def _stages(datasets: list[dict], trained: list[dict], active: dict | None) -> list[dict]:
    """The five stages of the walkthrough, with a done flag and a next action."""
    total_images = sum(d["images"] for d in datasets)
    merged = [d for d in datasets if (d["name"].startswith("train") or
                                      d["name"].startswith("merged"))]
    stages = [
        {"id": "env", "title": "Environment", "done": bool(describe_env().get("torch")),
         "detail": "torch + ultralytics installed, enough disk and RAM",
         "href": "/guide#env"},
        {"id": "dataset", "title": "Dataset", "done": total_images > 0,
         "detail": f"{total_images} labelled images in {len(datasets)} dataset(s)",
         "href": "/dataset"},
        {"id": "train", "title": "Train", "done": bool(trained),
         "detail": (f"{len(trained)} run(s) with weights"
                    if trained else "no trained model yet"),
         "href": "/train"},
        {"id": "evaluate", "title": "Verify", "done": bool(active),
         "detail": ("active model: " + active["name"]) if active
                   else "evaluate a run, then promote it to active",
         "href": "/evaluate"},
        {"id": "s2c", "title": "Use it (S2C)", "done": False,
         "detail": "send a file over the screen and read it back with a camera",
         "href": "/s2c"},
    ]
    return stages


# --------------------------------------------------------------------------- #
# pages — React SPA (client-side routing)
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def spa_root() -> HTMLResponse:
    """Serve the React SPA entry point."""
    path = STATIC / "index.html"
    if not path.exists():
        raise HTTPException(500, "missing index.html — run the frontend build")
    return HTMLResponse(net.stamp_build(path.read_text()),
                        headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #
@app.get("/api/suite/status", dependencies=[Depends(require_auth)])
def status() -> dict:
    return _stage_state()


@app.get("/api/suite/env", dependencies=[Depends(require_auth)])
def env() -> dict:
    return {"env": describe_env(), "disk": REG.disk(),
            "autolabel": AUTOLABEL,
            "python": _python_argv(),
            "root": str(DATA_DIR)}


# --------------------------------------------------------------------------- #
# projects (durable state: what is in development, what is finished)
# --------------------------------------------------------------------------- #
def _known_project(project: str | None) -> str | None:
    """Validate an optional ``project`` field so a typo is a 400, not a 500."""
    project = (project or "").strip() or None
    if project is None or DB is None:
        return project
    if DB.get_project(project) is None:
        raise HTTPException(
            400, f"no project {project!r} — create it on /projects (or omit the "
                 f"field to leave the artefact unassigned)")
    return project


def _project_or_404(key: str | int) -> dict:
    if DB is None:
        raise HTTPException(503, "database disabled (SUITE_DB=0)")
    proj = DB.get_project(key)
    if proj is None:
        raise HTTPException(404, f"no project {key}")
    return proj


@app.get("/api/suite/diagnose")
def diagnose(request: Request) -> dict:
    """Proxy/HTTPS diagnosis, plus the state of the store and the bus.

    Public on purpose: this is the endpoint you open when the app "does not work
    over https", and that is precisely when you cannot log in.  It leaks nothing
    (no password, no cookie, no filesystem paths) and every failing check carries
    the fix in plain words.
    """
    out = net.diagnose(request)
    out["service"] = "optical-modem-suite"
    out["build"] = net.build_id()
    db_info: dict = {"kind": "disabled"}
    if DB is not None:
        try:
            DB.counts()
            db_info = {**DB.info(), "ok": True}
        except Exception as exc:
            db_info = {"kind": DB.kind, "ok": False, "error": str(exc)[:200]}
    out["database"] = db_info
    out["bus"] = BUS.info()
    out["pages"] = ["/", "/wizard", "/projects", "/guide", "/dataset", "/train",
                    "/evaluate", "/models", "/s2c", "/display", "/camera"]
    return out


@app.get("/api/suite/progress", dependencies=[Depends(require_auth)])
def progress() -> dict:
    """Where this box is in the walkthrough, and the single best next action.

    Powers the dashboard rail, the wizard, the nav ticks and the CLI.  One
    source of truth: it is derived from disk + database state, never from a
    "visited" flag, so a box that is already trained opens on "send something"
    instead of asking the user to start over.
    """
    datasets = REG.list_datasets()
    runs = REG.list_runs()
    models = REG.list_models()
    active = REG.active()
    jobs = [j.as_dict() for j in JOBS.list(limit=6)]
    projects = DB.list_projects() if DB is not None else []
    transfers = _s2c().list()
    env = describe_env()
    # the one-click train action must be sized to *this* box, exactly like the
    # Train page's suggestion (same memory-driven batch, same imgsz)
    total_gb = (env.get("mem_total_bytes") or 8 * 1024 ** 3) / 1024 ** 3
    avail_gb = (env.get("mem_available_bytes") or total_gb * 1024 ** 3) / 1024 ** 3
    headroom_gb = min(total_gb, max(avail_gb, 1.0))
    cpu_batch = 8 if headroom_gb >= 6 else 4 if headroom_gb >= 3 else 2
    suggested = {
        "epochs": 120 if env.get("cuda") else 40,
        "imgsz": 640 if env.get("cuda") else (448 if headroom_gb >= 3 else 320),
        "batch": 32 if env.get("cuda") else cpu_batch,
        "workers": min(4, env.get("cpus_available") or 2),
        "device": "0" if env.get("cuda") else "cpu",
        "model": "yolo26n-pose.pt",
    }
    out = progress_mod.build(
        env=env, datasets=datasets, runs=runs, models=models,
        active=active, projects=projects, transfers=transfers,
        sessions=capture_app.STORE.list(), jobs=jobs, db_enabled=DB is not None,
        suggested=suggested)
    out["generated"] = time.time()
    return out


@app.get("/api/suite/finder/{shape}.svg")
def finder_thumb(shape: str, size: int = 72, fg: str = "#e9f2f7",
                 bg: str = "#0b0d10") -> Response:
    """The finder catalogue as a picture — used by the S2C picker.

    Rendered from the same mask the transmitter uses, so the thumbnail cannot
    drift from what actually goes on screen.  Unauthenticated on purpose: it is
    a static illustration of a public catalogue, and <img> tags do not always
    carry cookies.
    """
    from ..protocol import finder as fnd

    try:
        shape_obj = fnd.get_shape(shape)
    except KeyError:
        raise HTTPException(404, f"unknown finder shape {shape!r}")
    if shape_obj is None:                       # "none": an empty slot
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" '
               f'height="{size}" viewBox="0 0 {size} {size}">'
               f'<rect width="{size}" height="{size}" fill="{bg}" rx="8"/>'
               f'<rect x="6" y="6" width="{size - 12}" height="{size - 12}" '
               f'fill="none" stroke="{fg}" stroke-opacity=".25" rx="4" '
               f'stroke-dasharray="4 4"/></svg>')
        return Response(svg, media_type="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=86400"})
    mask = shape_obj.array
    n = mask.shape[0]
    cell = max(2.0, (size - 12) / n)
    cells = []
    for i in range(n):
        for j in range(n):
            if mask[i, j]:
                cells.append(f'<rect x="{6 + j * cell:.2f}" y="{6 + i * cell:.2f}" '
                             f'width="{cell:.2f}" height="{cell:.2f}" fill="{fg}"/>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
           f'viewBox="0 0 {size} {size}" shape-rendering="crispEdges">'
           f'<rect width="{size}" height="{size}" fill="{bg}" rx="8"/>'
           + "".join(cells) + "</svg>")
    return Response(svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/suite/palette", dependencies=[Depends(require_auth)])
def palette_table(cells: int = 32, ecc: str = "rs16") -> dict:
    """Colour palettes + capacity per modulation — the data behind the swatches."""
    from ..config import MODULATIONS, FrameLayout, ModemConfig

    out = []
    for name in sorted(MODULATIONS):
        try:
            cfg = ModemConfig(modulation=name, ecc=ecc,
                              layout=FrameLayout(cells=int(cells)))
        except Exception:                        # a profile that cannot fit cells
            continue
        m = cfg.mod()
        hexes = []
        for c in range(m.n_colors):
            rgb = m.palette[c]
            hexes.append("#%02x%02x%02x" % tuple(int(v) for v in rgb[:3]))
        out.append({"name": name, "bits": m.bits, "pair": bool(m.pair),
                    "colours": m.n_colors, "hex": hexes,
                    "max_user_payload": cfg.describe().get("max_user_payload"),
                    "cells": int(cells), "ecc": ecc})
    return {"modulations": out, "cells": int(cells), "ecc": ecc,
            "eccs": ["none", "rs8", "rs16", "rs32", "rs64", "ldpc12", "ldpc23"]}


@app.get("/api/suite/runs/{name}/curve", dependencies=[Depends(require_auth)])
def run_curve(name: str, limit: int = 400) -> dict:
    """Training curves from a run's ``results.csv`` (Ultralytics writes one).

    ``{epochs: [...], series: {"metrics/mAP50-95(B)": [...], "train/box_loss":
    [...]}}`` — the Train and Verify pages draw it as an inline SVG.  A missing or
    truncated CSV is not an error: the pages just do not draw a chart.
    """
    import csv

    safe = Path(str(name)).name
    if not safe or safe in (".", ".."):
        raise HTTPException(400, "bad run name")
    run_dir = REG.runs / safe
    csv_path = run_dir / "results.csv"
    if not csv_path.exists():
        ship_csv = PROJECT_ROOT / "models" / safe / "results.csv"
        if ship_csv.exists():
            csv_path = ship_csv
    if not csv_path.exists():                    # interrupted or hand-made run
        return {"name": safe, "epochs": [], "series": {}, "source": None}
    epochs: list[int] = []
    series: dict[str, list[float | None]] = {}
    try:
        with csv_path.open(newline="") as fh:
            rows = list(csv.DictReader(fh))
    except OSError as exc:
        raise HTTPException(500, f"cannot read results.csv: {exc}")
    for i, row in enumerate(rows[: max(1, limit)]):
        cleaned = {(k or "").strip(): v for k, v in row.items()}
        epochs.append(i + 1)
        for key, value in cleaned.items():
            if key.lower() in ("epoch", "time"):
                continue
            try:
                num = float(value)
            except (TypeError, ValueError):
                continue
            series.setdefault(key, []).append(num)
    # keep the chart readable: the interesting columns only
    keep = [k for k in series if any(tok in k.lower() for tok in
                                     ("map", "loss", "precision", "recall"))]
    return {"name": safe, "epochs": epochs,
            "series": {k: series[k] for k in keep[:12]},
            "all_columns": list(series)[:24],
            "source": str(csv_path)}


@app.get("/api/suite/projects", dependencies=[Depends(require_auth)])
def list_projects(status: str | None = None) -> dict:
    if DB is None:
        return {"projects": [], "enabled": False}
    return {"projects": DB.list_projects(status=status), "enabled": True,
            "statuses": ["development", "finished", "archived"]}


@app.post("/api/suite/projects", dependencies=[Depends(require_auth)])
def create_project(payload: dict) -> dict:
    if DB is None:
        raise HTTPException(503, "database disabled (SUITE_DB=0)")
    slug = str(payload.get("slug") or payload.get("name") or "").strip()
    if not slug:
        raise HTTPException(400, "give the project a name")
    try:
        proj = DB.create_project(
            slug, title=str(payload.get("title") or slug),
            description=str(payload.get("description") or ""),
            status=str(payload.get("status") or "development"),
            tags=payload.get("tags") or [],
            settings=payload.get("settings") or {})
    except ValueError as exc:
        # "already exists" is a conflict; a bad status/name is the caller's
        # mistake and must not look like a duplicate
        raise HTTPException(409 if "already exists" in str(exc) else 400, str(exc))
    BUS.publish({"kind": "project.created", "project": proj})
    return {"project": proj}


@app.get("/api/suite/projects/{key}", dependencies=[Depends(require_auth)])
def get_project(key: str) -> dict:
    proj = _project_or_404(key)
    out = {"project": proj}
    if DB is not None:
        out["datasets"] = DB.list_datasets(project_id=proj["id"])
        out["runs"] = DB.list_runs(project_id=proj["id"])
        out["transfers"] = DB.list_transfers(project_id=proj["id"], limit=20)
        out["jobs"] = DB.list_jobs(project_id=proj["id"], limit=20)
    return out


@app.patch("/api/suite/projects/{key}", dependencies=[Depends(require_auth)])
def update_project(key: str, payload: dict) -> dict:
    _project_or_404(key)
    try:
        proj = DB.update_project(key, **{k: v for k, v in payload.items()
                                        if k in ("title", "description", "status",
                                                 "tags", "settings", "slug")})
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    BUS.publish({"kind": "project.updated", "project": proj})
    return {"project": proj}


@app.delete("/api/suite/projects/{key}", dependencies=[Depends(require_auth)])
def delete_project(key: str, delete_datasets: bool = False,
                   reassign_to: str | None = None) -> dict:
    _project_or_404(key)
    ok = DB.delete_project(key, reassign_to=reassign_to,
                           delete_datasets=bool(delete_datasets))
    return {"ok": bool(ok)}


@app.get("/api/suite/state", dependencies=[Depends(require_auth)])
def state_overview() -> dict:
    """Everything the database knows, in one call (dashboard + export)."""
    if DB is None:
        return {"enabled": False, "projects": [], "counts": {},
                "database": {"kind": "disabled"}}
    return {
        "enabled": True,
        "database": DB.info(),
        "bus": BUS.info(),
        "counts": DB.counts(),
        "projects": DB.list_projects(),
        "datasets": DB.list_datasets(),
        "runs": DB.list_runs(),
        "models": DB.list_models(),
        "jobs": DB.list_jobs(limit=20),
        "transfers": DB.list_transfers(limit=20),
        "events": DB.list_events(limit=40),
        "active": REG.active(),
    }


@app.get("/api/suite/events", dependencies=[Depends(require_auth)])
def recent_events(limit: int = 50, kind: str | None = None) -> dict:
    """Recent job/project events (the SSE stream's backlog)."""
    events = BUS.recent(limit) if kind is None else [
        e for e in BUS.recent(max(limit * 4, 200)) if e.get("kind") == kind][:limit]
    return {"events": events, "bus": BUS.info()}


@app.get("/api/events", dependencies=[Depends(require_auth)])
async def event_stream(request: Request):
    """Server-Sent Events: job lifecycle without polling.

    The UI's poller stays as the fallback; this is what makes the log feel live
    and lets a second browser tab / the phone follow a training run.
    """
    import asyncio
    import queue as _queue

    def fmt(event: dict) -> str:
        return f"event: {event.get('kind', 'message')}\ndata: {json.dumps(event, default=str)}\n\n"

    q: _queue.Queue = _queue.Queue(maxsize=200)

    def on_event(event: dict) -> None:
        try:
            q.put_nowait(event)
        except _queue.Full:                     # slow client: drop, never block
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
                    event = await run_in_threadpool(q.get, True, 15.0)
                except _queue.Empty:
                    yield ": keep-alive\n\n"    # keeps proxies from closing it
                    continue
                yield fmt(event)
        finally:
            unsubscribe()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform",
                                      "X-Accel-Buffering": "no"})


# --------------------------------------------------------------------------- #
# datasets
# --------------------------------------------------------------------------- #
@app.get("/api/suite/datasets", dependencies=[Depends(require_auth)])
def list_datasets() -> dict:
    """The disk is the source of truth; the database adds ownership and history.

    A dataset directory that exists but has no row (created before the database,
    or copied in by hand) is still listed, with ``source: "disk"``; a row whose
    directory has gone missing is listed too, flagged ``missing`` so the UI can
    offer to forget it.
    """
    disk = REG.list_datasets()
    if DB is None:
        return {"datasets": disk, "root": str(REG.datasets), "enabled": False}
    meta = {row["name"]: row for row in DB.list_datasets()}
    by_id = {p["id"]: p["slug"] for p in DB.list_projects()}
    out, seen = [], set()
    for d in disk:
        row = meta.get(d["name"]) or {}
        seen.add(d["name"])
        out.append({**d, "source": row.get("source") or "disk",
                    "status": row.get("status") or "ready",
                    "project": by_id.get(row.get("project_id")),
                    "project_id": row.get("project_id"), "missing": False})
    for name, row in meta.items():
        if name in seen:
            continue
        out.append({"name": name, "images": row.get("images") or 0,
                    "splits": row.get("splits") or {}, "bytes": row.get("bytes") or 0,
                    "source": row.get("source"), "status": row.get("status"),
                    "project": by_id.get(row.get("project_id")),
                    "project_id": row.get("project_id"), "missing": True})
    return {"datasets": out, "root": str(REG.datasets), "enabled": True}


@app.post("/api/suite/datasets/{name}/assign", dependencies=[Depends(require_auth)])
def assign_dataset(name: str, payload: dict) -> dict:
    if DB is None:
        raise HTTPException(503, "Project database is disabled")
    path = _dataset_path(name)
    project = _known_project(payload.get("project"))
    if not project:
        raise HTTPException(400, "Choose a project")
    old = DB.get_dataset(name)
    target = _project_or_404(project)
    if old and old.get("project_id") and old["project_id"] != target["id"] and not payload.get("allow_move"):
        raise HTTPException(409, "Dataset belongs to another project; confirm moving it")
    row = DB.upsert_dataset(name, project=project, path=str(path))
    return {"dataset": row}


@app.post("/api/suite/datasets/{name}/curate", dependencies=[Depends(require_auth)])
def curate_dataset(name: str, payload: dict) -> dict:
    with dataset_crop._LOCK:
        source = _dataset_path(name)
        _recover_dataset_edits(source)
        dest_name = payload.get("name")
        if not isinstance(dest_name, str) or not dest_name or dest_name in (".", "..") or Path(dest_name).name != dest_name or dest_name.startswith(".") or len(dest_name) > 120:
            raise HTTPException(400, "Invalid destination dataset name")
        _safe_name(dest_name)
        project = _known_project(payload.get("project"))
        if project and DB is None:
            raise HTTPException(503, "Project database is disabled")
        source_yaml = REG.yaml_of(source)
        if source_yaml is None:
            raise HTTPException(400, "Source dataset has no YAML")
        dest = REG.dataset_dir(dest_name)
        try:
            count = dataset_curate.curated_copy(source, dest, payload.get("selected"), source_yaml)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if DB is not None:
            DB.upsert_dataset(dest_name, project=project, source="curated", status="ready", path=str(dest), images=count, params={"source": name})
        BUS.publish({"kind": "dataset.ready", "dataset": dest_name})
        return {"dataset": dest_name, "images": count}


@app.post("/api/suite/datasets/synth", dependencies=[Depends(require_auth)])
def synth_dataset(payload: dict) -> dict:
    name = str(payload.get("name") or "").strip() or time.strftime("synth-%Y%m%d-%H%M%S")
    _safe_name(name)
    out = REG.dataset_dir(name)
    if out.exists():
        raise HTTPException(409, f"dataset {name} already exists")
    n_train = int(payload.get("train", 300))
    n_val = int(payload.get("val", 60))
    if not (10 <= n_train <= 20000) or not (2 <= n_val <= 4000):
        raise HTTPException(400, "train must be 10..20000 and val 2..4000")
    argv = _python_argv() + ["-m", "optical_modem.suite.tasks", "synth",
                             "--out", str(out), "--train", str(n_train),
                             "--val", str(n_val), "--seed", str(int(payload.get("seed", 1))),
                             "--level", str(float(payload.get("level", 1.0)))]
    project = _known_project(payload.get("project"))
    job = _start_job("synth", f"Generate {n_train}+{n_val} synthetic frames", argv,
                     meta={"dataset": name, "train": n_train, "val": n_val,
                           "project": project})
    if DB is not None:
        try:
            DB.upsert_dataset(name, project=project, source="synth", status="building",
                              path=str(out),
                              params={"train": n_train, "val": n_val,
                                      "seed": int(payload.get("seed", 1)),
                                      "level": float(payload.get("level", 1.0))})
        except Exception as exc:
            print(f"[suite] store: {exc}", flush=True)
    return {"job": job.as_dict(), "dataset": name}


@app.post("/api/suite/datasets/captures", dependencies=[Depends(require_auth)])
def captures_dataset(payload: dict) -> dict:
    name = str(payload.get("name") or "").strip() or time.strftime("real-%Y%m%d-%H%M%S")
    _safe_name(name)
    out = REG.dataset_dir(name)
    if out.exists():
        raise HTTPException(409, f"dataset {name} already exists")
    argv = _python_argv() + ["-m", "optical_modem.suite.tasks", "captures",
                             "--data-dir", str(DATA_DIR), "--out", str(out),
                             "--val-frac", str(float(payload.get("val_frac", 0.15)))]
    if payload.get("verified_only", True):
        argv.append("--verified-only")
    project = _known_project(payload.get("project"))
    job = _start_job("captures", f"Convert captured photos -> {name}", argv,
                     meta={"dataset": name, "project": project})
    if DB is not None:
        DB.upsert_dataset(name, project=project, source="captures", status="building",
                          path=str(out),
                          params={"val_frac": float(payload.get("val_frac", 0.15)),
                                  "verified_only": bool(payload.get("verified_only", True))})
    return {"job": job.as_dict(), "dataset": name}


@app.post("/api/suite/datasets/merge", dependencies=[Depends(require_auth)])
def merge_dataset(payload: dict) -> dict:
    sources = [str(s) for s in (payload.get("sources") or [])]
    if not sources:
        raise HTTPException(400, "pick at least one source dataset")
    for s in sources:
        _dataset_path(s)
    name = str(payload.get("name") or "").strip() or time.strftime("train-%Y%m%d-%H%M%S")
    _safe_name(name)
    out = REG.dataset_dir(name)
    if out.exists():
        raise HTTPException(409, f"dataset {name} already exists")
    argv = _python_argv() + ["-m", "optical_modem.suite.tasks", "merge",
                             "--out", str(out)]
    for s in sources:
        argv += ["--source", str(REG.dataset_dir(s))]
    project = _known_project(payload.get("project"))
    job = _start_job("merge", f"Merge {len(sources)} dataset(s) -> {name}", argv,
                     meta={"dataset": name, "sources": sources, "project": project})
    if DB is not None:
        DB.upsert_dataset(name, project=project, source="merge", status="building",
                          path=str(out), params={"sources": sources})
    return {"job": job.as_dict(), "dataset": name}


@app.get("/api/suite/datasets/{name}", dependencies=[Depends(require_auth)])
def dataset_detail(name: str) -> dict:
    return REG.dataset_summary(_dataset_path(name))


@app.get("/api/suite/datasets/{name}/quality", dependencies=[Depends(require_auth)])
def dataset_quality_report(name: str, offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=200)) -> dict:
    dataset = _dataset_path(name)
    _recover_dataset_edits(dataset)
    return dataset_quality.inspect(dataset, offset, limit)


@app.get("/api/suite/datasets/{name}/preview", dependencies=[Depends(require_auth)])
def dataset_preview(name: str, split: str = "train", index: int = 0) -> Response:
    blob = dsets.preview(_dataset_path(name), split=split, index=index,
                         annotated=True)
    if blob is None:
        raise HTTPException(404, "no images in that split")
    return Response(blob, media_type="image/jpeg",
                    headers={"Cache-Control": "no-cache"})


def _recover_edit(name: str, split: str, filename: str) -> None:
    if split in ("train", "val"):
        try:
            dataset_crop.recover(_dataset_path(name), split, filename)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.get("/api/suite/datasets/{name}/images", dependencies=[Depends(require_auth)])
def dataset_images(name: str, split: str = "train", limit: int = Query(200, ge=1, le=2000), offset: int = Query(0, ge=0)) -> dict:
    root = _dataset_path(name)
    p = root / "images" / _split(split)
    if not p.resolve().is_relative_to(root.resolve()):
        raise HTTPException(400, "Invalid dataset image path")
    if not p.is_dir():
        return {"images": [], "split": split, "total": 0}
    files = sorted(f.name for f in p.iterdir()
                   if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp", ".webp"))
    total = len(files)
    page = files[offset: offset + limit]
    return {"split": split, "total": total, "images": page}


@app.get("/api/suite/datasets/{name}/image/{split}/{filename}", dependencies=[Depends(require_auth)])
def dataset_image(name: str, split: str, filename: str, size: int = Query(0, ge=0, le=1024)) -> Response:
    _recover_edit(name, split, filename)
    path = _dataset_path(name) / "images" / _split(split) / filename
    if Path(filename).name != filename or path.is_symlink() or not path.resolve().is_relative_to(_dataset_path(name).resolve()):
        raise HTTPException(400, "Invalid image path")
    if not path.is_file():
        raise HTTPException(404, "no such image")
    # Thumbnail mode: resize + disk-cache for fast gallery loads.
    if size and size > 0:
        cache_dir = REG.dataset_dir(name) / ".thumbs" / split / str(size)
        cache_dir.mkdir(parents=True, exist_ok=True)
        stat = path.stat()
        cached = cache_dir / (filename + f".{stat.st_mtime_ns}.{stat.st_size}.jpg")
        if not cached.is_file():
            try:
                with Image.open(path) as im:
                    im = im.convert("RGB")
                    im.thumbnail((size, size), Image.LANCZOS)
                    im.save(cached, "JPEG", quality=80, optimize=True)
            except Exception:
                # Fall back to serving the original on any decode failure.
                data = path.read_bytes()
                media = "image/jpeg"
                if path.suffix.lower() == ".png":
                    media = "image/png"
                elif path.suffix.lower() == ".webp":
                    media = "image/webp"
                elif path.suffix.lower() == ".bmp":
                    media = "image/bmp"
                return Response(data, media_type=media, headers={"Cache-Control": "private, no-cache"})
        data = cached.read_bytes()
        return Response(data, media_type="image/jpeg",
                        headers={"Cache-Control": "private, no-cache"})
    data = path.read_bytes()
    media = "image/jpeg"
    if path.suffix.lower() == ".png":
        media = "image/png"
    elif path.suffix.lower() == ".webp":
        media = "image/webp"
    elif path.suffix.lower() == ".bmp":
        media = "image/bmp"
    return Response(data, media_type=media,
                    headers={"Cache-Control": "private, no-cache"})


@app.get("/api/suite/datasets/{name}/label/{split}/{filename}", dependencies=[Depends(require_auth)])
def dataset_label(name: str, split: str, filename: str) -> dict:
    _recover_edit(name, split, filename)
    label_path = _dataset_path(name) / "labels" / _split(split) / (Path(filename).stem + ".txt")
    if label_path.is_symlink() or not label_path.resolve().is_relative_to(_dataset_path(name).resolve()):
        raise HTTPException(400, "Invalid label path")
    text = label_path.read_text().strip() if label_path.is_file() else ""
    # parse YOLO-pose: class cx cy w h (kp_x kp_y kp_v)*4
    keypoints = []
    if text:
        parts = text.splitlines()[0].split()
        if len(parts) >= 5:
            try:
                cx, cy, w, h = (float(parts[1]), float(parts[2]),
                                 float(parts[3]), float(parts[4]))
                kps = []
                i = 5
                while i + 2 < len(parts):
                    kps.append({"x": float(parts[i]), "y": float(parts[i + 1]),
                                "v": int(float(parts[i + 2]))})
                    i += 3
                keypoints = kps
            except (ValueError, OverflowError):
                pass
    return {"filename": filename, "text": text, "keypoints": keypoints,
            "path": str(label_path)}


@app.put("/api/suite/datasets/{name}/label/{split}/{filename}", dependencies=[Depends(require_auth)])
def save_dataset_label(name: str, split: str, filename: str, payload: dict) -> dict:
    with dataset_crop._LOCK:
        _dataset_not_busy(name)
        ds = _dataset_path(name)
        _split(split)
        if split in ("train", "val"):
            try:
                dataset_crop.recover(ds, split, filename)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        label_dir = ds / "labels" / split
        label_dir.mkdir(parents=True, exist_ok=True)
        label_path = label_dir / (Path(filename).stem + ".txt")
        if label_path.is_symlink() or not label_path.resolve().is_relative_to(ds.resolve()):
            raise HTTPException(400, "Invalid label path")
        text = str(payload.get("text", "")).strip()
        if "keypoints" in payload:
            existing = label_path.read_text().strip() if label_path.is_file() else ""
            if "expected_text" in payload and str(payload["expected_text"]).strip() != existing:
                raise HTTPException(409, "Label changed since it was loaded; reload before saving")
            if existing:
                rows = existing.splitlines()
                if len(rows) != 1 or len(rows[0].split()) != 17 or rows[0].split()[0] != "0":
                    raise HTTPException(409, "Pose Studio cannot safely replace this multi-object or unsupported label")
            kps = payload["keypoints"] or []
            if kps:
                import math
                if len(kps) != 4 or any(not isinstance(k, dict) or not all(isinstance(k.get(c), (int, float)) and math.isfinite(k[c]) and 0 <= k[c] <= 1 for c in ("x", "y")) or k.get("v", 2) not in (0, 1, 2) for k in kps):
                    raise HTTPException(400, "Expected four valid normalized keypoints")
                # reconstruct a minimal label; class 0, full-image box
                xs = [k["x"] for k in kps]; ys = [k["y"] for k in kps]
                cx = (min(xs) + max(xs)) / 2; cy = (min(ys) + max(ys)) / 2
                w = max(xs) - min(xs); h = max(ys) - min(ys)
                w = max(w, 1e-3); h = max(h, 1e-3)
                parts = ["0", f"{cx:.6f}", f"{cy:.6f}", f"{w:.6f}", f"{h:.6f}"]
                for k in kps:
                    parts += [f"{k['x']:.6f}", f"{k['y']:.6f}", str(int(k.get('v', 2)))]
                text = " ".join(parts)
        dataset_crop._atomic_write(label_path, (text + ("\n" if text else "")).encode())
        return {"ok": True, "path": str(label_path), "text": text}
@app.get("/api/suite/datasets/{name}/crop/{split}/{filename}", dependencies=[Depends(require_auth)])
def crop_status(name: str, split: str, filename: str) -> dict:
    try:
        with dataset_crop._LOCK:
            dataset = _dataset_path(name)
            dataset_crop.recover(dataset, split, filename)
            image, _, backup = dataset_crop.paths(dataset, split, filename)
            if not image.is_file():
                raise ValueError("Image not found")
            with Image.open(image) as im:
                return {"width": im.width, "height": im.height, "has_backup": (backup / "image").is_file(),
                        "revision": dataset_crop.revision(dataset, split, filename)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/suite/datasets/{name}/crop/{split}/{filename}", dependencies=[Depends(require_auth)])
def crop_image(name: str, split: str, filename: str, payload: dict) -> dict:
    try:
        with dataset_crop._LOCK:
            _dataset_not_busy(name)
            dataset = _dataset_path(name)
            dataset_crop.recover(dataset, split, filename)
            if payload.get("revision") != dataset_crop.revision(dataset, split, filename):
                raise HTTPException(409, "Image or label changed since preview; reload and preview again")
            dataset_crop.crop(dataset, split, filename, *(payload.get(k) for k in ("x", "y", "width", "height")))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.post("/api/suite/datasets/{name}/revert/{split}/{filename}", dependencies=[Depends(require_auth)])
def revert_image(name: str, split: str, filename: str) -> dict:
    try:
        with dataset_crop._LOCK:
            _dataset_not_busy(name)
            dataset_crop.revert(_dataset_path(name), split, filename)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.get("/api/suite/datasets/{name}/export.zip", dependencies=[Depends(require_auth)])
def export_dataset_zip(name: str) -> Response:
    """Export a relocatable dataset archive without exposing local paths."""
    import yaml
    with dataset_crop._LOCK:
        ds_dir = _dataset_path(name)
        _recover_dataset_edits(ds_dir)
        yaml_file = REG.yaml_of(ds_dir)
        rewritten = None
        if yaml_file:
            try:
                config = yaml.safe_load(yaml_file.read_text())
                if not isinstance(config, dict):
                    raise ValueError("Dataset YAML is not a mapping")
                configured = config.pop("path", None)
                root = Path(configured) if configured else ds_dir
                if not root.is_absolute():
                    root = ds_dir / root
                root = root.resolve()
                if not root.is_relative_to(ds_dir.resolve()):
                    raise ValueError("Dataset YAML points outside the dataset; cannot export it portably")
                for key in ("train", "val", "validation", "test", "minival"):
                    if not config.get(key):
                        continue
                    values = config[key] if isinstance(config[key], list) else [config[key]]
                    portable = []
                    for value in values:
                        path = Path(value)
                        resolved = (path if path.is_absolute() else root / path).resolve()
                        if not resolved.is_relative_to(ds_dir.resolve()):
                            raise ValueError("Dataset YAML split points outside the dataset; cannot export it portably")
                        portable.append(resolved.relative_to(ds_dir.resolve()).as_posix())
                    config[key] = portable if isinstance(config[key], list) else portable[0]
                rewritten = yaml.safe_dump(config, sort_keys=False)
            except (ValueError, TypeError, yaml.YAMLError) as exc:
                raise HTTPException(400, f"Invalid or non-portable dataset YAML: {exc}") from exc
        tmp = tempfile.NamedTemporaryFile(prefix="dataset-export-", suffix=".zip", delete=False)
        tmp_path = Path(tmp.name)
        tmp.close()
        try:
            with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for file_path in sorted(ds_dir.rglob("*")):
                    if not file_path.is_file() or file_path.is_symlink():
                        continue
                    rel = file_path.relative_to(ds_dir)
                    if rel.parts and rel.parts[0].startswith("."):
                        continue
                    if yaml_file and file_path == yaml_file:
                        zf.writestr(str(Path(ds_dir.name) / rel), rewritten)
                    else:
                        zf.write(file_path, arcname=str(Path(ds_dir.name) / rel))
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
    return FileResponse(tmp_path, media_type="application/zip", filename=f"{ds_dir.name}.zip",
                        headers={"Cache-Control": "no-store"},
                        background=BackgroundTask(tmp_path.unlink, missing_ok=True))


@app.delete("/api/suite/datasets/{name}", dependencies=[Depends(require_auth)])
def drop_dataset(name: str) -> dict:
    with dataset_crop._LOCK:
        _dataset_not_busy(name)
        gone = REG.drop_dataset(name)
        if DB is not None:
            DB.delete_dataset(name)
        if not gone:
            raise HTTPException(404, "no such dataset")
        return {"ok": True}


# --------------------------------------------------------------------------- #
# training / evaluation
# --------------------------------------------------------------------------- #
@app.get("/api/suite/train/defaults", dependencies=[Depends(require_auth)])
def train_defaults() -> dict:
    dsets_ = REG.list_datasets()
    merged = [d for d in dsets_ if d["images"] and d["yaml"]]
    env = describe_env()
    # a CPU-only box needs a small model + image size; a GPU can afford more.
    # Batch also follows *memory*: Ultralytics gets OOM-killed (SIGKILL, log
    # stops mid-epoch) long before it gets slow, so a 2 GB box must be told 2.
    total_gb = (env.get("mem_total_bytes") or 8 * 1024 ** 3) / 1024 ** 3
    avail_gb = (env.get("mem_available_bytes") or total_gb * 1024 ** 3) / 1024 ** 3
    headroom_gb = min(total_gb, max(avail_gb, 1.0))
    cpu_batch = 8 if headroom_gb >= 6 else 4 if headroom_gb >= 3 else 2
    cpu_imgsz = 448 if headroom_gb >= 3 else 320
    return {
        "env": env,
        "datasets": merged,
        "memory": {"total_gb": round(total_gb, 1), "available_gb": round(avail_gb, 1),
                   "note": ("batch/imgsz are sized to available RAM; on CPU each "
                            "448px batch-8 epoch step needs roughly 2.5 GB")},
        "suggested": {
            "epochs": 40 if not env.get("cuda") else 120,
            "imgsz": 640 if env.get("cuda") else cpu_imgsz,
            "batch": 32 if env.get("cuda") else cpu_batch,
            "model": "yolo26n-pose.pt",
            "workers": min(4, env.get("cpus_available") or 2),
            "device": "0" if env.get("cuda") else "cpu",
        },
        "runs": REG.list_runs(),
        # a checkpoint that shipped with the image needs no download and starts
        # from a frame detector instead of COCO people: much faster convergence
        "bundled_bases": REG.bundled_bases(),
        "base_models": (["yolo26n-pose.pt", "yolo26s-pose.pt", "yolo26m-pose.pt",
                        "yolo26l-pose.pt", "yolo26x-pose.pt",
                        "yolo11n-pose.pt", "yolo11s-pose.pt",
                        "yolov8n-pose.pt"]),
        "architectures": [
            {"name": "yolo26n-pose.pt", "family": "YOLO26", "size": "nano",
             "notes": "fastest, bundled base; best for CPU"},
            {"name": "yolo26s-pose.pt", "family": "YOLO26", "size": "small",
             "notes": "balanced speed/accuracy"},
            {"name": "yolo26m-pose.pt", "family": "YOLO26", "size": "medium",
             "notes": "higher accuracy, needs GPU"},
            {"name": "yolo26l-pose.pt", "family": "YOLO26", "size": "large",
             "notes": "high accuracy, heavy"},
            {"name": "yolo26x-pose.pt", "family": "YOLO26", "size": "xlarge",
             "notes": "max accuracy"},
            {"name": "yolo11n-pose.pt", "family": "YOLO11", "size": "nano",
             "notes": "alternate backbone"},
            {"name": "yolo11s-pose.pt", "family": "YOLO11", "size": "small",
             "notes": "alternate backbone"},
            {"name": "yolov8n-pose.pt", "family": "YOLOv8", "size": "nano",
             "notes": "legacy compatible"},
            {"name": "custom", "family": "Custom", "size": "any",
             "notes": "type any ultralytics model name or .pt path"},
        ],
        "active": REG.active(),
    }


@app.post("/api/suite/train", dependencies=[Depends(require_auth)])
async def start_training(payload: dict) -> dict:
    dataset = str(payload.get("dataset") or "")
    if not dataset:
        raise HTTPException(400, "choose a dataset")
    yaml_path = _require_yaml(dataset)
    ds_path = _dataset_path(dataset)
    _recover_dataset_edits(ds_path)
    name = str(payload.get("name") or "").strip() or time.strftime("run-%Y%m%d-%H%M%S")
    _safe_name(name)
    out = REG.runs / name
    if out.exists():
        raise HTTPException(409, f"run {name} already exists")
    epochs = int(payload.get("epochs", 40))
    imgsz = int(payload.get("imgsz", 448))
    batch = int(payload.get("batch", 8))
    patience = int(payload.get("patience", 0) or 0)
    early_stop_start = int(payload.get("early_stop_start", 0) or 0)
    eval_every = int(payload.get("eval_every", 1) or 1)
    if not (1 <= epochs <= 2000):
        raise HTTPException(400, "epochs must be 1..2000")
    if not (160 <= imgsz <= 1280):
        raise HTTPException(400, "imgsz must be 160..1280")
    if not (0 <= batch <= 128):
        raise HTTPException(400, "batch must be 0..128")
    if batch == 0:
        # Conservative CPU default; avoids GPU-only Ultralytics autobatching.
        batch = 4
    device = str(payload.get("device", "auto"))
    if device == "auto":
        device = "0" if describe_env().get("cuda") else "cpu"
    argv = _python_argv() + [
        "-m", "optical_modem.data.train_yolo",
        "--data", str(yaml_path),
        "--model", str(payload.get("model", "yolo26n-pose.pt")),
        "--epochs", str(epochs), "--imgsz", str(imgsz), "--batch", str(batch),
        "--device", device,
        "--workers", str(int(payload.get("workers", 2))), 
        "--project", str(REG.runs), "--name", name, 
        "--seed", str(int(payload.get("seed", 0))), 
        "--patience", str(patience), "--early-stop-start", str(early_stop_start), 
    ]
    if payload.get("mosaic") is not None:
        mosaic = float(payload["mosaic"])
        if not (0.0 <= mosaic <= 1.0):
            raise HTTPException(400, "mosaic must be 0.0..1.0")
        argv += ["--mosaic", str(mosaic)] 
    project = _known_project(payload.get("project")) 
    log.info("training started run=%s dataset=%s epochs=%s imgsz=%s batch=%s", name, dataset, epochs, imgsz, batch)
    job = _start_job("train", f"Train {name} ({epochs} epochs @ {imgsz}px)", argv,
                     meta={"run": name, "dataset": dataset, "epochs": epochs, 
                           "imgsz": imgsz, "project": project, 
                           "eval_every": eval_every})
    if DB is not None:
        DB.upsert_run(name, project=project, dataset=dataset, epochs=epochs,
                      imgsz=imgsz, batch=batch, device=device,
                      base_model=str(payload.get("model", "")), status="running",
                      path=str(out))
    return {"job": job.as_dict(), "run": name}


@app.get("/api/suite/runs", dependencies=[Depends(require_auth)])
def list_runs() -> dict:
    return {"runs": REG.list_runs(), "active": REG.active()}


def _run_dataset_name(run_dir: Path) -> str:
    """Resolve the run's recorded dataset without silently selecting another."""
    data_path = ""
    card = run_dir / "weights" / "model_card.json"
    if card.is_file():
        try:
            data_path = str(json.loads(card.read_text()).get("train_args", {}).get("data", ""))
        except (OSError, ValueError, TypeError):
            pass
    if not data_path and (run_dir / "args.yaml").is_file():
        try:
            import yaml
            data_path = str((yaml.safe_load((run_dir / "args.yaml").read_text()) or {}).get("data", ""))
        except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
            pass
    if data_path:
        for dataset in REG.list_datasets():
            if dataset.get("yaml") == data_path or dataset.get("path") == data_path:
                return dataset["name"]
    raise HTTPException(400, "The run's original dataset could not be identified; restore it before continuing")


@app.post("/api/suite/runs/{name}/extend", dependencies=[Depends(require_auth)])
async def extend_run(name: str, payload: dict | None = None) -> dict:
    payload = payload or {}
    run_dir = REG.runs / name
    weights = run_dir / "weights" / "best.pt"
    if not weights.exists():
        raise HTTPException(404, f"run {name} has no best.pt")
    extra = int(payload.get("epochs", 10))
    if not (1 <= extra <= 2000):
        raise HTTPException(400, "epochs must be 1..2000")
    # This is a new fine-tuning run starting from best.pt, so only the
    # requested additional epochs are run, not the old total again.
    card = run_dir / "weights" / "model_card.json"
    new_epochs = extra
    new_name = f"{name}-ext{extra}"
    out = REG.runs / new_name
    if out.exists():
        raise HTTPException(409, f"run {new_name} already exists")
    ds_name = _run_dataset_name(run_dir)
    yaml_path = _require_yaml(ds_name)
    try:
        recorded_imgsz = json.loads(card.read_text()).get("train_args", {}).get("imgsz", 448)
    except (OSError, ValueError, TypeError):
        recorded_imgsz = 448
    imgsz = int(payload.get("imgsz") or recorded_imgsz)
    batch = int(payload.get("batch", 4))
    device = str(payload.get("device", "cpu"))
    argv = _python_argv() + [
        "-m", "optical_modem.data.train_yolo",
        "--data", str(yaml_path),
        "--model", str(weights),
        "--epochs", str(new_epochs), "--imgsz", str(imgsz), "--batch", str(batch),
        "--device", device,
        "--workers", str(int(payload.get("workers", 2))),
        "--project", str(REG.runs), "--name", new_name,
        "--seed", str(int(payload.get("seed", 0))),
    ]
    project = _known_project(payload.get("project"))
    job = _start_job("train", f"Extend {name} (+{extra} epochs)", argv,
                     meta={"run": new_name, "dataset": ds_name, "epochs": new_epochs,
                           "imgsz": imgsz, "project": project})
    if DB is not None:
        DB.upsert_run(new_name, project=project, dataset=ds_name, epochs=new_epochs,
                      imgsz=imgsz, batch=batch, device=device, base_model=str(weights),
                      status="running", path=str(out))
    return {"job": job.as_dict(), "run": new_name, "base": name}
@app.post("/api/suite/runs/{name}/resume", dependencies=[Depends(require_auth)])
async def resume_run(name: str, payload: dict | None = None) -> dict:
    payload = payload or {}
    run_dir = REG.runs / name
    weights = run_dir / "weights" / "last.pt"
    if not weights.exists():
        raise HTTPException(404, f"run {name} has no last.pt to resume from")
    card = run_dir / "weights" / "model_card.json"
    orig_epochs = 40
    try:
        orig_epochs = int(json.loads(card.read_text()).get("train_args", {}).get("epochs", 40))
    except Exception:
        pass
    epochs = int(payload.get("epochs", orig_epochs))
    imgsz = int(payload.get("imgsz") or 448)
    try:
        imgsz = int(payload.get("imgsz") or json.loads(card.read_text()).get("train_args", {}).get("imgsz", 448))
    except Exception:
        pass
    batch = int(payload.get("batch", 4))
    device = str(payload.get("device", "cpu"))
    ds_name = _run_dataset_name(run_dir)
    yaml_path = _require_yaml(ds_name)
    argv = _python_argv() + [
        "-m", "optical_modem.data.train_yolo",
        "--data", str(yaml_path),
        "--model", str(weights),
        "--epochs", str(epochs), "--imgsz", str(imgsz), "--batch", str(batch),
        "--device", device,
        "--workers", str(int(payload.get("workers", 2))),
        "--project", str(REG.runs), "--name", name,
        "--resume",
    ]
    project = _known_project(payload.get("project"))
    job = _start_job("train", f"Resume {name}", argv,
                     meta={"run": name, "dataset": ds_name, "epochs": epochs,
                           "imgsz": imgsz, "project": project})
    if DB is not None:
        DB.upsert_run(name, project=project, dataset=ds_name, epochs=epochs,
                      imgsz=imgsz, batch=batch, device=device, base_model=str(weights),
                      status="running", path=str(run_dir))
    return {"job": job.as_dict(), "run": name}
@app.get("/api/suite/runs/{name}/images", dependencies=[Depends(require_auth)])
def run_images(name: str) -> dict:
    d = REG.runs / name
    if not d.is_dir():
        raise HTTPException(404, "no such run")
    imgs = sorted(p.name for p in d.iterdir()
                  if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    return {"run": name, "images": imgs}


@app.get("/api/suite/runs/{name}/image/{filename}", dependencies=[Depends(require_auth)])
def run_image(name: str, filename: str) -> Response:
    path = REG.runs / name / filename
    if not path.is_file():
        raise HTTPException(404, "no such image")
    data = path.read_bytes()
    media = "image/jpeg"
    if path.suffix.lower() == ".png":
        media = "image/png"
    return Response(data, media_type=media,
                    headers={"Cache-Control": "no-cache"})
@app.post("/api/suite/runs/{name}/evaluate", dependencies=[Depends(require_auth)])
def evaluate_run(name: str, payload: dict | None = None) -> dict:
    payload = payload or {}
    safe = Path(str(name)).name
    run = REG.runs / safe
    weights = run / "weights" / "best.pt"
    if not weights.exists():
        ship_weights = PROJECT_ROOT / "models" / safe / "weights" / "best.pt"
        if ship_weights.exists():
            weights = ship_weights
            run.mkdir(parents=True, exist_ok=True)
        else:
            raise HTTPException(404, f"run {name} has no best.pt")
    dataset = str(payload.get("dataset") or "")
    if dataset:
        _require_yaml(dataset)
    levels_raw = payload.get("levels")
    if levels_raw is None or str(levels_raw).strip() == "":
        levels_str = str(float(payload.get("level", 0.6)))
    elif isinstance(levels_raw, (list, tuple)):
        levels_str = ",".join(str(float(x)) for x in levels_raw)
    else:
        parts = [str(float(x.strip())) for x in str(levels_raw).split(",") if x.strip()]
        levels_str = ",".join(parts) if parts else "0.2,0.6,1.0"
    n = max(1, min(200, int(payload.get("n", 12))))
    imgsz = max(160, min(1280, int(payload.get("imgsz", 448))))
    out_json = run / "eval.json"
    argv = _python_argv() + [str(PROJECT_ROOT / "scripts" / "evaluate.py"),
                             "--weights", str(weights),
                             "--n", str(n), "--imgsz", str(imgsz),
                             "--levels", levels_str,
                             "--out", str(out_json)]
    if dataset:
        argv += ["--dataset", str(REG.dataset_dir(dataset))]
    job = _start_job("evaluate", f"Evaluate {safe} ({levels_str})", argv,
                     meta={"run": safe, "weights": str(weights),
                           "report": str(out_json), "dataset": dataset,
                           "levels": levels_str})
    return {"job": job.as_dict(), "report": str(out_json)}


@app.get("/api/suite/runs/{name}/eval", dependencies=[Depends(require_auth)])
def eval_report(name: str) -> dict:
    safe = Path(str(name)).name
    path = REG.runs / safe / "eval.json"
    if not path.exists() and safe == "yolo26n_optical":
        doc_eval = PROJECT_ROOT / "docs" / "model_eval.json"
        if doc_eval.exists():
            path = doc_eval
    if not path.exists():
        raise HTTPException(404, "no evaluation yet")
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(500, f"unreadable report: {exc}")


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
@app.get("/api/suite/models", dependencies=[Depends(require_auth)])
def models() -> dict:
    return {"models": REG.list_models(), "shipped": REG.shipped_models(),
            "active": REG.active(), "runs": REG.list_runs()}


@app.post("/api/suite/models/promote", dependencies=[Depends(require_auth)])
def promote(payload: dict) -> dict:
    run = str(payload.get("run") or "")
    if not run:
        raise HTTPException(400, "run is required")
    try:
        active = REG.promote(run, payload.get("name"))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    return {"active": active, "models": REG.list_models()}


@app.post("/api/suite/models/{name}/activate", dependencies=[Depends(require_auth)])
def activate(name: str) -> dict:
    try:
        active = REG.activate(name)
        if DB is not None:
            DB.activate_model(name)
        BUS.publish({"kind": "model.activated", "model": active})
        return {"active": active}
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))


@app.delete("/api/suite/models/{name}", dependencies=[Depends(require_auth)])
def delete_model(name: str) -> dict:
    ok = REG.delete_model(name)
    if DB is not None:
        DB.delete_model(name)
    if not ok:
        raise HTTPException(404, "no such model")
    return {"ok": True, "active": REG.active()}


# --------------------------------------------------------------------------- #
# jobs
# --------------------------------------------------------------------------- #
@app.get("/api/jobs", dependencies=[Depends(require_auth)])
def jobs(kind: str | None = None, limit: int = 30) -> dict:
    out = []
    for j in JOBS.list(kind=kind, limit=limit):
        if j.live:
            snap = JOBS.snapshot(j.id, lines=20)
            if snap is not None:
                out.append(snap)
                continue
        out.append(j.as_dict())
    return {"jobs": out}


@app.get("/api/jobs/{jid}", dependencies=[Depends(require_auth)])
def job(jid: str, lines: int = 120) -> dict:
    snap = JOBS.snapshot(jid, lines=max(10, min(lines, 2000)))
    if snap is None:
        raise HTTPException(404, "no such job")
    return snap


@app.post("/api/jobs/{jid}/pause", dependencies=[Depends(require_auth)])
def pause_job(jid: str) -> dict:
    log.info("job pause id=%s", jid)
    job_ = JOBS.get(jid)
    if job_ is None:
        raise HTTPException(404, "no such job")
    proc = JOBS._procs.get(jid)
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGSTOP)
        except OSError:
            proc.send_signal(signal.SIGSTOP)
    with JOBS._lock:
        job_.message = "paused"
        JOBS._save(job_)
    JOBS._emit("job.paused", job_)
    return job_.as_dict()


@app.post("/api/jobs/{jid}/resume", dependencies=[Depends(require_auth)])
def resume_job(jid: str) -> dict:
    log.info("job resume id=%s", jid)
    job_ = JOBS.get(jid)
    if job_ is None:
        raise HTTPException(404, "no such job")
    proc = JOBS._procs.get(jid)
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGCONT)
        except OSError:
            proc.send_signal(signal.SIGCONT)
    with JOBS._lock:
        job_.message = ""
        JOBS._save(job_)
    JOBS._emit("job.resumed", job_)
    return job_.as_dict()
@app.post("/api/jobs/{jid}/cancel", dependencies=[Depends(require_auth)])
def cancel_job(jid: str) -> dict:
    log.info("job cancel id=%s", jid)
    job_ = JOBS.cancel(jid)
    if job_ is None:
        raise HTTPException(404, "no such job")
    return job_.as_dict()


@app.delete("/api/jobs/{jid}", dependencies=[Depends(require_auth)])
def delete_job(jid: str) -> dict:
    log.info("job delete id=%s", jid)
    ok = JOBS.delete(jid)
    if not ok:
        raise HTTPException(404, "no such job")
    return {"ok": True, "id": jid}


# --------------------------------------------------------------------------- #
# S2C
# --------------------------------------------------------------------------- #
@app.get("/api/suite/s2c/options", dependencies=[Depends(require_auth)])
def s2c_options() -> dict:
    from ..config import MODULATIONS
    from ..protocol import finder as finder_mod

    env = describe_env()
    return {
        "active": REG.active(),
        "modulations": sorted(MODULATIONS),
        "eccs": ["rs8", "rs16", "rs32", "rs64", "ldpc12", "ldpc23", "none"],
        "resolutions": ["1280x720", "1920x1080", "2560x1440"],
        # the finder-pattern catalogue: name, size, span options and the
        # capacity each shape costs at the current default geometry
        "finder_shapes": [{"name": "none", "size": 0, "symmetry": "none",
                           "description": "no finder pattern (maximum payload)"}]
        + [shape.as_dict() for shape in finder_mod.SHAPES.values()],
        "finder_spans": [{"name": k, "description": v}
                         for k, v in finder_mod.SPANS.items()],
        "transfers": _s2c().list(),
        "env": env,
    }


@app.post("/api/suite/s2c/transmit", dependencies=[Depends(require_auth)])
async def s2c_transmit(payload: dict) -> dict:
    engine = _s2c()
    text = payload.get("text")
    name = str(payload.get("name") or "")
    data = None
    if payload.get("data_b64"):
        import base64

        try:
            data = base64.b64decode(payload["data_b64"])
        except Exception:
            raise HTTPException(400, "data_b64 is not valid base64")
    if text is None and data is None:
        raise HTTPException(400, "send text or a file")
    try:
        tr = await run_in_threadpool(
            engine.transmit, text, data, name,
            str(payload.get("modulation", "rgb8")),
            str(payload.get("ecc", "rs32")),
            str(payload.get("resolution", "1280x720")),
            payload.get("cells") or None,      # None -> robustness-first default
            int(payload.get("symbol_px", 16)),
            int(payload.get("repeat", 2)),
            float(payload.get("fps", 3.0)),
            S2C_MAX_FRAMES,
            str(payload.get("finder") or "none"),
            str(payload.get("finder_span") or "corners"),
        )
    except KeyError as exc:                    # unknown finder shape / span
        raise HTTPException(400, str(exc).strip("'\""))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if DB is not None:
        try:
            DB.record_transfer(tr.info(), project_id=None)
        except Exception as exc:
            print(f"[suite] store: {exc}", flush=True)
    BUS.publish({"kind": "s2c.transmit", "transfer": {"id": tr.id,
                                                     "frames": tr.count}})
    return {"transfer": tr.info(), "result": tr.result()}


@app.get("/api/suite/s2c/transfers", dependencies=[Depends(require_auth)])
def s2c_transfers() -> dict:
    return {"transfers": _s2c().list()}


@app.get("/api/suite/s2c/{tid}/frame.png", dependencies=[Depends(require_auth)])
def s2c_frame(tid: str, index: int = 0) -> Response:
    try:
        png = _s2c().frame_png(tid, index)
    except KeyError:
        raise HTTPException(404, "no such transfer")
    return Response(png, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/suite/s2c/{tid}/payload", dependencies=[Depends(require_auth)])
def s2c_payload(tid: str) -> Response:
    """The reassembled bytes, as a plain download the phone can save."""
    try:
        blob, name = _s2c().payload(tid)
    except KeyError:
        raise HTTPException(404, "no such transfer")
    except LookupError as exc:
        raise HTTPException(409, str(exc))
    return Response(blob, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{name}"',
                             "Cache-Control": "no-store"})


@app.get("/api/suite/s2c/{tid}/preview.jpg", dependencies=[Depends(require_auth)])
def s2c_preview(tid: str, index: int = 0) -> Response:
    try:
        blob = _s2c().frame_jpeg(tid, index)
    except KeyError:
        raise HTTPException(404, "no such transfer")
    return Response(blob, media_type="image/jpeg")


@app.get("/api/suite/s2c/{tid}", dependencies=[Depends(require_auth)])
def s2c_state(tid: str) -> dict:
    try:
        tr = _s2c().get(tid)
    except KeyError:
        raise HTTPException(404, "no such transfer")
    return {"transfer": tr.info(), "result": tr.result(), "log": tr.log[-12:]}


@app.post("/api/suite/s2c/{tid}/receive", dependencies=[Depends(require_auth)])
async def s2c_receive(tid: str, file: UploadFile = File(...)) -> dict:
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty upload")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "upload too large")
    try:
        out = await run_in_threadpool(_s2c().receive_bytes, tid, raw)
    except KeyError:
        raise HTTPException(404, "no such transfer")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    _record_transfer(out.get("transfer") or {})
    if out.get("result", {}).get("complete"):
        BUS.publish({"kind": "s2c.complete", "transfer": {"id": tid}})
    return out


def _record_transfer(info: dict) -> None:
    """Mirror a transfer snapshot into the database (best effort)."""
    if DB is None or not info.get("id"):
        return
    try:
        DB.record_transfer(info)
    except Exception as exc:                 # never fail a receive on bookkeeping
        print(f"[suite] store: {exc}", flush=True)


@app.post("/api/suite/s2c/{tid}/reset", dependencies=[Depends(require_auth)])
def s2c_reset(tid: str) -> dict:
    try:
        return {"result": _s2c().reset_rx(tid)}
    except KeyError:
        raise HTTPException(404, "no such transfer")


@app.post("/api/suite/s2c/{tid}/loopback", dependencies=[Depends(require_auth)])
async def s2c_loopback(tid: str, payload: dict | None = None) -> dict:
    payload = payload or {}
    try:
        tr = _s2c().get(tid)
    except KeyError:
        raise HTTPException(404, "no such transfer")
    _s2c().reset_rx(tid)
    report = await run_in_threadpool(
        _s2c().loopback, tid, float(payload.get("level", 0.35)),
        int(payload.get("frames", min(4, tr.count))), int(payload.get("seed", 0)))
    report["result"] = _s2c().get(tid).result()
    _record_transfer({**_s2c().get(tid).info(), **report["result"],
                      "complete": report["result"].get("complete")})
    BUS.publish({"kind": "s2c.loopback", "subject": tid,
                 "decoded": report.get("decoded", 0)})
    return report


# --------------------------------------------------------------------------- #
# optical channel lab (single-frame physics & demodulator workbench)
# --------------------------------------------------------------------------- #
def _jpeg_data_url(img: Any, max_px: int = 840, quality: int = 86) -> str:
    import cv2

    h, w = img.shape[:2]
    if max(h, w) > max_px:
        scale = max_px / max(h, w)
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


def _rectify_quad_preview(img: Any, quad_pts: Any, out_px: int = 320) -> str:
    import cv2
    import numpy as np

    src = np.asarray(quad_pts, dtype=np.float32)[:4]
    dst = np.array([[0, 0], [out_px - 1, 0], [out_px - 1, out_px - 1], [0, out_px - 1]],
                   dtype=np.float32)
    mat = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(img, mat, (out_px, out_px), flags=cv2.INTER_LINEAR)
    return _jpeg_data_url(warped, max_px=out_px, quality=90)


def _draw_lab_overlay(img: Any, best: Any, detections: list, gt_quad: Any = None) -> Any:
    import cv2
    import numpy as np

    vis = img.copy()
    names = ["TL", "TR", "BR", "BL"]
    if gt_quad is not None:
        gq = np.asarray(gt_quad, np.int32).reshape(-1, 1, 2)
        cv2.polylines(vis, [gq], True, (235, 200, 40), 1, cv2.LINE_AA)
    for d in detections:
        dq = d.quad.pts.astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(vis, [dq], True, (40, 175, 255), 2, cv2.LINE_AA)
    if best is not None and best.quad is not None:
        q = np.asarray(best.quad, np.int32)
        color = (140, 225, 50) if best.ok else (90, 90, 255)
        cv2.polylines(vis, [q.reshape(-1, 1, 2)], True, color, 2, cv2.LINE_AA)
        for (x, y), nm in zip(q[:4], names):
            cv2.circle(vis, (int(x), int(y)), 5, color, -1, cv2.LINE_AA)
            cv2.circle(vis, (int(x), int(y)), 7, (15, 20, 28), 1, cv2.LINE_AA)
            cv2.putText(vis, nm, (int(x) + 8, max(16, int(y) - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.52, color, 2, cv2.LINE_AA)
    return vis


def _run_lab_simulate(payload: dict) -> dict:
    import numpy as np
    from ..phy.channel import ChannelConfig, grid_quad_from_frame_quad, rect_quad, simulate
    from ..phy.render import screen_geometry
    from ..protocol.packet import Packetizer
    from ..receiver import HybridReceiver
    from ..sim import prepare_frame

    modulation = str(payload.get("modulation") or "rgb8")
    ecc = str(payload.get("ecc") or "rs32")
    resolution = str(payload.get("resolution") or "1280x720")
    cells = int(payload["cells"]) if payload.get("cells") else None
    symbol_px = max(6, min(48, int(payload.get("symbol_px") or 16)))
    finder = str(payload.get("finder") or "ring")
    finder_span = str(payload.get("finder_span") or "corners")
    level = max(0.0, min(1.3, float(payload.get("level", 0.35))))
    seed = int(payload.get("seed", 42))
    mode = str(payload.get("mode") or "auto")
    if mode not in ("auto", "yolo", "classical"):
        mode = "auto"

    cfg = make_config(modulation, ecc, resolution, cells, symbol_px,
                      finder=finder, finder_span=finder_span)
    desc = cfg.describe()
    text = str(payload.get("text") or "Optical Modem Lab — Photon Frame #01")
    raw_bytes = text.encode("utf-8")
    pkt = Packetizer(cfg)
    frames = list(pkt.frames_for(raw_bytes))
    if not frames:
        raise ValueError("text produced no frames")
    hdr, body = frames[0]
    tx_img = prepare_frame(cfg, hdr, body)

    rng = np.random.default_rng(seed)
    if level <= 0.005:
        cam_img = tx_img.copy()
        if cfg.display is not None:
            gx, gy, gw, gh = screen_geometry(cfg)["grid"]
            gt = np.array([[gx, gy], [gx + gw, gy], [gx + gw, gy + gh], [gx, gy + gh]],
                          dtype=np.float32)
        else:
            fh, fw = tx_img.shape[:2]
            placed = np.array([[0, 0], [fw, 0], [fw, fh], [0, fh]], dtype=np.float32)
            gt = grid_quad_from_frame_quad(placed, cfg.frame_px, cfg.cell_px,
                                           cfg.quiet_zone_cells, cfg.layout.grid)
    else:
        ch = ChannelConfig.handheld(rng, level)
        ch.out_size = 960
        cam_img, placed = simulate(tx_img, ch, rng)
        if cfg.display is not None:
            gt = rect_quad(placed, tx_img.shape[:2], screen_geometry(cfg)["grid"])
        else:
            gt = grid_quad_from_frame_quad(placed, cfg.frame_px, cfg.cell_px,
                                           cfg.quiet_zone_cells, cfg.layout.grid)

    try:
        rx = HybridReceiver(cfg, mode=mode, yolo_weights=REG.active_weights(), yolo_imgsz=448)
    except Exception as exc:
        if mode == "yolo":
            raise ValueError(str(exc))
        rx = HybridReceiver(cfg, mode="classical")

    t0 = time.perf_counter()
    rep = rx.decode(cam_img)
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    best = rep.best()
    used_quad = best.quad if (best is not None and best.quad is not None) else (
        rep.detections[0].quad.pts if rep.detections else None
    )
    corner_err_pct = None
    if used_quad is not None and gt is not None:
        side = max(1.0, float(np.linalg.norm(gt[0] - gt[1])))
        corner_err_pct = round(
            100.0 * float(np.linalg.norm(np.asarray(used_quad)[:4] - gt, axis=1).mean()) / side,
            2,
        )

    decoded_text = ""
    if rep.ok and best is not None and best.payload is not None:
        # body carries user chunk (with frame packetizer overhead in Packetizer.push)
        p2 = Packetizer(cfg)
        from ..protocol.packet import DecodedFrame
        msg = p2.push(DecodedFrame(header=best.header, payload=best.payload,
                                   crc_ok=True, fec_ok=True,
                                   n_corrected=best.n_corrected,
                                   quality=best.quality))
        raw_out = msg if msg is not None else best.payload
        try:
            decoded_text = raw_out.decode("utf-8")
        except UnicodeDecodeError:
            decoded_text = raw_out.hex()

    overlay = _draw_lab_overlay(cam_img, best, rep.detections, gt)
    rectified = _rectify_quad_preview(cam_img, used_quad) if used_quad is not None else ""

    return {
        "ok": bool(rep.ok),
        "payload_matches": bool(rep.ok and best is not None and best.payload == body),
        "decoded_text": decoded_text,
        "total_frames_for_text": len(frames),
        "frame_body_bytes": len(body),
        "config": desc,
        "telemetry": {
            "mode_requested": mode,
            "yolo_active": rx.yolo is not None,
            "yolo_note": rx.yolo_error,
            "detector": best.detector if best is not None else ("yolo26" if rep.detections else "classical"),
            "detections": len(rep.detections),
            "ring_score": round(float(best.ring_score), 3) if best is not None else None,
            "finder_score": (round(float(best.finder_score), 3)
                             if best is not None and best.finder_score is not None else None),
            "quality": round(float(best.quality), 3) if best is not None else None,
            "n_corrected": int(best.n_corrected) if best is not None else 0,
            "corner_err_pct": corner_err_pct,
            "elapsed_ms": elapsed_ms,
            "timings_ms": {k: round(float(v) * 1000, 1) for k, v in rep.timings.items()},
            "reason": "" if rep.ok else (best.reason if best is not None else "no frame found"),
        },
        "tx_preview": _jpeg_data_url(tx_img, max_px=760, quality=90),
        "cam_overlay": _jpeg_data_url(overlay, max_px=840, quality=88),
        "rectified": rectified,
    }


def _run_lab_decode(raw: bytes, params: dict) -> dict:
    import cv2
    import numpy as np
    from ..protocol.packet import DecodedFrame, Packetizer
    from ..receiver import HybridReceiver

    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("uploaded file is not a readable image")

    modulation = str(params.get("modulation") or "rgb8")
    ecc = str(params.get("ecc") or "rs32")
    resolution = str(params.get("resolution") or "1280x720")
    cells = int(params["cells"]) if params.get("cells") else None
    symbol_px = max(6, min(48, int(params.get("symbol_px") or 16)))
    finder = str(params.get("finder") or "none")
    mode = str(params.get("mode") or "auto")
    cfg = make_config(modulation, ecc, resolution, cells, symbol_px, finder=finder)
    try:
        rx = HybridReceiver(cfg, mode=mode, yolo_weights=REG.active_weights(), yolo_imgsz=448)
    except Exception as exc:
        if mode == "yolo":
            raise ValueError(str(exc))
        rx = HybridReceiver(cfg, mode="classical")

    t0 = time.perf_counter()
    rep = rx.decode(img)
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    best = rep.best()
    used_quad = best.quad if (best is not None and best.quad is not None) else (
        rep.detections[0].quad.pts if rep.detections else None
    )
    decoded_text = ""
    decoded_bytes = 0
    if rep.ok and best is not None and best.payload is not None:
        p2 = Packetizer(cfg)
        msg = p2.push(DecodedFrame(header=best.header, payload=best.payload,
                                   crc_ok=True, fec_ok=True,
                                   n_corrected=best.n_corrected,
                                   quality=best.quality))
        raw_out = msg if msg is not None else best.payload
        decoded_bytes = len(raw_out)
        try:
            decoded_text = raw_out.decode("utf-8")
        except UnicodeDecodeError:
            decoded_text = raw_out.hex()

    overlay = _draw_lab_overlay(img, best, rep.detections, None)
    rectified = _rectify_quad_preview(img, used_quad) if used_quad is not None else ""
    return {
        "ok": bool(rep.ok),
        "decoded_text": decoded_text,
        "decoded_bytes": decoded_bytes,
        "image_size": [int(img.shape[1]), int(img.shape[0])],
        "telemetry": {
            "mode_requested": mode,
            "yolo_active": rx.yolo is not None,
            "detector": best.detector if best is not None else ("yolo26" if rep.detections else "classical"),
            "detections": len(rep.detections),
            "ring_score": round(float(best.ring_score), 3) if best is not None else None,
            "finder_score": (round(float(best.finder_score), 3)
                             if best is not None and best.finder_score is not None else None),
            "quality": round(float(best.quality), 3) if best is not None else None,
            "n_corrected": int(best.n_corrected) if best is not None else 0,
            "seq": int(best.header.seq) if (best and best.header) else None,
            "elapsed_ms": elapsed_ms,
            "reason": "" if rep.ok else (best.reason if best is not None else "no frame found"),
        },
        "cam_overlay": _jpeg_data_url(overlay, max_px=840, quality=88),
        "rectified": rectified,
    }


@app.post("/api/suite/lab/simulate", dependencies=[Depends(require_auth)])
async def lab_simulate(payload: dict) -> dict:
    try:
        return await run_in_threadpool(_run_lab_simulate, payload)
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/suite/lab/decode", dependencies=[Depends(require_auth)])
async def lab_decode(
    file: UploadFile = File(...),
    modulation: str = Form("rgb8"),
    ecc: str = Form("rs32"),
    resolution: str = Form("1280x720"),
    cells: str = Form(""),
    symbol_px: int = Form(16),
    finder: str = Form("none"),
    mode: str = Form("auto"),
) -> dict:
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty upload")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "upload too large")
    try:
        return await run_in_threadpool(_run_lab_decode, raw, {
            "modulation": modulation, "ecc": ecc, "resolution": resolution,
            "cells": int(cells) if cells.strip() else None,
            "symbol_px": symbol_px, "finder": finder, "mode": mode,
        })
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc))


# --------------------------------------------------------------------------- #
# misc
# --------------------------------------------------------------------------- #
@app.get("/api/suite/export/manifest", dependencies=[Depends(require_auth)])
def export_manifest() -> JSONResponse:
    """One JSON with everything needed to reproduce this box elsewhere."""
    state = _stage_state()
    state["capture_sessions"] = capture_app.STORE.list()
    state["generated"] = time.time()
    return JSONResponse(state)


def _require_internal(request: Request) -> None:
    peer = request.client.host if request.client else ""
    xff = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    # Block direct internet access (public peer) and proxied public clients.
    try:
        peer_addr = ipaddress.ip_address(peer) if peer else None
    except ValueError:
        peer_addr = None
    peer_ok = peer_addr is not None and (peer_addr.is_loopback or peer_addr.is_private or peer_addr.is_reserved)
    xff_public = False
    if xff:
        try:
            xa = ipaddress.ip_address(xff)
            xff_public = not (xa.is_loopback or xa.is_private or xa.is_reserved)
        except ValueError:
            xff_public = True
    if not peer_ok or xff_public:
        raise HTTPException(404, "not found")


@app.get("/healthz", dependencies=[Depends(_require_internal)])
def healthz(request: Request, response: Response) -> dict:
    """Liveness + a summary the compose healthcheck and Coolify can read."""
    # The database is part of the health contract: a container whose store is
    # unreachable must fail its healthcheck rather than serve broken pages.
    db_ok, db_info = None, {"kind": "disabled"}
    if DB is not None:
        try:
            DB.counts()
            db_ok, db_info = True, DB.info()
        except Exception as exc:
            db_ok, db_info = False, {"kind": DB.kind, "error": str(exc)[:200]}
    if db_ok is False:
        response.status_code = 503
    return {"ok": db_ok is not False, "service": "optical-modem-suite",
            "root": str(DATA_DIR), "build": net.build_id(),
            "sessions": len(capture_app.STORE.list()),
            "datasets": len(REG.list_datasets()),
            "models": len(REG.list_models()),
            "active_model": (REG.active() or {}).get("name"),
            "jobs_running": sum(1 for j in JOBS.list(limit=50) if j.live),
            "password_set": bool(capture_app.APP_PASSWORD),
            "database": {**db_info, "ok": db_ok},
            "bus": BUS.info()}


@app.get("/version", dependencies=[Depends(_require_internal)])
def version() -> dict:
    from .. import __version__ as v

    return {"version": v, "env": describe_env()}


@app.get("/{full_path:path}", response_class=HTMLResponse)
def spa_catchall(full_path: str) -> HTMLResponse:
    """Catch-all for client-side routing in the React SPA.

    Serves the SPA index.html for any path that doesn't match an API route
    or a static file.  The React Router handles the actual routing.
    """
    # Don't intercept API or static asset requests
    if full_path.startswith("api/") or full_path.startswith("static/") or full_path.startswith("suite-assets/"):
        raise HTTPException(404, "not found")
    path = STATIC / "index.html"
    if not path.exists():
        raise HTTPException(500, "missing index.html — run the frontend build")
    return HTMLResponse(net.stamp_build(path.read_text()),
                        headers={"Cache-Control": "no-store"})
