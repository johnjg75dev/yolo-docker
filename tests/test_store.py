"""The state store: projects (development/finished), artefacts, jobs, transfers.

Runs on SQLite on purpose.  The schema is engine-agnostic; Postgres in
docker-compose gets the same tables.  What these tests protect is the behaviour
the UI depends on: status is a first-class project field, deleting a project
never silently destroys or orphans artefacts, and re-importing from disk is
idempotent (a restart must not duplicate rows).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from optical_modem.suite import db as db_mod
from optical_modem.suite.db import Store, default_url


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store(url=f"sqlite:///{tmp_path / 'suite.db'}")


# --------------------------------------------------------------------------- #
# wiring
# --------------------------------------------------------------------------- #
def test_default_url_prefers_database_url(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db:5432/suite")
    url = default_url(tmp_path)
    # psycopg3 is the driver the image ships
    assert url == "postgresql+psycopg://u:p@db:5432/suite"
    monkeypatch.delenv("DATABASE_URL")
    assert default_url(tmp_path).startswith("sqlite:///")
    assert Path(default_url(tmp_path).split("///", 1)[1]).parent == tmp_path


def test_url_is_redacted(store: Store) -> None:
    assert store.info()["kind"] == "sqlite"
    assert store.redacted_url()


def test_suite_db_zero_disables_the_store(monkeypatch) -> None:
    monkeypatch.setenv("SUITE_DB", "0")
    assert db_mod.get_store() is None


def test_singleton_can_be_reset(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'a.db'}")
    db_mod.reset_store(None)
    first = db_mod.get_store()
    assert first is db_mod.get_store()          # same object
    second = db_mod.reset_store(f"sqlite:///{tmp_path / 'b.db'}")
    assert second is not first
    assert db_mod.get_store() is second
    db_mod.reset_store(None)


# --------------------------------------------------------------------------- #
# projects: development / finished
# --------------------------------------------------------------------------- #
def test_projects_are_created_as_development(store: Store) -> None:
    p = store.create_project("v0.3-hd", title="v0.3 — HD frames", tags=["hd", "finder"])
    assert p["slug"] == "v0.3-hd"
    assert p["status"] == "development"
    assert sorted(p["tags"]) == ["finder", "hd"]        # tags are a set
    assert store.list_projects(status="development")[0]["slug"] == "v0.3-hd"
    assert store.list_projects(status="finished") == []


def test_project_lifecycle_to_finished(store: Store) -> None:
    store.create_project("demo")
    done = store.update_project("demo", status="finished", description="shipped")
    assert done["status"] == "finished"
    assert done["description"] == "shipped"
    assert [p["slug"] for p in store.list_projects(status="finished")] == ["demo"]
    assert store.list_projects(status="development") == []


def test_duplicate_slug_is_refused(store: Store) -> None:
    store.create_project("demo")
    with pytest.raises(ValueError):
        store.create_project("demo")
    with pytest.raises(ValueError):
        store.create_project("   ")            # nothing slug-worthy


def test_unknown_project_is_reported(store: Store) -> None:
    assert store.get_project("nope") is None
    with pytest.raises(KeyError):
        store.update_project("nope", status="finished")
    assert store.delete_project("nope") is False


def test_bad_status_is_rejected(store: Store) -> None:
    store.create_project("demo")
    with pytest.raises(ValueError):
        store.update_project("demo", status="paused")


def test_projects_carry_artefact_counts(store: Store) -> None:
    p = store.create_project("demo")
    store.upsert_dataset("ds1", project_id=p["slug"], images=12)
    store.upsert_dataset("ds2", project_id=p["id"], images=3)
    store.upsert_run("run1", project_id=p["slug"], dataset="ds1", status="ok")
    row = store.get_project("demo")
    assert row["n_datasets"] == 2 and row["n_runs"] == 1


def test_deleting_a_project_detaches_artefacts(store: Store) -> None:
    p = store.create_project("demo")
    store.upsert_dataset("ds1", project_id=p["slug"], images=5)
    store.upsert_run("run1", project_id=p["slug"], status="ok")
    assert store.delete_project("demo") is True
    assert store.get_project("demo") is None
    assert store.get_dataset("ds1")["project_id"] is None       # kept, detached
    assert store.get_run("run1")["project_id"] is None
    assert store.get_dataset("ds1")["images"] == 5              # data intact


def test_deleting_a_project_can_reassign(store: Store) -> None:
    a = store.create_project("a")
    b = store.create_project("b")
    store.upsert_dataset("ds1", project_id=a["slug"])
    store.delete_project("a", reassign_to=b["slug"])
    assert store.get_dataset("ds1")["project_id"] == b["id"]


def test_deleting_a_project_can_drop_datasets(store: Store) -> None:
    p = store.create_project("demo")
    store.upsert_dataset("ds1", project_id=p["slug"])
    store.delete_project("demo", delete_datasets=True)
    assert store.get_dataset("ds1") is None


# --------------------------------------------------------------------------- #
# artefacts
# --------------------------------------------------------------------------- #
def test_dataset_upsert_updates_in_place(store: Store) -> None:
    store.upsert_dataset("ds1", images=4, splits={"train": 4}, source="synth")
    out = store.upsert_dataset("ds1", images=9, status="ready")
    assert out["images"] == 9 and out["source"] == "synth"     # untouched fields stay
    assert len(store.list_datasets()) == 1
    assert store.list_datasets()[0]["splits"] == {"train": 4}


def test_run_upsert_and_metrics(store: Store) -> None:
    store.upsert_run("run1", dataset="ds1", epochs=20, metrics={"box_map50_95": 0.71})
    out = store.upsert_run("run1", status="ok", wall_seconds=123.0)
    assert out["metrics"]["box_map50_95"] == 0.71
    assert out["epochs"] == 20 and out["status"] == "ok"


def test_only_one_model_is_active(store: Store) -> None:
    store.promote_model("m1", run="run1", metrics={"box_map50_95": 0.7})
    store.promote_model("m2", run="run2", metrics={"box_map50_95": 0.8})
    assert store.active_model()["name"] == "m2"      # newest promotion wins
    store.activate_model("m1")
    assert store.active_model()["name"] == "m1"
    actives = [m for m in store.list_models() if m["active"]]
    assert len(actives) == 1
    assert store.delete_model("m1") is True
    assert store.active_model() is None


def test_delete_dataset(store: Store) -> None:
    store.upsert_dataset("ds1", images=1)
    assert store.delete_dataset("ds1") is True
    assert store.delete_dataset("ds1") is False
    assert store.list_datasets() == []


# --------------------------------------------------------------------------- #
# jobs, transfers, events
# --------------------------------------------------------------------------- #
def test_job_history_round_trip(store: Store) -> None:
    base = {"id": "abc123", "kind": "train", "title": "train ds1",
            "status": "queued", "progress": 0.0, "created": 1.0}
    store.record_job(base)
    store.record_job({**base, "status": "running", "progress": 0.4,
                      "message": "epoch 3/10", "started": 2.0})
    done = store.record_job({**base, "status": "finished", "progress": 1.0,
                             "finished": 9.0, "metrics": {"box_map50_95": 0.6}})
    assert done["status"] == "finished"
    assert done["metrics"]["box_map50_95"] == 0.6
    assert len(store.list_jobs()) == 1
    assert store.list_jobs(kind="train")[0]["id"] == "abc123"
    assert store.list_jobs(kind="dataset") == []
    assert store.get_job("abc123")["message"] == "epoch 3/10"


def test_interrupted_jobs_are_marked_on_boot(store: Store) -> None:
    for jid, status in (("a", "running"), ("b", "queued"), ("c", "finished")):
        store.record_job({"id": jid, "kind": "train", "status": status,
                          "created": 1.0})
    n = store.mark_interrupted()
    assert n == 2
    assert store.get_job("a")["status"] == "interrupted"
    assert store.get_job("b")["status"] == "interrupted"
    assert store.get_job("c")["status"] == "finished"       # untouched
    assert store.mark_interrupted() == 0                    # idempotent


def test_transfer_records_are_updated_not_duplicated(store: Store) -> None:
    store.record_transfer({"id": "t1", "bytes": 800, "frames": 4, "complete": False,
                           "received": 1, "sender": "a.txt", "modulation": "rgb8",
                           "ecc": "rs16", "resolution": "1280x720", "finder": "ring"})
    out = store.record_transfer({"id": "t1", "bytes": 800, "frames": 4,
                                 "complete": True, "received": 4,
                                 "sha1": "deadbeef", "folder": "t1"})
    assert out["complete"] is True and out["received"] == 4
    assert out["finder"] == "ring"          # not clobbered with None
    assert len(store.list_transfers()) == 1


def test_events_are_ordered_newest_first(store: Store) -> None:
    store.create_project("a")
    store.create_project("b")
    events = store.list_events(limit=10)
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "project.created"
    assert kinds.count("project.created") == 2
    assert [e["ts"] for e in events] == sorted([e["ts"] for e in events], reverse=True)
    assert store.list_events(kind="project.created")


def test_counts_cover_every_table(store: Store) -> None:
    store.create_project("p")
    store.upsert_dataset("d")
    store.upsert_run("r")
    store.promote_model("m")
    store.record_job({"id": "j", "kind": "k", "status": "queued"})
    store.record_transfer({"id": "t", "bytes": 1, "frames": 1})
    c = store.counts()
    assert c["projects"] == 1 and c["datasets"] == 1 and c["runs"] == 1
    assert c["models"] == 1 and c["jobs"] == 1 and c["transfers"] == 1
    assert c["events"] == 4          # project, dataset, run, model (see list_events)


# --------------------------------------------------------------------------- #
# disk -> database bootstrap
# --------------------------------------------------------------------------- #
class _FakeRegistry:
    """The bits of the on-disk registry ``sync_from_disk`` touches."""

    def __init__(self, datasets, runs, models, active=None):
        self._d, self._r, self._m, self._a = datasets, runs, models, active

    def list_datasets(self):
        return self._d

    def list_runs(self):
        return self._r

    def list_models(self):
        return self._m

    def active(self):
        return self._a


def _registry() -> _FakeRegistry:
    return _FakeRegistry(
        datasets=[{"name": "ds-old", "images": 40, "path": "/data/datasets/ds-old",
                   "yaml": "/data/datasets/ds-old/data.yaml", "splits": {"train": 40},
                   "bytes": 1234}],
        runs=[{"name": "run-old", "dataset": "ds-old", "has_weights": True,
               "metrics": {"box_map50_95": 0.5}, "path": "/data/runs/run-old"}],
        models=[{"name": "run-old", "weights": "/data/models/run-old/best.pt",
                 "card": {"run": "run-old"}, "metrics": {}, "active": True}],
        active={"name": "run-old"},
    )


def test_sync_from_disk_imports_existing_artefacts(store: Store) -> None:
    added = store.sync_from_disk(_registry())
    assert added["datasets"] == 1 and added["runs"] == 1 and added["models"] == 1
    assert store.get_dataset("ds-old")["images"] == 40
    assert store.get_run("run-old")["status"] == "ok"
    assert store.active_model()["name"] == "run-old"
    assert store.get_dataset("ds-old")["source"] == "unknown"


def test_sync_from_disk_is_idempotent(store: Store) -> None:
    store.sync_from_disk(_registry())
    counts = store.counts()
    again = store.sync_from_disk(_registry())            # a restart
    assert again["datasets"] == 0 and again["runs"] == 0 and again["models"] == 0
    assert store.counts()["datasets"] == counts["datasets"]
    assert store.counts()["runs"] == counts["runs"]
    assert store.counts()["models"] == counts["models"]


def test_database_survives_a_new_store_on_the_same_file(tmp_path) -> None:
    url = f"sqlite:///{tmp_path / 'persist.db'}"
    first = Store(url=url)
    p = first.create_project("durable", status="finished")
    first.upsert_dataset("ds", project_id=p["slug"], images=7)
    second = Store(url=url)                               # container restart
    assert second.get_project("durable")["status"] == "finished"
    assert second.get_dataset("ds")["images"] == 7
    assert second.counts()["projects"] == 1
