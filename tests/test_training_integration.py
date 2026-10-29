"""Opt-in real, offline CPU training through the suite API and job manager."""
import importlib
import os
import time

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('RUN_TRAINING_INTEGRATION') != '1', reason='real CPU training is opt-in')


def test_real_cpu_training_job(tmp_path, monkeypatch):
    pytest.importorskip('torch')
    pytest.importorskip('ultralytics')
    from fastapi.testclient import TestClient
    from optical_modem.suite.datasets import synth_dataset

    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('APP_PASSWORD', 'testpass')
    monkeypatch.setenv('SUITE_DB', 'sqlite')
    monkeypatch.setenv('SUITE_RUN_JOBS', 'inprocess')
    monkeypatch.setenv('YOLO_OFFLINE', '1')
    monkeypatch.setenv('YOLO_CONFIG_DIR', str(tmp_path / '.ultralytics'))
    monkeypatch.setenv('OMP_NUM_THREADS', '2')
    synth_dataset(tmp_path / 'datasets/small', n_train=10, n_val=2, seed=41, level=0.4, workers=1)
    import optical_modem.capture.app as capture
    import optical_modem.suite.app as suite
    importlib.reload(capture)
    importlib.reload(suite)
    with TestClient(suite.app) as client:
        assert client.post('/api/login', json={'password': 'testpass'}).status_code == 200
        result = client.post('/api/suite/train', json={'dataset': 'small', 'name': 'real-smoke', 'epochs': 1, 'imgsz': 160, 'batch': 0, 'device': 'cpu', 'workers': 0})
        assert result.status_code == 200, result.text
        job_id = result.json()['job']['id']
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            job = client.get(f'/api/jobs/{job_id}').json()
            if not job['live']:
                break
            time.sleep(0.5)
        assert job['status'] == 'ok', job.get('tail') or job.get('error')
        assert job['progress'] == 1
        assert (tmp_path / 'runs/real-smoke/weights/best.pt').is_file()
        assert client.get('/api/suite/runs/real-smoke/curve').json()['epochs']
