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


def test_assign_and_curate_existing_dataset(client: TestClient, tmp_path) -> None:
    dataset = tmp_path / 'datasets' / 'existing'
    (dataset / 'images/train').mkdir(parents=True)
    (dataset / 'labels/train').mkdir(parents=True)
    (dataset / 'images/train/a.jpg').write_bytes(b'image')
    (dataset / 'labels/train/a.txt').write_text('0 0.5 0.5 1 1\n')
    (dataset / 'data.yaml').write_text('path: old\ntrain: images/train\nval: images/val\nnames: [frame]\n')
    for slug in ('first', 'second'):
        assert client.post('/api/suite/projects', json={'slug': slug, 'title': slug}).status_code == 200
    assert client.post('/api/suite/datasets/existing/assign', json={'project': 'first'}).status_code == 200
    assert client.post('/api/suite/datasets/existing/assign', json={'project': 'second'}).status_code == 409
    assert client.post('/api/suite/datasets/existing/assign', json={'project': 'second', 'allow_move': True}).status_code == 200
    r = client.post('/api/suite/datasets/existing/curate', json={'name': 'selection', 'project': 'first', 'selected': {'train': ['a.jpg'], 'val': []}})
    assert r.status_code == 200, r.text
    assert (tmp_path / 'datasets/selection/labels/train/a.txt').read_text() == '0 0.5 0.5 1 1\n'
    assert (dataset / 'images/train/a.jpg').exists()
    assert client.post('/api/suite/datasets/existing/curate', json={'name': '../escape', 'selected': {'train': ['a.jpg']}}).status_code == 400
    detail = client.get('/api/suite/projects/first').json()
    assert any(d['name'] == 'selection' for d in detail['datasets'])


def test_training_defaults_and_safe_label_edit(client, tmp_path, monkeypatch):
    import optical_modem.suite.app as suite
    from types import SimpleNamespace
    dataset = tmp_path / 'datasets' / 'testdata'
    (dataset / 'images/train').mkdir(parents=True)
    (dataset / 'labels/train').mkdir(parents=True)
    (dataset / 'images/train/a.png').write_bytes(b'image')
    (dataset / 'data.yaml').write_text('path: .\ntrain: images/train\nval: images/val\n')
    label = dataset / 'labels/train/a.txt'
    original = '0 0.5 0.5 0.5 0.5 ' + '0.5 0.5 2 ' * 4
    label.write_text(original + '\n' + original + '\n')
    r = client.put('/api/suite/datasets/testdata/label/train/a.png', json={'keypoints': []})
    assert r.status_code == 409
    assert label.read_text() == original + '\n' + original + '\n'
    monkeypatch.setattr(suite, 'describe_env', lambda: {'cuda': False})
    launched = {}
    def start(*args, **kwargs):
        launched['argv'] = args[2]
        return SimpleNamespace(as_dict=lambda: {'id': 'test'})
    monkeypatch.setattr(suite.JOBS, 'start', start)
    r = client.post('/api/suite/train', json={'dataset': 'testdata', 'name': 'run1', 'batch': 0, 'device': 'auto'})
    assert r.status_code == 200, r.text
    assert launched['argv'][launched['argv'].index('--batch') + 1] == '4'
    assert launched['argv'][launched['argv'].index('--device') + 1] == 'cpu'
    r = client.post('/api/suite/datasets/synth', json={'name': '../escape', 'train': 10, 'val': 2})
    assert r.status_code == 400


def test_cross_origin_mutations_rejected_by_default(client):
    response = client.post('/api/suite/projects', headers={'Origin': 'https://evil.example'}, json={'slug': 'unexpected', 'title': 'Unexpected'})
    assert response.status_code == 403
    preflight = client.options('/api/suite/projects', headers={'Origin': 'https://evil.example', 'Access-Control-Request-Method': 'POST'})
    assert preflight.status_code == 400
    assert 'access-control-allow-origin' not in preflight.headers
    assert client.post('/api/suite/projects', headers={'Origin': 'http://testserver'}, json={'slug': 'allowed', 'title': 'Allowed'}).status_code == 200


def test_crop_rejects_stale_previews(client, tmp_path):
    from PIL import Image
    ds = tmp_path / 'datasets' / 'crop-test'
    images = ds / 'images/train'
    labels = ds / 'labels/train'
    images.mkdir(parents=True); labels.mkdir(parents=True)
    Image.new('RGB', (80, 60), 'red').save(images / 'a.png')
    original = (images / 'a.png').read_bytes()
    url = '/api/suite/datasets/crop-test/crop/train/a.png'
    revision = client.get(url).json()['revision']
    (labels / 'a.txt').write_text('')
    payload = {'x': 0, 'y': 0, 'width': 40, 'height': 40, 'revision': revision}
    assert client.post(url, json=payload).status_code == 409
    assert (images / 'a.png').read_bytes() == original
    payload['revision'] = client.get(url).json()['revision']
    assert client.post(url, json=payload).status_code == 200
    assert client.get(url).json()['has_backup'] is True


def test_default_model_is_in_container_build_context():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert (root / 'yolo26n-pose.pt').is_file()
    assert 'COPY yolo26n-pose.pt ./yolo26n-pose.pt' in (root / 'Dockerfile').read_text()


def test_dataset_is_read_only_during_live_job(client, tmp_path, monkeypatch):
    from types import SimpleNamespace
    import optical_modem.suite.app as suite
    dataset = tmp_path / 'datasets' / 'busy'
    (dataset / 'images/train').mkdir(parents=True)
    (dataset / 'images/train/a.png').write_bytes(b'original')
    monkeypatch.setattr(suite.JOBS, 'list', lambda **kwargs: [SimpleNamespace(live=True, title='Train busy', meta={'dataset': 'busy'})])
    assert client.put('/api/suite/datasets/busy/label/train/a.png', json={'text': ''}).status_code == 409
    assert client.delete('/api/suite/datasets/busy').status_code == 409
    assert (dataset / 'images/train/a.png').read_bytes() == b'original'


def test_resume_rejects_missing_original_dataset(client, tmp_path):
    run = tmp_path / 'runs' / 'old'
    (run / 'weights').mkdir(parents=True)
    (run / 'weights/last.pt').write_bytes(b'placeholder')
    (run / 'args.yaml').write_text('data: /missing/dataset/data.yaml\n')
    r = client.post('/api/suite/runs/old/resume', json={})
    assert r.status_code == 400
    assert 'original dataset' in r.json()['detail']


def test_health_fails_when_database_is_unavailable(client, monkeypatch):
    import optical_modem.suite.app as suite
    class BrokenStore:
        kind = 'broken'
        def counts(self):
            raise RuntimeError('offline')
    monkeypatch.setattr(suite, 'DB', BrokenStore())
    suite.app.dependency_overrides[suite._require_internal] = lambda: None
    try:
        result = client.get('/healthz')
        assert result.status_code == 503
        assert result.json()['database']['ok'] is False
    finally:
        suite.app.dependency_overrides.pop(suite._require_internal, None)


def test_export_rewrites_absolute_dataset_yaml(client, tmp_path):
    import yaml
    ds = tmp_path / 'datasets' / 'portable'
    (ds / 'images/train').mkdir(parents=True)
    (ds / 'data.yaml').write_text(f'path: {ds}\ntrain: images/train\nval: images/train\nnames: [frame]\n')
    r = client.get('/api/suite/datasets/portable/export.zip')
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
        config = yaml.safe_load(archive.read('portable/data.yaml'))
        assert 'path' not in config
        assert config['train'] == 'images/train'
    assert str(ds) in (ds / 'data.yaml').read_text()


def test_explicit_cross_origin_configuration(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('APP_PASSWORD', 'testpass')
    monkeypatch.setenv('SUITE_DB', 'sqlite')
    monkeypatch.setenv('CORS_ORIGINS', 'https://studio.example')
    import optical_modem.capture.app as capture
    import optical_modem.suite.app as suite
    importlib.reload(capture)
    importlib.reload(suite)
    with TestClient(suite.app) as c:
        preflight = c.options('/api/login', headers={'Origin': 'https://studio.example', 'Access-Control-Request-Method': 'POST'})
        assert preflight.status_code == 200
        assert preflight.headers['access-control-allow-origin'] == 'https://studio.example'
        result = c.post('/api/login', headers={'Origin': 'https://studio.example'}, json={'password': 'testpass'})
        assert result.status_code == 200
        assert result.headers['access-control-allow-origin'] == 'https://studio.example'


def test_extend_trains_only_requested_additional_epochs(client, tmp_path, monkeypatch):
    import optical_modem.suite.app as suite
    from types import SimpleNamespace
    ds = tmp_path / 'datasets' / 'extend-data'
    (ds / 'images/train').mkdir(parents=True)
    (ds / 'data.yaml').write_text('path: .\ntrain: images/train\nval: images/train\n')
    run = tmp_path / 'runs' / 'old'
    (run / 'weights').mkdir(parents=True)
    (run / 'weights/best.pt').write_bytes(b'checkpoint')
    (run / 'args.yaml').write_text(f'data: {ds / "data.yaml"}\n')
    launched = {}
    def start(*args, **kwargs):
        launched['argv'] = args[2]
        return SimpleNamespace(as_dict=lambda: {'id': 'test'})
    monkeypatch.setattr(suite.JOBS, 'start', start)
    r = client.post('/api/suite/runs/old/extend', json={'epochs': 10})
    assert r.status_code == 200, r.text
    assert launched['argv'][launched['argv'].index('--epochs') + 1] == '10'


def _boot_capture(env_overrides):
    """Import optical_modem.capture.app in a subprocess; return the CompletedProcess."""
    import os
    import subprocess
    import sys
    env = {**os.environ, 'DATA_DIR': str(tmp_path_hint), **env_overrides}
    env.pop('APP_PASSWORD', None)
    env.pop('ALLOW_ANONYMOUS', None)
    env.update(env_overrides)
    return subprocess.run([sys.executable, '-c', 'import optical_modem.capture.app'],
                          env=env, capture_output=True, text=True)


def test_production_password_requirement(tmp_path):
    """A missing password must be fatal, with or without REQUIRE_PASSWORD.

    Previously auth was simply skipped whenever APP_PASSWORD was empty, so the
    documented `uvicorn ... --host 0.0.0.0` workflow published a wide-open
    server with no login prompt anywhere.
    """
    import os
    import subprocess
    import sys

    def boot(overrides):
        env = {**os.environ, 'DATA_DIR': str(tmp_path)}
        env.pop('APP_PASSWORD', None)
        env.pop('ALLOW_ANONYMOUS', None)
        env.update(overrides)
        return subprocess.run([sys.executable, '-c', 'import optical_modem.capture.app'],
                              env=env, capture_output=True, text=True)

    # the old contract: REQUIRE_PASSWORD=1 with no password is fatal
    r = boot({'REQUIRE_PASSWORD': '1', 'APP_PASSWORD': ''})
    assert r.returncode != 0
    assert 'APP_PASSWORD' in r.stderr

    # the new, stricter default: no password is fatal even without REQUIRE_PASSWORD
    r = boot({'REQUIRE_PASSWORD': '0', 'APP_PASSWORD': ''})
    assert r.returncode != 0, "must fail closed by default, not run unauthenticated"
    assert 'APP_PASSWORD' in r.stderr

    # and the explicit opt-out still works
    r = boot({'ALLOW_ANONYMOUS': '1', 'APP_PASSWORD': ''})
    assert r.returncode == 0, f"ALLOW_ANONYMOUS=1 must boot: {r.stderr[-400:]}"


def test_anonymous_mode_is_reported(tmp_path):
    """The UI needs to know it is running open, instead of silently auto-logging in."""
    from starlette.testclient import TestClient
    import optical_modem.capture.app as capture
    from fastapi.testclient import TestClient as _TC
    client = TestClient(capture.app)
    body = client.get('/api/capabilities').json()
    assert 'auth_mode' in body
    assert body['auth_mode'] in ('password', 'anonymous')
    assert body['auth_mode'] == ('password' if capture.APP_PASSWORD else 'anonymous')


def test_security_headers_and_bmp_type(client, tmp_path):
    ds = tmp_path / 'datasets' / 'bitmap' / 'images/train'
    ds.mkdir(parents=True)
    (ds / 'image.bmp').write_bytes(b'BMfake')
    r = client.get('/api/suite/datasets/bitmap/image/train/image.bmp')
    assert r.headers['content-type'] == 'image/bmp'
    assert r.headers['x-content-type-options'] == 'nosniff'
    assert r.headers['referrer-policy'] == 'no-referrer'


def test_logout_revokes_token_after_reload(client):
    import importlib
    import optical_modem.capture.app as capture
    login = client.post('/api/login', json={'password': 'testpass'})
    token = login.json()['token']
    assert client.post('/api/logout').status_code == 200
    assert client.get('/api/me', headers={'X-OM-Token': token}).status_code == 401
    importlib.reload(capture)
    assert not capture._verify(token)
