"""Standalone job worker — runs training/synth/eval in a separate container.

Usage:
    python -m optical_modem.suite.worker

When the suite is started with ``SUITE_RUN_JOBS=worker`` the web process only
queues jobs and this worker claims and runs them. Both share ``DATA_DIR`` and
the database; Redis carries the events so the UI stays live.

This is deliberately a thin loop around :meth:`JobManager.tick`: claiming,
capacity, adoption and reaping all live in one place so the web process and the
worker cannot drift apart. An earlier version called ``jm._spawn()`` directly,
which skipped the claim — the exact double execution this mode exists to avoid.

Environment: same as the suite (DATA_DIR, DATABASE_URL, REDIS_URL, ...).
"""
from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from optical_modem.suite.jobs import JobManager

try:
    from optical_modem.suite.logging_config import configure as _configure_logging, get_logger as _get_logger
    _configure_logging()
    log = _get_logger("suite.worker")
except Exception:  # pragma: no cover
    import logging
    log = logging.getLogger("suite.worker")


def _bus_store():
    """Connect to the shared store and bus, degrading to neither if unavailable.

    The store is not optional in practice: without it there is no queue, no
    claim, and two workers would run everything twice. So a failure here is
    fatal rather than silently single-node.
    """
    from optical_modem.suite.db import get_store as _get_store
    from optical_modem.suite.bus import get_bus as _get_bus
    store, bus = None, None
    try:
        store = _get_store()
    except Exception as exc:
        print(f"[worker] FATAL: no shared job store: {exc}", flush=True)
        raise
    try:
        bus = _get_bus()
    except Exception as exc:
        print(f"[worker] bus unavailable, events will not fan out: {exc}", flush=True)
    return store, bus


def main() -> None:
    data_dir = Path(os.environ.get("DATA_DIR", "/data")).resolve()
    project_root = Path(os.environ.get("PROJECT_ROOT", str(HERE))).resolve()
    python = os.environ.get("SUITE_PYTHON") or sys.executable
    max_concurrent = int(os.environ.get("SUITE_MAX_JOBS", "1"))
    idle_sleep = float(os.environ.get("SUITE_WORKER_IDLE_POLL", "2.0"))
    busy_sleep = float(os.environ.get("SUITE_WORKER_BUSY_POLL", "0.5"))

    store, bus = _bus_store()

    jm = JobManager(
        data_dir, python=python, project_root=project_root,
        max_concurrent=max_concurrent,
        env={**os.environ, "DATA_DIR": str(data_dir),
             "YOLO_OFFLINE": os.environ.get("YOLO_OFFLINE", "1"),
             "PYTHONPATH": str(project_root)},
        store=store, bus=bus,
        # not inprocess: this process claims from the shared queue rather than
        # assuming anything it enqueues is its own to run
        run_inprocess=False,
    )

    # ---- graceful shutdown ---------------------------------------------
    # On SIGTERM (docker stop, a rolling deploy) stop taking new work but let
    # running jobs finish, then release them so another worker picks them up.
    # Killing a worker mid-training wastes however long the run already took.
    stopping = {"now": False}

    def _handle(signum, _frame):
        name = signal.Signals(signum).name
        if stopping["now"]:
            print(f"[worker] {name} again; exiting immediately", flush=True)
            raise SystemExit(1)
        stopping["now"] = True
        print(f"[worker] {name}: finishing current jobs, then exiting", flush=True)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handle)
        except (ValueError, OSError):      # pragma: no cover - non-main thread
            pass

    print(f"[worker] started id={jm.worker_id} data_dir={data_dir} "
          f"max_concurrent={max_concurrent}", flush=True)
    log.info("worker started id=%s data_dir=%s max_concurrent=%s",
             jm.worker_id, data_dir, max_concurrent)

    grace_deadline = None
    grace_limit = float(os.environ.get("SUITE_WORKER_DRAIN_TIMEOUT", "900"))
    last_registration = 0.0

    while True:
        try:
            result = jm.tick()
            if any(result.get(k) for k in ("started", "requeued", "interrupted")):
                print(f"[worker] {result}", flush=True)

            # Advertise ourselves so /api/suite/workers can show the fleet.
            # Cheap and TTL'd: a worker that vanishes disappears on its own.
            now = time.time()
            if bus is not None and (now - last_registration) > 30:
                try:
                    bus.heartbeat(f"worker:{jm.worker_id}", 120.0)
                except Exception:
                    pass
                last_registration = now
        except KeyboardInterrupt:
            break
        except Exception as exc:            # never die on a single error
            print(f"[worker] loop error: {exc}", flush=True)
            log.exception("worker loop error")

        if stopping["now"]:
            if not jm._procs:
                print("[worker] drained; exiting", flush=True)
                return
            if grace_deadline is None:
                grace_deadline = time.time() + grace_limit
            if time.time() > grace_deadline:
                print("[worker] drain timeout; releasing jobs for another worker",
                      flush=True)
                for jid in list(jm._procs):
                    jm.queue.release(jid, status="queued")
                return

        # Poll fast while busy so the queue drains promptly; back off when idle
        # rather than hammering the database every 2 s forever.
        time.sleep(busy_sleep if jm._procs else idle_sleep)


if __name__ == "__main__":
    main()
