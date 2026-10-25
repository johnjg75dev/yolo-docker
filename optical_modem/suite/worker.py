"""Standalone job worker — runs training/synth/eval in a separate container.

Usage:
    python -m optical_modem.suite.worker

When the suite is started with ``SUITE_RUN_JOBS=worker`` the web process only
queues jobs (status=queued) and this worker picks them up, runs the
subprocess, and updates ``job.json`` + the database so the UI sees live
progress.  Both processes share ``/data`` (volume) and Redis.

Environment: same as the suite (DATA_DIR, DATABASE_URL, REDIS_URL, ...).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from optical_modem.suite.jobs import JobManager, QUEUED, LIVE


def main() -> None:
    data_dir = Path(os.environ.get("DATA_DIR", "/data")).resolve()
    project_root = Path(os.environ.get("PROJECT_ROOT", str(HERE))).resolve()
    python = os.environ.get("SUITE_PYTHON") or sys.executable
    max_concurrent = int(os.environ.get("SUITE_MAX_JOBS", "1"))

    try:
        from optical_modem.suite.db import get_store as _get_store
        from optical_modem.suite.bus import get_bus as _get_bus
        store = _get_store()
        bus = _get_bus()
    except Exception as exc:
        print(f"[worker] store/bus unavailable: {exc}", flush=True)
        store = None
        bus = None

    jm = JobManager(
        data_dir, python=python, project_root=project_root,
        max_concurrent=max_concurrent,
        env={**os.environ, "DATA_DIR": str(data_dir),
             "YOLO_OFFLINE": os.environ.get("YOLO_OFFLINE", "1"),
             "PYTHONPATH": str(project_root)},
        store=store, bus=bus, run_inprocess=True,
    )
    try:
        jm.adopt_from_store()
    except Exception:
        pass

    print(f"[worker] started; data_dir={data_dir} max_concurrent={max_concurrent}", flush=True)
    while True:
        try:
            jm.adopt_from_store()
            live = [j for j in jm.list() if j.status in LIVE]
            if len(live) < max_concurrent:
                queued = [j for j in jm.list() if j.status == QUEUED]
                for job in queued:
                    if len([j for j in jm.list() if j.status in LIVE]) >= max_concurrent:
                        break
                    print(f"[worker] spawning {job.id}", flush=True)
                    jm._spawn(job, cwd=project_root)
            # heartbeat all live jobs
            for job in live:
                jm.heartbeat(job.id)
        except Exception as exc:  # never die on a single error
            print(f"[worker] loop error: {exc}", flush=True)
        time.sleep(2.0)


if __name__ == "__main__":
    main()
