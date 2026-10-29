"""Redis-backed messaging for jobs — with a working fallback.

Two problems this solves, both real on a single-container deployment:

1. **job → browser**: the UI polls ``/api/jobs/<id>`` every 1.2 s.  With Redis
   the worker publishes events and the browser can hold one Server-Sent-Events
   connection instead (``GET /api/events``).  Polling stays available, so a
   browser without ``EventSource`` (or a Redis-less deployment) is unaffected.
2. **job → job**: "only one training run at a time" and "this dataset is being
   built" are *locks*, and a lock must expire if the process dies.  Redis keys
   with a TTL do that; a Python dict does not.

Design rules:

* Redis is **optional**.  ``REDIS_URL`` unset (or ``SUITE_REDIS=0``) selects the
  in-process bus, which implements the same API.  Single worker = single
  process, so the fallback is genuinely equivalent for the shipped topology —
  Redis earns its place when you add a second worker, a separate training
  container, or want job events to survive a restart.
* Publishing must never raise into a request handler: every call is wrapped and
  degrades to the local bus.
* The local bus still drives SSE, so the feature works in development.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import deque
from typing import Any, Callable

try:
    from .logging_config import configure as _configure_logging, get_logger as _get_logger
    _configure_logging()
    log = _get_logger("suite.bus")
except Exception:  # pragma: no cover
    import logging
    log = logging.getLogger("suite.bus")

try:                                     # pragma: no cover - exercised via tests
    import redis as _redis
except Exception:                        # pragma: no cover
    _redis = None

CHANNEL = "optical-suite:jobs"
EVENTS_KEY = "optical-suite:events"       # capped ring buffer of recent events
LOCK_PREFIX = "optical-suite:lock:"
HEARTBEAT_PREFIX = "optical-suite:hb:"


class LocalBus:
    """In-process event bus + expiring locks (the no-Redis fallback)."""

    kind = "memory"

    def __init__(self, keep: int = 500):
        self._subs: list[Callable[[dict], None]] = []
        self._lock = threading.RLock()
        self._recent: deque[dict] = deque(maxlen=keep)
        self._locks: dict[str, float] = {}
        self._beats: dict[str, float] = {}

    # ---- events ----------------------------------------------------------
    def publish(self, event: dict) -> None:
        event = {"ts": time.time(), **event}
        log.debug("bus publish kind=%s subject=%s", event.get("kind"), event.get("subject"))
        with self._lock:
            self._recent.append(event)
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(event)
            except Exception:                # a dead subscriber must not break a job
                pass

    def subscribe(self, fn: Callable[[dict], None]) -> Callable[[], None]:
        with self._lock:
            self._subs.append(fn)
        return lambda: self.unsubscribe(fn)

    def unsubscribe(self, fn: Callable[[dict], None]) -> None:
        with self._lock:
            if fn in self._subs:
                self._subs.remove(fn)

    def recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            return list(self._recent)[-limit:]

    # ---- locks -----------------------------------------------------------
    def acquire(self, name: str, ttl: float = 3600.0) -> bool:
        now = time.time()
        with self._lock:
            until = self._locks.get(name, 0.0)
            if until > now:
                return False
            self._locks[name] = now + ttl
            return True

    def release(self, name: str) -> None:
        with self._lock:
            self._locks.pop(name, None)

    def held(self, name: str) -> bool:
        with self._lock:
            return self._locks.get(name, 0.0) > time.time()

    # ---- fleet -----------------------------------------------------------
    def workers(self) -> list[str]:
        """Worker ids that have advertised themselves.

        The in-process bus has nowhere shared to keep these, so it reports
        nothing at all -- the fleet view then falls back to live job ownership
        (``by_owner``), which misses idle workers. RedisBus overrides this.
        """
        return []

    # ---- heartbeats ------------------------------------------------------
    def heartbeat(self, key: str, ttl: float = 30.0) -> None:
        """Mark a job as alive (expires, so a crashed worker stops looking live)."""
        with self._lock:
            self._beats[key] = time.time() + ttl

    def alive(self, key: str) -> bool:
        with self._lock:
            return self._beats.get(key, 0.0) > time.time()

    def info(self) -> dict:
        with self._lock:
            return {"kind": self.kind, "subscribers": len(self._subs),
                    "recent": len(self._recent)}

    def close(self) -> None:
        pass


class RedisBus(LocalBus):
    """Redis pub/sub + TTL locks.  Falls back to the local bus on any error."""

    kind = "redis"

    def __init__(self, url: str, keep: int = 500, connect: bool = True):
        super().__init__(keep=keep)
        self.url = url
        #: stamped on every event so the pub/sub echo of our own publish is not
        #: delivered twice to local subscribers
        self._origin = uuid.uuid4().hex
        self._client = _redis.Redis.from_url(url, decode_responses=True,
                                             socket_timeout=2.0,
                                             socket_connect_timeout=2.0)
        self._offline = not connect               # no listener thread when offline
        if connect:
            self._client.ping()                  # fail fast: caller falls back
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ensure_listener()

    # ---- events ----------------------------------------------------------
    def _ensure_listener(self) -> None:
        if getattr(self, "_offline", False):
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._listen, name="redis-events",
                                        daemon=True)
        self._thread.start()

    def _listen(self) -> None:
        while not self._stop.is_set():
            try:
                pubsub = self._client.pubsub(ignore_subscribe_messages=True)
                pubsub.subscribe(CHANNEL)
                for message in pubsub.listen():
                    if self._stop.is_set():
                        break
                    if message.get("type") != "message":
                        continue
                    try:
                        event = json.loads(message["data"])
                    except (TypeError, ValueError):
                        continue
                    if event.get("origin") == self._origin:
                        continue                     # our own publish: already local
                    super().publish(event)          # fan out to local subscribers
            except Exception:                        # redis restarted, network blip
                time.sleep(1.0)

    def publish(self, event: dict) -> None:
        event = {"ts": time.time(), "origin": self._origin, **event}
        super().publish(event)                       # local subscribers first
        try:
            payload = json.dumps(event, default=str)
            pipe = self._client.pipeline()
            pipe.publish(CHANNEL, payload)
            pipe.lpush(EVENTS_KEY, payload)
            pipe.ltrim(EVENTS_KEY, 0, max(0, self._kept - 1))
            pipe.execute()
        except Exception as e:
            log.warning("redis publish failed: %s", e)

    @property
    def _kept(self) -> int:
        return self._recent.maxlen or 200

    def recent(self, limit: int = 50) -> list[dict]:
        try:
            raw = self._client.lrange(EVENTS_KEY, 0, max(0, limit - 1))
            return [json.loads(r) for r in raw][::-1]
        except Exception:
            return super().recent(limit)

    # ---- locks -----------------------------------------------------------
    def acquire(self, name: str, ttl: float = 3600.0) -> bool:
        try:
            return bool(self._client.set(LOCK_PREFIX + name, "1", nx=True,
                                         ex=max(1, int(ttl))))
        except Exception:
            return super().acquire(name, ttl)

    def release(self, name: str) -> None:
        try:
            self._client.delete(LOCK_PREFIX + name)
        except Exception:
            pass
        super().release(name)

    def held(self, name: str) -> bool:
        try:
            return bool(self._client.exists(LOCK_PREFIX + name))
        except Exception:
            return super().held(name)

    def workers(self) -> list[str]:
        """Worker ids that advertised themselves and have not expired.

        Keys are written with a TTL by :meth:`heartbeat`, so a worker that is
        killed simply disappears -- no reaper needed for the fleet view.
        """
        try:
            names = [key.split(":", 1)[1] for key in
                     self._client.scan_iter(match=f"{HEARTBEAT_PREFIX}worker:*", count=200)]
        except Exception:
            return []
        return [n[len("worker:"):] for n in names if n.startswith("worker:")]

    def heartbeat(self, key: str, ttl: float = 30.0) -> None:
        """Mark a job as alive; a stale key means the process died."""
        super().heartbeat(key, ttl)
        try:
            self._client.set(HEARTBEAT_PREFIX + key, str(time.time()),
                             ex=max(1, int(ttl)))
        except Exception:
            pass

    def alive(self, key: str) -> bool:
        try:
            return bool(self._client.exists(HEARTBEAT_PREFIX + key))
        except Exception:
            return super().alive(key)

    def info(self) -> dict:
        out = super().info()
        out["url"] = self._redact()
        try:
            info = self._client.info("server") or {}
            out["redis"] = (info.get("redis_version") if isinstance(info, dict)
                            else None)
        except Exception:
            out["redis"] = None
        return out

    def _redact(self) -> str:
        url = self.url
        if "@" in url:
            head, tail = url.split("@", 1)
            if ":" in head.split("//")[-1]:
                return head.rsplit(":", 1)[0] + ":***@" + tail
        return url

    def close(self) -> None:
        self._stop.set()
        try:
            self._client.close()
        except Exception:
            pass


_BUS: LocalBus | None = None


def get_bus() -> LocalBus:
    """Process-wide bus: Redis when configured and reachable, else in-memory."""
    global _BUS
    if _BUS is not None:
        return _BUS
    if os.environ.get("SUITE_REDIS", "1") in ("0", "false", "off"):
        _BUS = LocalBus()
        return _BUS
    url = os.environ.get("REDIS_URL", "").strip()
    if url and _redis is not None:
        try:
            _BUS = RedisBus(url)
            return _BUS
        except Exception:
            _BUS = LocalBus()
            _BUS.publish({"kind": "bus.fallback", "reason": "redis unreachable",
                          "url": url})
            return _BUS
    _BUS = LocalBus()
    return _BUS


def reset_bus(url: str | None = None) -> LocalBus:
    """Test hook: swap the process-wide bus (``url="memory"`` for the fallback)."""
    global _BUS
    if _BUS is not None:
        _BUS.close()
    if not url or url == "memory":
        _BUS = LocalBus()
    else:
        os.environ["REDIS_URL"] = url
        _BUS = RedisBus(url)
    return _BUS


def job_event(kind: str, job: dict, **extra) -> dict:
    """A compact job event — what the browser and the log actually need."""
    payload: dict[str, Any] = {
        "kind": kind,
        "subject": str(job.get("id") or ""),
        "ts": time.time(),
        "job": {k: job.get(k) for k in ("id", "kind", "title", "status", "progress",
                                        "message", "elapsed", "live")},
    }
    if job.get("error"):
        payload["job"]["error"] = job["error"]
    payload.update(extra)
    return payload
