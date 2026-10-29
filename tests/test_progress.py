"""The guided flow: stage ordering, one truthful next action, and the visuals.

`/api/suite/progress` is what the dashboard rail, the wizard, the nav ticks and
the CLI all read, so the properties that matter are:

* the answer is derived from state (not from "did you visit the page"),
* exactly one stage is ``current`` and everything after it is ``blocked``,
* the ``next`` action is runnable — it names a real endpoint and a payload,
* and it advances when the artefacts it asks for appear.

The visual endpoints (finder SVG, palettes, training curves) are checked for the
same reason: a broken thumbnail or an empty chart is a silent guide failure.
"""
from __future__ import annotations

import csv
import importlib
import sys
from pathlib import Path

import pytest

from optical_modem.suite import progress as pg


def _env(**kw):
    base = {"torch": "2.14.1+cpu", "ultralytics": "8.4.183", "cpu_count": 4,
            "disk_free_bytes": 50e9, "python": "3.12.0", "opencv": "4.11.0"}
    base.update(kw)
    return base


def _run(**kw):
    base = {"name": "run-1", "has_weights": True, "metrics": {"box_map50_95": 0.61}}
    base.update(kw)
    return base


def _build(**kw):
    args = dict(env=_env(), datasets=[], runs=[], models=[], active=None,
                projects=[], transfers=[], sessions=[], jobs=[], db_enabled=True)
    args.update(kw)
    return pg.build(**args)


# --------------------------------------------------------------------------- #
# ordering and the single "you are here"
# --------------------------------------------------------------------------- #
def test_empty_box_starts_at_the_environment_or_the_dataset() -> None:
    out = _build()
    assert [s["id"] for s in out["stages"]] == ["env", "project", "dataset",
                                                "train", "verify", "send"]
    assert out["stages"][0]["done"] is True            # torch is installed
    assert out["next"]["id"] in ("project", "dataset")  # first unfinished
    assert out["percent"] == round(100 / 6)
    assert out["complete"] is False


def test_exactly_one_stage_is_current_and_the_rest_are_blocked() -> None:
    out = _build(datasets=[{"name": "d1", "images": 40}])
    current = [s for s in out["stages"] if s["current"]]
    assert len(current) == 1
    idx = out["stages"].index(current[0])
    for later in out["stages"][idx + 1:]:
        assert later["blocked"] is True
        assert later["done"] is False or later["blocked"] is True
    for earlier in out["stages"][:idx]:
        assert earlier["blocked"] is False


def test_a_trained_box_is_told_to_verify_not_to_train_again() -> None:
    out = _build(env=_env(), datasets=[{"name": "d", "images": 200}],
                 projects=[{"slug": "p", "status": "development"}],
                 runs=[_run()], active=None)
    assert out["stages"][3]["done"] is True            # train
    assert out["next"]["id"] == "verify"


def test_a_finished_box_reports_complete() -> None:
    out = _build(env=_env(), datasets=[{"name": "d", "images": 200}],
                 projects=[{"slug": "p", "status": "finished"}], runs=[_run()],
                 active={"name": "run-1"},
                 transfers=[{"id": "t", "received": 2, "complete": True}])
    assert out["complete"] is True
    assert out["percent"] == 100
    assert out["next"] is None


def test_missing_torch_is_not_fatal_but_is_reported() -> None:
    out = _build(env=_env(torch=None))
    env = out["stages"][0]
    assert env["done"] is False
    labels = {c["label"]: c["done"] for c in env["checklist"]}
    assert labels["torch importable"] is False
    assert out["next"]["id"] == "env"


def test_database_disabled_says_so_instead_of_pretending() -> None:
    out = _build(db_enabled=False)
    project = next(s for s in out["stages"] if s["id"] == "project")
    assert "database disabled" in project["detail"]
    assert project["done"] is False


# --------------------------------------------------------------------------- #
# the next action has to be runnable
# --------------------------------------------------------------------------- #
def test_dataset_action_is_a_real_post_with_a_payload() -> None:
    out = _build()
    ds = next(s for s in out["stages"] if s["id"] == "dataset")
    action = ds["action"]
    assert action["kind"] == "post"
    assert action["endpoint"] == "/api/suite/datasets/synth"
    assert action["payload"]["train"] == pg.SYNTH_TRAIN > 0
    assert action["payload"]["val"] == pg.SYNTH_VAL > 0
    assert action["then"] == "/dataset"


def test_train_action_points_at_the_dataset_that_exists() -> None:
    out = _build(datasets=[{"name": "synth-01", "images": 44},
                           {"name": "train-merged", "images": 120}])
    tr = next(s for s in out["stages"] if s["id"] == "train")
    assert tr["action"]["kind"] == "post"
    assert tr["action"]["endpoint"] == "/api/suite/train"
    # prefer the merged dataset: that is the one assembled for training
    assert tr["action"]["payload"]["dataset"] == "train-merged"


def test_train_action_uses_the_box_sized_defaults() -> None:
    out = _build(datasets=[{"name": "d", "images": 60}],
                 suggested={"epochs": 40, "imgsz": 320, "batch": 2,
                            "workers": 2, "device": "cpu", "model": "yolo26n-pose.pt"})
    tr = next(s for s in out["stages"] if s["id"] == "train")
    payload = tr["action"]["payload"]
    assert payload["imgsz"] == 320          # a 2 GB box must not be told 448
    assert payload["batch"] == 2
    assert payload["device"] == "cpu"
    assert "320px" in tr["action"]["label"]


def test_train_action_without_a_dataset_is_a_link_not_a_broken_post() -> None:
    out = _build()
    tr = next(s for s in out["stages"] if s["id"] == "train")
    assert tr["action"]["kind"] == "link"
    assert tr["action"]["href"] == "/dataset"


def test_no_action_is_ever_undefined() -> None:
    for kwargs in ({}, {"datasets": [{"name": "d", "images": 1}]},
                   {"active": {"name": "m"}},
                   {"transfers": [{"id": "t", "received": 1, "complete": True}]}):
        out = _build(**kwargs)
        for stage in out["stages"]:
            assert stage["action"], stage["id"]
            assert stage["action"]["kind"] in ("link", "post")
            assert stage["href"].startswith("/")


def test_blockers_explain_why_later_steps_wait() -> None:
    out = _build()
    assert out["next"]["blocked_by"]
    assert all(isinstance(b, str) and b for b in out["next"]["blocked_by"])
    assert len(out["next"]["blocked_by"]) <= 3


def test_counts_are_consistent_with_the_inputs() -> None:
    out = _build(datasets=[{"name": "a", "images": 30}, {"name": "b", "images": 12}],
                 runs=[_run(), _run(name="run-2", has_weights=False)],
                 projects=[{"slug": "p", "status": "development"}],
                 transfers=[{"id": "t", "received": 1, "complete": False}],
                 sessions=[{"accepted": 7}])
    assert out["counts"] == {
        "datasets": 2, "images": 42, "runs": 2, "models": 0, "projects": 1,
        "transfers": 1, "shots": 7, "complete_transfers": 0, "jobs_running": 0}


def test_progress_advances_when_the_artefacts_appear() -> None:
    """The same inputs plus one artefact must move the recommendation on."""
    first = _build()
    assert first["next"]["id"] in ("project", "dataset")

    after_project = _build(projects=[{"slug": "p", "status": "development"}])
    assert after_project["next"]["id"] == "dataset"
    assert after_project["percent"] > first["percent"]

    after_data = _build(projects=[{"slug": "p", "status": "development"}],
                        datasets=[{"name": "d", "images": 50}])
    assert after_data["next"]["id"] == "train"


# --------------------------------------------------------------------------- #
# the HTTP surface (TestClient, same fixtures as the capture tests)
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_PASSWORD", "pw")
    monkeypatch.setenv("APP_SECRET", "secret")
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.setenv("SUITE_DB", "0")          # SQLite-free: pure disk state
    # Both apps read APP_SECRET / DATA_DIR at import time. Reload the capture app
    # too: the suite re-exports its ``require_auth``, and a cookie signed by a
    # freshly-imported suite against a stale capture module is a 401 (which is
    # exactly what happened when these tests ran after tests/test_capture.py).
    sys = __import__("sys")
    for mod in [m for m in list(sys.modules)
                if m.startswith(("optical_modem.suite", "optical_modem.capture.app"))]:
        sys.modules.pop(mod, None)
    import importlib

    from fastapi.testclient import TestClient

    import optical_modem.capture.app as capture_mod

    importlib.reload(capture_mod)
    from optical_modem.suite import app as app_mod
    with TestClient(app_mod.app) as c:
        c.post("/api/login", json={"password": "pw"})
        yield c


def test_progress_endpoint_shape(client) -> None:
    r = client.get("/api/suite/progress")
    assert r.status_code == 200
    data = r.json()
    for key in ("stages", "next", "percent", "done", "total", "counts"):
        assert key in data
    assert data["total"] == len(data["stages"]) == 6
    assert 0 <= data["percent"] <= 100
    stage = data["stages"][0]
    for key in ("id", "title", "href", "detail", "done", "checklist", "why"):
        assert key in stage
    assert stage["why"]


def test_finder_svg_is_served_for_every_shape(client) -> None:
    from optical_modem.protocol.finder import SHAPES

    for name in list(SHAPES) + ["none"]:
        r = client.get(f"/api/suite/finder/{name}.svg")
        assert r.status_code == 200, name
        assert r.headers["content-type"].startswith("image/svg+xml")
        assert r.text.startswith("<svg") and "</svg>" in r.text
    # the bitmap of a shape must actually contain filled tiles
    ring = client.get("/api/suite/finder/ring.svg?size=48").text
    assert ring.count("<rect") > 5


def test_unknown_finder_shape_is_a_404(client) -> None:
    assert client.get("/api/suite/finder/hexagram.svg").status_code == 404


def test_palette_endpoint_lists_modulations_with_colours(client) -> None:
    r = client.get("/api/suite/palette?cells=32&ecc=rs16")
    assert r.status_code == 200
    data = r.json()
    assert data["modulations"]
    by_name = {m["name"]: m for m in data["modulations"]}
    assert "rgb8" in by_name
    rgb8 = by_name["rgb8"]
    assert rgb8["bits"] == 3 and rgb8["colours"] == 8
    assert len(rgb8["hex"]) == 8
    assert all(h.startswith("#") and len(h) == 7 for h in rgb8["hex"])
    assert rgb8["max_user_payload"] > 0
    # greyscale profiles really are grey
    assert by_name["gray2"]["colours"] == 2


def test_curve_endpoint_reads_results_csv(client, tmp_path) -> None:
    run = tmp_path / "runs" / "demo-run"
    run.mkdir(parents=True)
    with (run / "results.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["epoch", "time", "train/box_loss", "metrics/mAP50-95(B)"])
        w.writerow([1, 12.0, 1.80, 0.20])
        w.writerow([2, 24.0, 1.21, 0.48])
        w.writerow([3, 36.0, 0.93, 0.62])
    data = client.get("/api/suite/runs/demo-run/curve").json()
    assert data["epochs"] == [1, 2, 3]
    assert data["series"]["metrics/mAP50-95(B)"] == [0.20, 0.48, 0.62]
    assert "time" not in {k.lower() for k in data["series"]}


def test_curve_endpoint_is_empty_not_broken_without_a_csv(client) -> None:
    data = client.get("/api/suite/runs/never-trained/curve").json()
    assert data["epochs"] == [] and data["series"] == {}


def test_wizard_page_is_served(client) -> None:
    r = client.get("/wizard")
    assert r.status_code == 200
    assert "Guided setup" in r.text
    assert '<div id="root"></div>' in r.text
    assert "/suite-assets/assets/" in r.text
    assert "__BUILD__" not in r.text
