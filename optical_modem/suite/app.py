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

import io
import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import (Depends, FastAPI, File, Form, HTTPException, Query, Request,
                     Response, UploadFile)
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, StreamingResponse)
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from ..capture import app as capture_app
from ..capture.app import MAX_UPLOAD_BYTES, require_auth  # noqa: F401  (re-exported)
from . import datasets as dsets
from . import progress as progress_mod
from .bus import get_bus
from .db import get_store
from .jobs import JobManager
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
JOBS = JobManager(DATA_DIR, project_root=PROJECT_ROOT,
                  python=os.environ.get("SUITE_PYTHON") or None,
                  max_concurrent=int(os.environ.get("SUITE_MAX_JOBS", "1")),
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
    return HTMLResponse(path.read_text(),
                        headers={"Cache-Control": "no-cache"})


def _python_argv() -> list[str]:
    """The interpreter jobs run in (``SUITE_PYTHON`` lets you swap in a venv)."""
    import sys

    return [os.environ.get("SUITE_PYTHON") or sys.executable, "-u"]


def _dataset_path(name: str) -> Path:
    p = REG.dataset_dir(name)
    if not p.is_dir():
        raise HTTPException(404, f"no dataset {name}")
    return p


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
# pages
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    return _page("suite.html")


@app.get("/login", response_class=HTMLResponse)
def login_page() -> HTMLResponse:
    """The capture station's landing page doubles as the suite's sign-in page."""
    from ..capture.app import STATIC as CAPTURE_STATIC

    path = CAPTURE_STATIC / "index.html"
    return HTMLResponse(path.read_text(), headers={"Cache-Control": "no-cache"})


@app.get("/projects", response_class=HTMLResponse)
def projects_page() -> HTMLResponse:
    return _page("projects.html")


@app.get("/wizard", response_class=HTMLResponse)
def wizard_page() -> HTMLResponse:
    """The guided flow: one stage at a time, with the action inline."""
    return _page("wizard.html")


@app.get("/guide", response_class=HTMLResponse)
def guide() -> HTMLResponse:
    return _page("guide.html")


@app.get("/dataset", response_class=HTMLResponse)
def dataset_page() -> HTMLResponse:
    return _page("dataset.html")


@app.get("/train", response_class=HTMLResponse)
def train_page() -> HTMLResponse:
    return _page("train.html")


@app.get("/evaluate", response_class=HTMLResponse)
def evaluate_page() -> HTMLResponse:
    return _page("evaluate.html")


@app.get("/models", response_class=HTMLResponse)
def models_page() -> HTMLResponse:
    return _page("models.html")


@app.get("/s2c", response_class=HTMLResponse)
def s2c_page() -> HTMLResponse:
    return _page("s2c.html")


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
                             headers={"Cache-Control": "no-cache",
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


@app.post("/api/suite/datasets/synth", dependencies=[Depends(require_auth)])
def synth_dataset(payload: dict) -> dict:
    name = str(payload.get("name") or "").strip() or time.strftime("synth-%Y%m%d-%H%M%S")
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
    job = JOBS.start("synth", f"Generate {n_train}+{n_val} synthetic frames", argv,
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
    out = REG.dataset_dir(name)
    if out.exists():
        raise HTTPException(409, f"dataset {name} already exists")
    argv = _python_argv() + ["-m", "optical_modem.suite.tasks", "captures",
                             "--data-dir", str(DATA_DIR), "--out", str(out),
                             "--val-frac", str(float(payload.get("val_frac", 0.15)))]
    if payload.get("verified_only", True):
        argv.append("--verified-only")
    project = _known_project(payload.get("project"))
    job = JOBS.start("captures", f"Convert captured photos -> {name}", argv,
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
    out = REG.dataset_dir(name)
    if out.exists():
        raise HTTPException(409, f"dataset {name} already exists")
    argv = _python_argv() + ["-m", "optical_modem.suite.tasks", "merge",
                             "--out", str(out)]
    for s in sources:
        argv += ["--source", str(REG.dataset_dir(s))]
    project = _known_project(payload.get("project"))
    job = JOBS.start("merge", f"Merge {len(sources)} dataset(s) -> {name}", argv,
                     meta={"dataset": name, "sources": sources, "project": project})
    if DB is not None:
        DB.upsert_dataset(name, project=project, source="merge", status="building",
                          path=str(out), params={"sources": sources})
    return {"job": job.as_dict(), "dataset": name}


@app.get("/api/suite/datasets/{name}", dependencies=[Depends(require_auth)])
def dataset_detail(name: str) -> dict:
    return REG.dataset_summary(_dataset_path(name))


@app.get("/api/suite/datasets/{name}/preview", dependencies=[Depends(require_auth)])
def dataset_preview(name: str, split: str = "train", index: int = 0) -> Response:
    blob = dsets.preview(_dataset_path(name), split=split, index=index,
                         annotated=True)
    if blob is None:
        raise HTTPException(404, "no images in that split")
    return Response(blob, media_type="image/jpeg",
                    headers={"Cache-Control": "no-cache"})


@app.delete("/api/suite/datasets/{name}", dependencies=[Depends(require_auth)])
def drop_dataset(name: str) -> dict:
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
        "base_models": (["yolo26n-pose.pt", "yolo26s-pose.pt", "yolo26m-pose.pt"]),
        "active": REG.active(),
    }


@app.post("/api/suite/train", dependencies=[Depends(require_auth)])
async def start_training(payload: dict) -> dict:
    dataset = str(payload.get("dataset") or "")
    if not dataset:
        raise HTTPException(400, "choose a dataset")
    yaml_path = _require_yaml(dataset)
    name = str(payload.get("name") or "").strip() or time.strftime("run-%Y%m%d-%H%M%S")
    out = REG.runs / name
    if out.exists():
        raise HTTPException(409, f"run {name} already exists")
    epochs = int(payload.get("epochs", 40))
    imgsz = int(payload.get("imgsz", 448))
    batch = int(payload.get("batch", 8))
    if not (1 <= epochs <= 2000):
        raise HTTPException(400, "epochs must be 1..2000")
    if not (160 <= imgsz <= 1280):
        raise HTTPException(400, "imgsz must be 160..1280")
    if not (1 <= batch <= 128):
        raise HTTPException(400, "batch must be 1..128")
    argv = _python_argv() + [
        "-m", "optical_modem.data.train_yolo",
        "--data", str(yaml_path),
        "--model", str(payload.get("model", "yolo26n-pose.pt")),
        "--epochs", str(epochs), "--imgsz", str(imgsz), "--batch", str(batch),
        "--device", str(payload.get("device", "cpu")),
        "--workers", str(int(payload.get("workers", 2))),
        "--project", str(REG.runs), "--name", name,
        "--seed", str(int(payload.get("seed", 0))),
    ]
    project = _known_project(payload.get("project"))
    job = JOBS.start("train", f"Train {name} ({epochs} epochs @ {imgsz}px)", argv,
                     meta={"run": name, "dataset": dataset, "epochs": epochs,
                           "imgsz": imgsz, "project": project})
    if DB is not None:
        DB.upsert_run(name, project=project, dataset=dataset, epochs=epochs,
                      imgsz=imgsz, batch=batch, device=str(payload.get("device", "cpu")),
                      base_model=str(payload.get("model", "")), status="running",
                      path=str(out))
    return {"job": job.as_dict(), "run": name}


@app.get("/api/suite/runs", dependencies=[Depends(require_auth)])
def list_runs() -> dict:
    return {"runs": REG.list_runs(), "active": REG.active()}


@app.post("/api/suite/runs/{name}/evaluate", dependencies=[Depends(require_auth)])
def evaluate_run(name: str, payload: dict | None = None) -> dict:
    payload = payload or {}
    run = REG.runs / name
    weights = run / "weights" / "best.pt"
    if not weights.exists():
        raise HTTPException(404, f"run {name} has no best.pt")
    dataset = str(payload.get("dataset") or "")
    if dataset:
        _require_yaml(dataset)
    level = float(payload.get("level", 0.6))
    n = int(payload.get("n", 12))
    out_json = run / "eval.json"
    argv = _python_argv() + [str(PROJECT_ROOT / "scripts" / "evaluate.py"),
                             "--weights", str(weights),
                             "--n", str(n), "--levels", str(level),
                             "--out", str(out_json)]
    if dataset:
        argv += ["--dataset", str(REG.dataset_dir(dataset))]
    job = JOBS.start("evaluate", f"Evaluate {name}", argv,
                     meta={"run": name, "weights": str(weights),
                           "report": str(out_json), "dataset": dataset})
    return {"job": job.as_dict(), "report": str(out_json)}


@app.get("/api/suite/runs/{name}/eval", dependencies=[Depends(require_auth)])
def eval_report(name: str) -> dict:
    path = REG.runs / name / "eval.json"
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
    return {"models": REG.list_models(), "active": REG.active(),
            "runs": REG.list_runs()}


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
    return {"jobs": [j.as_dict() for j in JOBS.list(kind=kind, limit=limit)]}


@app.get("/api/jobs/{jid}", dependencies=[Depends(require_auth)])
def job(jid: str, lines: int = 120) -> dict:
    snap = JOBS.snapshot(jid, lines=max(10, min(lines, 2000)))
    if snap is None:
        raise HTTPException(404, "no such job")
    return snap


@app.post("/api/jobs/{jid}/cancel", dependencies=[Depends(require_auth)])
def cancel_job(jid: str) -> dict:
    job_ = JOBS.cancel(jid)
    if job_ is None:
        raise HTTPException(404, "no such job")
    return job_.as_dict()


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
# misc
# --------------------------------------------------------------------------- #
@app.get("/api/suite/export/manifest", dependencies=[Depends(require_auth)])
def export_manifest() -> JSONResponse:
    """One JSON with everything needed to reproduce this box elsewhere."""
    state = _stage_state()
    state["capture_sessions"] = capture_app.STORE.list()
    state["generated"] = time.time()
    return JSONResponse(state)


@app.get("/healthz")
def healthz() -> dict:
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
    return {"ok": db_ok is not False, "service": "optical-modem-suite",
            "root": str(DATA_DIR),
            "sessions": len(capture_app.STORE.list()),
            "datasets": len(REG.list_datasets()),
            "models": len(REG.list_models()),
            "active_model": (REG.active() or {}).get("name"),
            "jobs_running": sum(1 for j in JOBS.list(limit=50) if j.live),
            "password_set": bool(capture_app.APP_PASSWORD),
            "database": {**db_info, "ok": db_ok},
            "bus": BUS.info()}


@app.get("/version")
def version() -> dict:
    from .. import __version__ as v

    return {"version": v, "env": describe_env()}
