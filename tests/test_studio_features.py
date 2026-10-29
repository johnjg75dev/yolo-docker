"""Tests for Studio enhancements: Channel Lab, shipped model fallbacks, and dataset ZIP export."""
from __future__ import annotations

import base64
import io
import zipfile

import pytest
from starlette.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_PASSWORD", "testpass")
    monkeypatch.setenv("SUITE_DB", "sqlite")
    monkeypatch.setenv("RUN_JOBS_INPROCESS", "1")

    import importlib
    import optical_modem.capture.app as cap_app
    import optical_modem.suite.app as suite_app

    importlib.reload(cap_app)
    importlib.reload(suite_app)

    c = TestClient(suite_app.app)
    r = c.post("/api/login", json={"password": "testpass"})
    assert r.status_code == 200
    return c


def test_spa_routes_serve_stamped_react_shell(client: TestClient) -> None:
    for route in ("/", "/wizard", "/s2c", "/lab", "/projects", "/datasets", "/guide"):
        r = client.get(route)
        assert r.status_code == 200, route
        assert '<div id="root"></div>' in r.text
        assert "/suite-assets/assets/" in r.text
        assert "__BUILD__" not in r.text


def test_channel_lab_simulate_and_decode(client: TestClient) -> None:
    sim = client.post(
        "/api/suite/lab/simulate",
        json={
            "text": "STUDIO LAB TEST",
            "modulation": "rgb8",
            "ecc": "rs32",
            "resolution": "1280x720",
            "symbol_px": 16,
            "finder": "none",
            "level": 0.1,
            "seed": 7,
            "mode": "classical",
        },
    )
    assert sim.status_code == 200, sim.text
    body = sim.json()
    assert body["ok"] is True
    assert body["payload_matches"] is True
    assert body["decoded_text"] == "STUDIO LAB TEST"
    assert body["tx_preview"].startswith("data:image/jpeg;base64,")
    assert body["cam_overlay"].startswith("data:image/jpeg;base64,")
    assert body["rectified"].startswith("data:image/jpeg;base64,")

    # Feed a real S2C frame PNG into /api/suite/lab/decode
    tx = client.post(
        "/api/suite/s2c/transmit",
        json={
            "text": "DECODE UPLOAD CHECK",
            "modulation": "rgb8",
            "ecc": "rs32",
            "resolution": "1280x720",
            "symbol_px": 16,
            "repeat": 1,
            "fps": 2,
            "finder": "none",
        },
    )
    assert tx.status_code == 200
    tid = tx.json()["transfer"]["id"]
    frame_png = client.get(f"/api/suite/s2c/{tid}/frame.png?index=0").content

    dec = client.post(
        "/api/suite/lab/decode",
        files={"file": ("frame.png", frame_png, "image/png")},
        data={
            "modulation": "rgb8",
            "ecc": "rs32",
            "resolution": "1280x720",
            "symbol_px": "16",
            "finder": "none",
            "mode": "classical",
        },
    )
    assert dec.status_code == 200, dec.text
    dbody = dec.json()
    assert dbody["ok"] is True
    assert "DECODE UPLOAD CHECK" in dbody["decoded_text"]
    assert dbody["cam_overlay"].startswith("data:image/jpeg;base64,")


def test_shipped_model_curves_eval_and_activation(client: TestClient) -> None:
    curve = client.get("/api/suite/runs/yolo26n_optical/curve")
    assert curve.status_code == 200
    c_json = curve.json()
    assert len(c_json["epochs"]) > 0
    assert any("mAP" in k for k in c_json["series"])

    ev = client.get("/api/suite/runs/yolo26n_optical/eval")
    assert ev.status_code == 200
    ev_json = ev.json()
    assert "levels" in ev_json and len(ev_json["levels"]) > 0

    models = client.get("/api/suite/models")
    assert models.status_code == 200
    m_json = models.json()
    assert any(m["name"] == "yolo26n_optical" for m in m_json.get("shipped", []))

    act = client.post("/api/suite/models/yolo26n_optical/activate")
    assert act.status_code == 200
    assert act.json()["active"]["name"] == "yolo26n_optical"


def test_dataset_export_zip_and_binary_s2c(client: TestClient, tmp_path) -> None:
    ds_dir = tmp_path / "datasets" / "tiny-export"
    (ds_dir / "images" / "train").mkdir(parents=True)
    (ds_dir / "labels" / "train").mkdir(parents=True)
    (ds_dir / "dataset.yaml").write_text("path: .\ntrain: images/train\nval: images/train\n")
    (ds_dir / "images" / "train" / "0001.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (ds_dir / "labels" / "train" / "0001.txt").write_text("0 0.5 0.5 0.5 0.5 0.25 0.25 2 0.75 0.25 2 0.75 0.75 2 0.25 0.75 2\n")

    zr = client.get("/api/suite/datasets/tiny-export/export.zip")
    assert zr.status_code == 200
    assert zr.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(zr.content), "r") as zf:
        names = set(zf.namelist())
        assert "tiny-export/dataset.yaml" in names
        assert "tiny-export/labels/train/0001.txt" in names

    # Binary data_b64 transmit
    raw = bytes(range(64))
    tx = client.post(
        "/api/suite/s2c/transmit",
        json={
            "data_b64": base64.b64encode(raw).decode("ascii"),
            "name": "probe.bin",
            "modulation": "rgb8",
            "ecc": "rs32",
            "resolution": "1280x720",
            "symbol_px": 16,
            "repeat": 1,
            "fps": 2,
            "finder": "none",
        },
    )
    assert tx.status_code == 200
    assert tx.json()["transfer"]["bytes"] == 64
