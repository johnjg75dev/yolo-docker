"""The message bus: job events to every browser, and locks that actually hold.

Redis is worth having here for three concrete things, all covered below:
  * fan-out — one training worker, N browsers (SSE) plus the CLI, all seeing the
    same event stream;
  * ``SET NX EX`` locks — "only one training job at a time" survives a process
    restart and expires if the holder dies;
  * a capped event ring — a page opened ten minutes after a job ended can still
    show what happened.

``fakeredis`` speaks the real protocol over a real socket, so RedisBus is
exercised over TCP, not against a mock.
"""
from __future__ import annotations

import threading
import time

import pytest

from optical_modem.suite.bus import (CHANNEL, EVENTS_KEY, LocalBus, RedisBus,
                                     get_bus, job_event, reset_bus)


# --------------------------------------------------------------------------- #
# in-process bus (the no-Redis fallback)
# --------------------------------------------------------------------------- #
def test_local_bus_delivers_to_every_subscriber_once():
    bus = LocalBus()
    got_a, got_b = [], []
    bus.subscribe(got_a.append)
    bus.subscribe(got_b.append)
    bus.publish({"kind": "job.started", "id": "j1"})
    assert [e["kind"] for e in got_a] == ["job.started"]
    assert [e["kind"] for e in got_b] == ["job.started"]
    assert got_a[0]["ts"] > 0


def test_local_bus_unsubscribe_stops_delivery():
    bus = LocalBus()
    got = []
    fn = got.append
    bus.subscribe(fn)
    bus.publish({"kind": "a"})
    bus.unsubscribe(fn)
    bus.publish({"kind": "b"})
    assert [e["kind"] for e in got] == ["a"]
    bus.unsubscribe(fn)                       # idempotent


def test_local_bus_ring_is_capped_and_newest_last():
    bus = LocalBus(keep=3)
    for i in range(10):
        bus.publish({"kind": "tick", "i": i})
    recent = bus.recent(limit=10)
    assert [e["i"] for e in recent] == [7, 8, 9]
    assert len(bus.recent(limit=2)) == 2


def test_local_bus_locks_are_exclusive_and_expire():
    bus = LocalBus()
    assert bus.acquire("train", ttl=0.2) is True
    assert bus.acquire("train", ttl=0.2) is False     # already held
    assert bus.held("train") is True
    assert bus.acquire("dataset", ttl=5) is True      # different name, fine
    time.sleep(0.3)
    assert bus.held("train") is False                 # died with its holder
    assert bus.acquire("train", ttl=5) is True        # and can be taken over
    bus.release("train")
    assert bus.held("train") is False


def test_local_bus_info_is_serialisable():
    info = LocalBus().info()
    assert info["kind"] == "memory"
    assert set(info) == {"kind", "subscribers", "recent"}
    import json
    json.dumps(info)                              # goes straight into /healthz


def test_get_bus_falls_back_without_redis(monkeypatch):
    monkeypatch.setenv("SUITE_REDIS", "0")
    reset_bus(None)
    bus = get_bus()
    assert isinstance(bus, LocalBus)
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")   # nothing listens
    reset_bus(None)
    assert isinstance(get_bus(), LocalBus)                     # degrades, no crash
    reset_bus(None)


def test_job_event_shape():
    ev = job_event("job.finished", {"id": "j1", "kind": "train", "status": "finished"},
                   project="demo")
    assert ev["kind"] == "job.finished"
    assert ev["job"]["id"] == "j1" and ev["project"] == "demo"
    assert ev["ts"] > 0 and "subject" in ev


# --------------------------------------------------------------------------- #
# Redis bus, over a real socket (fakeredis server)
# --------------------------------------------------------------------------- #
@pytest.fixture()
def redis_server():
    """A real TCP server speaking the Redis protocol (fakeredis, no daemon)."""
    fakeredis = pytest.importorskip("fakeredis")
    srv = fakeredis.TcpFakeServer(("127.0.0.1", 0), server_type="redis")
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    host, port = srv.server_address[:2]
    yield f"redis://{host}:{port}/0"
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=2)


def _wait(fn, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.02)
    return False


def test_redis_bus_publishes_and_receives_exactly_once(redis_server):
    tx = RedisBus(redis_server)
    rx = RedisBus(redis_server)
    got = []
    rx.subscribe(got.append)
    assert _wait(lambda: rx.info().get("subscribers") == 1)
    tx.publish({"kind": "job.started", "id": "j1"})
    assert _wait(lambda: len(got) >= 1)
    time.sleep(0.15)
    # the publisher also sees its own event (loopback) but must not duplicate it
    assert [e["kind"] for e in got] == ["job.started"]
    assert rx.recent(limit=5)[-1]["id"] == "j1"
    tx.close()
    rx.close()


def test_redis_bus_reaches_a_second_process_like_subscriber(redis_server):
    """Two independent buses == worker process + web process."""
    worker = RedisBus(redis_server)
    web = RedisBus(redis_server)
    seen_web = []
    web.subscribe(seen_web.append)
    assert _wait(lambda: web.info().get("subscribers") == 1)
    worker.publish(job_event("job.finished", {"id": "j9", "kind": "train"}))
    assert _wait(lambda: seen_web)
    assert seen_web[0]["job"]["id"] == "j9"
    worker.close()
    web.close()


def test_redis_bus_locks(redis_server):
    a = RedisBus(redis_server)
    b = RedisBus(redis_server)                     # a different process
    assert a.acquire("train", ttl=5) is True
    assert b.acquire("train", ttl=5) is False      # refused across processes
    assert b.held("train") is True
    a.release("train")
    assert b.acquire("train", ttl=0.3) is True
    assert _wait(lambda: not b.held("train"), timeout=2.0)   # TTL expires
    a.close()
    b.close()


def test_redis_bus_heartbeat(redis_server):
    bus = RedisBus(redis_server)
    bus.heartbeat("job:j1", ttl=0.3)
    assert bus.alive("job:j1") is True
    assert _wait(lambda: not bus.alive("job:j1"), timeout=2.0)
    bus.close()


def test_redis_bus_event_list_is_capped(redis_server):
    bus = RedisBus(redis_server, keep=5)
    for i in range(20):
        bus.publish({"kind": "tick", "i": i})
    recent = bus.recent(limit=50)
    assert len(recent) <= 5
    assert recent[-1]["i"] == 19
    bus.close()


def test_redis_bus_url_is_redacted(redis_server):
    bus = RedisBus("redis://user:s3cret@127.0.0.1:6399/0", connect=False)
    assert "s3cret" not in bus.info()["url"]
    assert "*" in bus.info()["url"]


def test_bus_keys_are_namespaced():
    assert CHANNEL.startswith("optical-suite:")
    assert EVENTS_KEY.startswith("optical-suite:")


def test_redis_bus_survives_a_subscriber_that_raises(redis_server):
    bus = RedisBus(redis_server)

    def boom(_ev):
        raise RuntimeError("browser went away")

    good = []
    bus.subscribe(boom)
    bus.subscribe(good.append)
    assert _wait(lambda: bus.info().get("subscribers") == 2)
    bus.publish({"kind": "job.failed", "id": "j2"})
    assert _wait(lambda: good)              # the healthy subscriber still got it
    bus.close()
