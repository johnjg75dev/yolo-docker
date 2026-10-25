"""Background jobs for the suite (dataset generation, training, evaluation).

Everything expensive happens in a **subprocess**, not in the web worker:

* the event loop never blocks on a 40-minute training run;
* torch memory is returned to the OS when the run ends (the receiver and the
  capture station only need it transiently);
* a job survives a browser refresh, and its log is a file you can `docker exec`
  into and tail yourself;
* one crash cannot take the web UI down with it.

State lives in ``<root>/jobs/<id>/job.json`` (rewritten atomically on every
change) with the raw stdout+stderr in ``log.txt`` beside it, so the whole history
is inspectable and restart-safe: a job that was running when the container died
is reported as ``interrupted`` rather than pretending to be alive.

    python -m optical_modem.suite.tasks synth --out ...      # a typical argv
"""
from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

try:
    from .logging_config import configure as _configure_logging, get_logger as _get_logger
    _configure_logging()
    log = _get_logger("suite.jobs")
except Exception:  # pragma: no cover
    import logging
    log = logging.getLogger("suite.jobs")

#: states a job can be in
QUEUED, RUNNING, OK, FAILED, CANCELLED, INTERRUPTED = (
    "queued", "running", "ok", "failed", "cancelled", "interrupted")
LIVE = (QUEUED, RUNNING)

#: how many log lines ``tail`` returns by default
TAIL_LINES = 120
#: cap on how much of the log is parsed for progress / returned to the browser
TAIL_BYTES = 400_000


@dataclass
class Job:
    id: str
    kind: str                       # "synth" | "merge" | "train" | "evaluate" | "promote"
    title: str
    argv: list[str]
    status: str = QUEUED
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    returncode: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    progress: float = 0.0
    message: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @property
    def live(self) -> bool:
        return self.status in LIVE

    def as_dict(self, tail: str = "") -> dict:
        d = asdict(self)
        d["live"] = self.live
        d["elapsed"] = round((self.finished or time.time()) - (self.started or self.created), 1)
        d["command"] = " ".join(shlex.quote(a) for a in self.argv)
        if tail:
            d["tail"] = tail
        return d


# --------------------------------------------------------------------------- #
# progress parsing
# --------------------------------------------------------------------------- #
_TQDM = re.compile(r"^\s*(\d+)/(\d+)\s*\[")                     # ultralytics tqdm
_EPOCH = re.compile(r"^\s*(\d+)/(\d+)\s+\S+\s+.*?(?:\s|$)", re.M)
_MAP = re.compile(r"all\s+(\d+)\s+(\d+)\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)")


def parse_progress(kind: str, text: str) -> tuple[float, str, dict]:
    """Best-effort progress from a log tail.

    Ultralytics writes tqdm bars with ``\\r``; the *last* one in the tail is the
    current epoch.  Nothing here is load-bearing — it only drives a bar.
    """
    if not text:
        return 0.0, "", {}
    metrics: dict[str, Any] = {}
    progress, message = 0.0, ""
    if kind == "train":
        lines = re.split(r"[\r\n]+", text)
        epochs = [ln for ln in lines if re.match(r"^\s+\d+/\d+\s", ln)]
        if epochs:
            last = epochs[-1]
            m = re.match(r"^\s+(\d+)/(\d+)\s", last)
            if m:
                done, total = int(m.group(1)), max(1, int(m.group(2)))
                # refine with the tqdm bar on the same line (batch progress within
                # the current epoch).  Ultralytics prints "N/M [...]" twice on
                # that line: the epoch counter first, then the batch counter after
                # the colon.  Grab the *last* "a/b [" occurrence.
                bars = re.findall(r"(\d+)/(\d+)\s*\[", last)
                if len(bars) >= 2:
                    bdone, btotal = int(bars[-1][0]), max(1, int(bars[-1][1]))
                    if btotal > 0 and bdone < btotal:
                        progress = min(1.0, ((done - 1) + bdone / btotal) / total)
                        message = f"epoch {done}/{total} · {bdone}/{btotal}"
                    else:
                        progress = min(1.0, done / total)
                        message = f"epoch {done}/{total}"
                else:
                    progress = min(1.0, done / total)
                    message = f"epoch {done}/{total}"
        for m in _MAP.finditer(text):
            metrics = {"precision": float(m.group(3)), "recall": float(m.group(4)),
                       "map50": float(m.group(5)), "map50_95": float(m.group(6))}
    elif kind in ("synth", "merge"):
        hits = [m for m in _TQDM.finditer(text)]
        if hits:
            done, total = int(hits[-1].group(1)), max(1, int(hits[-1].group(2)))
            progress = min(1.0, done / total)
            message = f"{done}/{total}"
        elif "yaml:" in text or "written" in text:
            progress, message = 1.0, "done"
    elif kind == "evaluate":
        progress, message = (0.5, "evaluating") if "level" in text else (0.0, "")
    elif kind == "promote":
        progress, message = (1.0, "promoted") if "active" in text else (0.0, "")
    return progress, message, metrics


def _failure_reason(rc: int, text: str = "") -> str:
    """A human sentence for a non-zero exit — the UI shows this verbatim.

    ``rc < 0`` means the process died from a signal; SIGKILL on a small box is
    almost always the kernel OOM killer, and saying so saves an hour of
    debugging a log that simply stops mid-epoch.
    """
    if rc == 0:
        return ""
    if rc < 0:
        sig = -rc
        try:
            name = signal.Signals(sig).name
        except ValueError:
            name = f"signal {sig}"
        reason = f"killed by {name}"
        if sig == signal.SIGKILL:
            reason += (" — almost certainly out of memory: lower --batch or --imgsz,"
                       " or give the container more RAM")
        return reason
    last = ""
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if not line or line.startswith("$ ") or set(line) <= set("─━ \t"):
            continue
        last = line[:300]
        break
    return f"exit code {rc}" + (f": {last}" if last else "")


def read_tail(path: Path, max_bytes: int = TAIL_BYTES) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.read(1)               # drop the partial line
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


# --------------------------------------------------------------------------- #
# manager
# --------------------------------------------------------------------------- #
class JobManager:
    """Starts, tracks and cancels subprocess jobs; thread-safe, disk-backed."""

    def __init__(self, root: str | Path, python: str | None = None,
                 env: dict[str, str] | None = None, max_concurrent: int = 2,
                 project_root: str | Path | None = None,
                 store=None, bus=None, run_inprocess: bool = True):
        self.run_inprocess = bool(run_inprocess)
        self.root = Path(root) / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.python = python or sys.executable
        self.env = dict(os.environ if env is None else env)
        self.env.setdefault("PYTHONUNBUFFERED", "1")
        self.max_concurrent = int(max_concurrent)
        self.project_root = Path(project_root or Path(__file__).resolve().parents[2])
        #: optional ``suite.db.Store`` — job history lives there, not only in
        #: ``jobs/<id>/job.json``, so a restart does not lose the record
        self.store = store
        #: optional ``suite.bus`` — publishes job.* events for SSE / other workers
        self.bus = bus
        self._jobs: dict[str, Job] = {}
        self._procs: dict[str, subprocess.Popen] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()
        self._load()

    # ---- disk ------------------------------------------------------------
    def dir(self, jid: str) -> Path:
        return self.root / jid

    def log_path(self, jid: str) -> Path:
        return self.dir(jid) / "log.txt"

    def _save(self, job: Job) -> None:
        path = self.dir(job.id) / "job.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(job.as_dict(), indent=1))
        tmp.replace(path)

    def _load(self) -> None:
        for path in sorted(self.root.glob("*/job.json")):
            try:
                raw = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            known = {f: raw[f] for f in Job.__dataclass_fields__ if f in raw}
            known.pop("live", None)
            known.pop("elapsed", None)
            known.pop("command", None)
            known.pop("tail", None)
            try:
                job = Job(**known)
            except TypeError:            # schema drift: ignore the entry
                continue
            if job.live:
                # the container went away mid-run: be honest about it
                job.status = INTERRUPTED
                job.error = "interrupted by a restart"
                job.finished = job.finished or time.time()
                self._save(job)
            self._jobs[job.id] = job
        # newest first for listing
        self._order = sorted(self._jobs, key=lambda j: self._jobs[j].created, reverse=True)

    # ---- public ----------------------------------------------------------
    def start(self, kind: str, title: str, argv: list[str],
              meta: dict | None = None, cwd: str | Path | None = None) -> Job:
        """Launch ``argv`` as a job.  Raises RuntimeError when too many run."""
        with self._lock:
            live = [j for j in self._jobs.values() if j.live]
            if len(live) >= self.max_concurrent:
                raise RuntimeError(
                    f"{len(live)} jobs already running ({', '.join(j.kind for j in live)}); "
                    "wait for one to finish or cancel it")
            jid = time.strftime("%Y%m%d-%H%M%S") + "-" + kind
            n = 2
            while jid in self._jobs:
                jid = f"{time.strftime('%Y%m%d-%H%M%S')}-{kind}-{n}"
                n += 1
            job = Job(id=jid, kind=kind, title=title, argv=[str(a) for a in argv],
                      meta=meta or {}, status=QUEUED)
            self.dir(jid).mkdir(parents=True, exist_ok=True)
            self.log_path(jid).write_text(
                f"$ {' '.join(shlex.quote(a) for a in job.argv)}\n")
            self._jobs[jid] = job
            self._order.insert(0, jid)
            self._save(job)
        self._emit("job.queued", job)
        log.info("job queued id=%s kind=%s title=%s", jid, kind, title)
        if self.run_inprocess:
            self._spawn(job, cwd)
        return job

    def _spawn(self, job: Job, cwd: str | Path | None = None) -> None:
        log = open(self.log_path(job.id), "ab", buffering=0)
        try:
            proc = subprocess.Popen(
                job.argv, cwd=str(cwd or self.project_root), env=self.env,
                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                start_new_session=True,           # own process group -> real cancel
            )
        except OSError as exc:
            log.write(f"\n[suite] could not start: {exc}\n".encode())
            log.close()
            with self._lock:
                job.status, job.error = FAILED, str(exc)
                job.finished, job.progress = time.time(), 1.0
                self._save(job)
            return
        with self._lock:
            job.status, job.started = RUNNING, time.time()
            self._procs[job.id] = proc
            self._save(job)
        self._emit("job.started", job)
        log.info("job started id=%s pid=%s", job.id, proc.pid)
        t = threading.Thread(target=self._monitor, args=(job, proc, log),
                             name=f"job-{job.id}", daemon=True)
        self._threads[job.id] = t
        t.start()

    def _monitor(self, job: Job, proc: subprocess.Popen, log) -> None:
        try:
            rc = proc.wait()
        finally:
            try:
                log.close()
            except OSError:
                pass
        with self._lock:
            self._procs.pop(job.id, None)
            job.returncode = rc
            job.finished = time.time()
            if job.status == CANCELLED:
                pass
            elif rc == 0:
                job.status, job.progress = OK, 1.0
            else:
                job.status = FAILED
                job.error = _failure_reason(rc, read_tail(self.log_path(job.id)))
                job.progress = 0.0
            text = read_tail(self.log_path(job.id))
            prog, msg, metrics = parse_progress(job.kind, text)
            if job.status == OK:
                job.progress = 1.0
            elif prog:
                job.progress = prog
            job.message = msg if job.status == OK else (job.error or msg)
            job.metrics.update(metrics)
            self._save(job)
        self._emit("job.finished" if job.status == OK else "job.failed", job)
        log.info("job finished id=%s status=%s metrics=%s", job.id, job.status, job.metrics)

    def get(self, jid: str) -> Job | None:
        with self._lock:
            return self._jobs.get(jid)

    def list(self, kind: str | None = None, limit: int = 50) -> list[Job]:
        with self._lock:
            out = [self._jobs[j] for j in self._order if j in self._jobs]
        if kind:
            out = [j for j in out if j.kind == kind]
        return out[:limit]

    def running(self, kind: str | None = None) -> Job | None:
        for job in self.list(kind=kind):
            if job.live:
                return job
        return None

    def tail(self, jid: str, lines: int = TAIL_LINES) -> str:
        text = read_tail(self.log_path(jid))
        if not text:
            return ""
        rows = text.splitlines()
        return "\n".join(rows[-lines:])

    def snapshot(self, jid: str, lines: int = TAIL_LINES) -> dict | None:
        job = self.get(jid)
        if job is None:
            return None
        text = read_tail(self.log_path(jid))
        prog, msg, metrics = parse_progress(job.kind, text)
        d = job.as_dict(tail="\n".join(text.splitlines()[-lines:]))
        if job.live:
            d["progress"] = max(job.progress, prog)
            d["message"] = msg or job.message
            d["metrics"] = {**job.metrics, **metrics}
            if msg and getattr(self, "_last_log_msg", {}).get(jid) != msg:
                self.setdefault("_last_log_msg", {})[jid] = msg
                log.info("job %s: %s %s", jid, msg, metrics if metrics else "")
        return d

    def cancel(self, jid: str) -> Job | None:
        job = self.get(jid)
        if job is None:
            return None
        proc = self._procs.get(jid)
        with self._lock:
            job.status = CANCELLED
            job.finished = time.time()
            job.message = "cancelled"
            self._save(job)
        self._emit("job.cancelled", job)
        log.info("job cancelled id=%s", jid)
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except OSError:
                proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:      # pragma: no cover
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except OSError:
                    proc.kill()
        return job

    # ---- events / persistence -------------------------------------------
    def _emit(self, kind: str, job: Job) -> None:
        """Publish a job event and mirror the record into the database.

        Both are best-effort: a broken Redis or a locked database must never make
        a training run fail.
        """
        snapshot = job.as_dict()
        if self.bus is not None:
            try:
                from .bus import job_event

                self.bus.publish(job_event(kind, snapshot))
            except Exception:
                pass
        if self.store is not None:
            try:
                self.store.record_job(snapshot)
                self._settle_artefact(kind, snapshot)
            except Exception:
                pass

    def _settle_artefact(self, kind: str, snapshot: dict) -> None:
        """Finish what the endpoint started: a dataset stops being "building".

        The endpoint records ``status=building`` when it queues the job; the job
        itself knows when it worked.  Doing it here means a dataset never stays
        half-built because the browser was closed.
        """
        if kind not in ("job.finished", "job.failed"):
            return
        meta = snapshot.get("meta") or {}
        name = meta.get("dataset")
        if not name:
            return
        ok = snapshot.get("status") == "ok"
        self.store.upsert_dataset(name, status="ready" if ok else "failed")
        if ok:
            self.store.log_event("dataset.ready", name,
                                 detail={"job": snapshot.get("id")})

    def heartbeat(self, jid: str) -> None:
        """Refresh the liveness key for a running job (used by the poller)."""
        if self.bus is None:
            return
        try:
            self.bus.heartbeat(f"job:{jid}", 60.0)
        except Exception:
            pass

    def adopt_from_store(self) -> dict:
        """Import job records that only exist in the database.

        A container restart keeps the database and the volume, but the in-memory
        registry starts empty and a job id from before the restart would 404 in
        the UI even though its log is still on disk.  This rehydrates those
        entries (as ``interrupted`` when they were live).
        """
        added = 0
        if self.store is None:
            return {"adopted": 0}
        try:
            rows = self.store.list_jobs(limit=200)
        except Exception:
            return {"adopted": 0}
        with self._lock:
            for row in rows:
                jid = row["id"]
                if jid in self._jobs:
                    continue
                known = {f: row[f] for f in Job.__dataclass_fields__ if f in row}
                known.pop("id", None)
                try:
                    job = Job(id=jid, **{**known, "argv": known.get("argv") or []})
                except TypeError:
                    continue
                if job.live:
                    job.status = INTERRUPTED
                    job.error = job.error or "interrupted by a restart"
                    job.finished = job.finished or time.time()
                self._jobs[jid] = job
                self._order.append(jid)
                added += 1
            self._order.sort(key=lambda j: self._jobs[j].created, reverse=True)
        return {"adopted": added}

    def delete(self, jid: str) -> bool:
        with self._lock:
            job = self._jobs.pop(jid, None)
            if jid in self._order:
                self._order.remove(jid)
            proc = self._procs.pop(jid, None)
            thread = self._threads.pop(jid, None)
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except OSError:
                try:
                    proc.kill()
                except OSError:
                    pass
        d = self.dir(jid)
        try:
            import shutil
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass
        if self.store is not None:
            try:
                self.store.delete_job(jid)
            except Exception:
                pass
        log.info("job deleted id=%s", jid)
        return job is not None

    def log_file(self, jid: str) -> Path:
        return self.log_path(jid)
