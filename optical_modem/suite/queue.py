"""Job claiming for separate worker containers (audit S5).

The suite originally ran every job inside the web process, guarded by
`SUITE_RUN_JOBS=inprocess`. That is fine up to a point, but it means a training
run competes with request serving for the same GIL and the same memory budget --
and an OOM kill takes the UI down with it. This module is what lets N worker
containers pull from one queue without ever running the same job twice.

Ownership lives in the database, in one place.

    It is tempting to keep the claim in Redis when Redis is configured, because
    it is faster. Do not: you then have two systems that can disagree about who
    owns a job, and reconciling that is strictly harder than the microseconds it
    saves on a transition that happens once per job. The database is already
    shared by every container (Postgres in compose, a file on the volume for
    SQLite), already durable, and Store.claim_job is a single conditional UPDATE
    that both engines serialise. Redis stays what it is good at here: fanning
    events out to whichever process is listening.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from typing import Any, Callable

try:
    from .logging_config import configure as _configure_logging, get_logger as _get_logger
    _configure_logging()
    log = _get_logger("suite.queue")
except Exception:  # pragma: no cover
    import logging
    log = logging.getLogger("suite.queue")

#: A job whose owner has not heartbeated in this long is presumed dead and may be
#: stolen by another worker. It must comfortably exceed the heartbeat interval --
#: stealing from a live worker would run the job twice.
CLAIM_TTL_SECONDS = float(os.environ.get("SUITE_CLAIM_TTL", "300"))

#: How often a running job refreshes its claim, and how often the fleet reaper
#: sweeps. The TTL is ~8x the heartbeat, so a worker would have to miss several
#: consecutive beats before its job is reassigned.
HEARTBEAT_SECONDS = float(os.environ.get("SUITE_HEARTBEAT", "30"))

#: How many times a job may be (re)started before we conclude it is not going to
#: run -- typically an input that reliably triggers the OOM killer.
MAX_ATTEMPTS = int(os.environ.get("SUITE_MAX_ATTEMPTS", "3"))


def worker_id(prefix: str = "worker") -> str:
    """A short, stable, unique identity for this process.

    Hostname comes from the container id under Docker/Compose, so two replicas
    of the same service are distinguishable in the UI without extra config.
    """
    return f"{prefix}-{socket.gethostname()}-{os.getpid()}"


class JobQueue:
    """Claim / heartbeat / release / reap, on top of ``Store``.

    Every operation is a single conditional statement, so correctness does not
    depend on this process being the only one running.
    """

    def __init__(self, store, worker: str | None = None,
                 ttl: float = CLAIM_TTL_SECONDS,
                 heartbeat: float = HEARTBEAT_SECONDS,
                 max_attempts: int = MAX_ATTEMPTS,
                 emit: Callable[[dict], None] | None = None):
        self.store = store
        self.worker = worker or worker_id()
        self.ttl = max(30.0, ttl)
        self.heartbeat_interval = max(5.0, min(heartbeat, self.ttl / 4.0))
        self.max_attempts = max(1, max_attempts)
        #: publish (kind, jid, extra) so the UI can show which worker has a job
        self._emit = emit

    # ---- the four primitives -------------------------------------------
    def claim(self, jid: str) -> bool:
        """Take ownership. ``True`` only if this process now owns ``jid``."""
        if self.store is None:
            return True                     # no shared store: single process assumed
        try:
            won = self.store.claim_job(jid, self.worker, ttl=self.ttl)
        except Exception:
            log.exception("claim failed for %s", jid)
            return False
        if won and self._emit:
            self._emit({"kind": "job.claimed", "job": jid, "worker": self.worker})
        return won

    def heartbeat(self, jid: str) -> bool:
        """Refresh liveness. ``False`` means we lost the claim -- stop working."""
        if self.store is None:
            return True
        try:
            return self.store.heartbeat_job(jid, self.worker)
        except Exception:
            log.warning("heartbeat failed for %s", jid, exc_info=True)
            return True                     # transient: do not kill a healthy job

    def release(self, jid: str, status: str | None = None) -> bool:
        """Drop ownership. Other workers are unaffected."""
        if self.store is None:
            return True
        try:
            return self.store.release_job(jid, self.worker, status)
        except Exception:
            log.warning("release failed for %s", jid, exc_info=True)
            return False

    def reap(self) -> dict:
        """Requeue jobs whose owner died, and give up on hopeless ones.

        Without this, a worker that is OOM-killed or rescheduled leaves its job
        ``running`` forever: nothing is writing to it, and the queue looks busy
        to every other worker. This is the single most important failure path in
        the multi-worker design, so it is also the one with a test.
        """
        if self.store is None:
            return {"requeued": [], "interrupted": [], "stale_after": self.ttl}
        try:
            result = self.store.reap_stale_jobs(self.ttl, self.max_attempts)
        except Exception:
            log.exception("reap failed")
            return {"requeued": [], "interrupted": [], "stale_after": self.ttl}
        for jid in result["requeued"]:
            log.warning("requeued %s (owner stopped heartbeating)", jid)
            if self._emit:
                self._emit({"kind": "job.requeued", "job": jid})
        for jid in result["interrupted"]:
            log.error("gave up on %s after %d attempts", jid, self.max_attempts)
            if self._emit:
                self._emit({"kind": "job.interrupted", "job": jid})
        return result

    def stats(self) -> dict:
        if self.store is None:
            return {}
        try:
            return self.store.queue_stats()
        except Exception:
            return {}

    # ---- background heartbeat ------------------------------------------
    def start_heartbeat(self, jid: str, stop: threading.Event) -> threading.Thread:
        """Beat until ``stop`` is set or we discover the claim is gone.

        Deliberately a thread per job rather than one shared loop: jobs are few
        (bounded by SUITE_MAX_JOBS) and this way a single slow call cannot delay
        another job's heartbeat into a spurious steal.
        """
        def loop():
            while not stop.wait(self.heartbeat_interval):
                if not self.heartbeat(jid):
                    log.error("lost claim on %s; another worker may be running it", jid)
                    return
        thread = threading.Thread(target=loop, name=f"claim-{jid[:8]}", daemon=True)
        thread.start()
        return thread


def from_env(store, emit: Callable[[dict], None] | None = None) -> JobQueue:
    """Build a queue using the documented SUITE_* environment variables."""
    return JobQueue(store, emit=emit)
