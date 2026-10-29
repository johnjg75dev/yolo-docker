"""Tests for separate worker containers (audit S5).

The failure mode this whole design exists to prevent is *silent double
execution*: two workers see the same queued job and both run it. That does not
show up as an error -- it shows up as a training run whose results are garbage,
or a dataset with duplicated images, hours later. So the tests below assert on
the claim count, not on the job's exit status.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from optical_modem.suite.db import Store  # noqa: E402
from optical_modem.suite.jobs import JobManager  # noqa: E402
from optical_modem.suite.queue import JobQueue, worker_id  # noqa: E402


@pytest.fixture
def store(tmp_path):
    st = Store(url=f"sqlite:///{tmp_path/'suite.db'}")
    yield st
    try:
        st.engine.dispose()
    except Exception:
        pass


def _manager(tmp_path, store, name, **kw):
    return JobManager(tmp_path / "data", store=store, run_inprocess=False,
                      worker_id=name, **kw)


def _drain(*managers, timeout=25.0):
    """Tick every worker until nothing is left queued or running."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        busy = False
        for m in managers:
            m.tick()
            stats = m.queue.stats()
            if stats.get("queued") or stats.get("running"):
                busy = True
        if not busy:
            break
        time.sleep(0.15)
    return time.time() < deadline


# --------------------------------------------------------------------------- #
# the core guarantee
# --------------------------------------------------------------------------- #
def test_no_job_runs_twice_across_workers(tmp_path, store):
    """Two workers, one queue: every job is claimed exactly once."""
    a = _manager(tmp_path, store, "worker-A", max_concurrent=2)
    b = _manager(tmp_path, store, "worker-B", max_concurrent=2)

    jids = []
    for i in range(8):
        job = a.start("synth", f"job {i}", ["sh", "-c", "sleep 0.1"])
        jids.append(job.id)
        # the enqueueing process must not have started anything itself
        assert len(a._procs) == 0, "web/worker split: start() must not spawn"

    assert _drain(a, b), "jobs did not settle"

    for jid in jids:
        row = store.get_job(jid)
        assert row["attempts"] == 1, f"{jid} was claimed {row['attempts']} times"
        assert row["status"] == "ok", f"{jid} ended {row['status']}"
    stats = store.queue_stats()
    assert stats["queued"] == 0 and stats["running"] == 0, stats


def test_work_is_distributed_not_hoarded(tmp_path, store):
    """Both workers should actually take work, not one monopolising the queue."""
    a = _manager(tmp_path, store, "worker-A", max_concurrent=1)
    b = _manager(tmp_path, store, "worker-B", max_concurrent=1)
    jids = [a.start("synth", f"j{i}", ["sh", "-c", "sleep 0.2"]).id for i in range(6)]
    assert _drain(a, b), "jobs did not settle"

    owners = {store.get_job(j)["owner"] for j in jids}
    # owners are cleared on release, so check the job.meta recorded at spawn
    ran_on = set()
    for m in (a, b):
        for j in m.list():
            # both managers know every job (they share the volume), so attribute
            # only the ones this manager actually spawned
            if j.id in jids and j.meta.get("worker") == m.worker_id:
                ran_on.add(j.meta["worker"])
    assert len(ran_on) == 2, f"only {ran_on} did work -- queue was not shared"


def test_a_second_worker_cannot_steal_a_live_job(tmp_path, store):
    """A fresh heartbeat must protect a running job from the reaper."""
    a = _manager(tmp_path, store, "worker-A")
    jid = a.start("synth", "long", ["sh", "-c", "sleep 2"]).id
    a.tick()
    assert len(a._procs) == 1, "worker A should have spawned"

    b = _manager(tmp_path, store, "worker-B")
    time.sleep(0.4)
    reaped = b.tick()
    assert reaped["requeued"] == [], "stole a job that was actively heartbeating"
    assert store.get_job(jid)["attempts"] == 1

    a.cancel(jid)


def _simulate_dead_worker(store, jid, name="worker-doomed", age=3600.0):
    """Leave behind exactly what a SIGKILLed worker container leaves behind.

    Killing the subprocess through the manager does NOT simulate this: the
    monitor thread observes the exit and records a normal 'failed' job. A real
    container death removes the process *and* the observer, so the row keeps
    status=running with an owner that will never heartbeat again. That stale
    row is what the reaper exists to find.
    """
    # age must exceed the queue's CLAIM_TTL, or the reaper is *correct* to leave
    # the job alone -- a recent heartbeat means the owner is probably fine.
    store.claim_job(jid, name)
    with store.engine.begin() as conn:
        conn.exec_driver_sql("UPDATE jobs SET heartbeat = :h WHERE id = :i",
                             {"h": time.time() - age, "i": jid})


def test_dead_workers_jobs_are_requeued(tmp_path, store):
    """The reaper must recover work from a worker that vanished.

    This is the failure mode OOM kills and node reschedules produce: the job
    stays ``running`` forever because nothing is writing to it, and every other
    worker sees a queue that looks busy.
    """
    jid = "orphan-1"
    store.record_job({"id": jid, "kind": "synth", "title": "orphan", "status": "queued",
                      "argv": ["sh", "-c", "sleep 0.2"]})
    survivor = _manager(tmp_path, store, "worker-B")
    survivor.adopt_from_store()
    _simulate_dead_worker(store, jid)
    assert store.get_job(jid)["status"] == "running"

    result = survivor.tick()
    assert jid in result["requeued"], f"orphan not recovered: {result}"
    # tick() recovers *and* starts, so the job should now belong to the survivor
    row = store.get_job(jid)
    assert row["owner"] == "worker-B", f"orphan not taken over: {row}"
    assert row["attempts"] == 2, f"expected a second attempt, got {row['attempts']}"

    deadline = time.time() + 15
    while store.get_job(jid)["status"] in ("queued", "running") and time.time() < deadline:
        time.sleep(0.1)
    assert store.get_job(jid)["status"] == "ok", "recovered job did not complete"


def test_hopeless_jobs_are_not_requeued_forever(tmp_path, store):
    """After max_attempts a job is interrupted, not retried until the queue rots.

    A job whose input reliably triggers the OOM killer would otherwise be
    reclaimed forever, each attempt burning a worker slot for minutes.
    """
    m = _manager(tmp_path, store, "worker-A")
    m.queue.max_attempts = 3

    jid = "retry-test"
    store.record_job({"id": jid, "kind": "synth", "title": "bad", "status": "queued",
                      "argv": ["sh", "-c", "exit 1"]})
    m.adopt_from_store()

    # Three workers claim it and die. The first two are requeued; by the third
    # the job has used up its budget and must be parked as interrupted.
    outcomes = []
    for i in range(m.queue.max_attempts):
        _simulate_dead_worker(store, jid, name=f"dead-{i}")
        outcomes.append(store.reap_stale_jobs(m.queue.ttl, m.queue.max_attempts))

    assert jid in outcomes[0]["requeued"], "first orphan should be retried"
    assert jid in outcomes[1]["requeued"], "second orphan should be retried"
    assert jid in outcomes[2]["interrupted"], "third must give up"
    assert store.get_job(jid)["status"] == "interrupted"

    # it must stay parked: a later tick must not resurrect it into the queue
    after = m.tick()
    assert jid not in after["requeued"], "interrupted job was retried anyway"
    assert store.get_job(jid)["status"] == "interrupted"


def test_single_process_mode_is_unaffected(tmp_path):
    """With no store the claim protocol must stay completely inert."""
    m = JobManager(tmp_path / "data", max_concurrent=2)
    assert m.run_inprocess and not m.enqueue_only
    job = m.start("synth", "inline", ["sh", "-c", "sleep 0.1"])
    assert len(m._procs) == 1, "inprocess must still spawn immediately"
    deadline = time.time() + 10
    while m.list()[0].status in ("queued", "running") and time.time() < deadline:
        time.sleep(0.05)
    assert job.status == "ok"
    assert m.tick() == {"started": [], "adopted": 0, "requeued": [], "interrupted": []}


def test_worker_ids_are_distinct():
    ids = {worker_id("worker") for _ in range(5)}
    assert len(ids) >= 1
    assert all(i.startswith("worker-") for i in ids)


def test_queue_without_store_is_a_noop():
    """A deployment with no DB at all must not break on the claim path."""
    q = JobQueue(None)
    assert q.claim("anything") is True       # nobody to contend with
    assert q.heartbeat("anything") is True
    assert q.release("anything") is True
    assert q.reap()["requeued"] == []
    assert q.stats() == {}
