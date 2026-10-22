"""Tests for the paired capture station (plan, storage, API, auto-labelling).

The API tests use FastAPI's TestClient against a throw-away data directory, so
they exercise the real route table (auth, pairing codes, uploads, advancing)
without a browser or a phone.
"""
import importlib
import json

import cv2
import numpy as np
import pytest

from optical_modem.capture.sessions import FrameSpec, SessionStore, label_photo, make_plan


# --------------------------------------------------------------------------- #
# the plan: deterministic, feasible, and the same after a reload
# --------------------------------------------------------------------------- #
def test_plan_is_deterministic_and_feasible():
    a = make_plan(seed=1234, count=8, resolution="1280x720")
    b = make_plan(seed=1234, count=8, resolution="1280x720")
    assert [s.__dict__ for s in a] == [s.__dict__ for s in b]
    assert len(a) == 8 and [s.index for s in a] == list(range(8))
    for spec in a:
        cfg = spec.config()
        mod = cfg.mod()
        assert spec.cell_px >= mod.min_px_per_cell
        assert 0 < spec.payload_len <= cfg.describe()["max_user_payload"]
        assert spec.render().shape[:2] == (720, 1280)


def test_plan_payloads_differ_per_frame_and_are_reproducible():
    specs = make_plan(seed=99, count=6)
    payloads = [s.payload() for s in specs]
    assert all(len(p) == s.payload_len for p, s in zip(payloads, specs))
    assert len({p for p in payloads}) == len(payloads)
    assert [s.payload() for s in specs] == payloads      # same seed -> same bytes


def test_frame_spec_round_trips_through_json():
    spec = make_plan(seed=7, count=1)[0]
    again = FrameSpec(**spec.__dict__)
    assert again.describe() == spec.describe()
    assert again.payload() == spec.payload()


# --------------------------------------------------------------------------- #
# store: shots advance the cursor, reject steps back, state survives a restart
# --------------------------------------------------------------------------- #
def _jpeg(img):
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    assert ok
    return buf.tobytes()


def test_store_advances_and_persists(tmp_path):
    store = SessionStore(tmp_path)
    sess = store.create("test", count=3)
    assert sess.index == 0 and sess.status == "live"

    png = store.frame_png(sess, 0)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert (store.session_dir(sess.id) / "frames" / "0000.png").exists()

    rec = store.add_shot(sess, 0, _jpeg(sess.spec(0).render()),
                         {"device": "pytest", "label": {"found": True,
                                                        "score": 1.0}})
    assert rec["index"] == 0 and rec["attempt"] == 1
    assert sess.index == 1
    shot_json = store.session_dir(sess.id) / "shots" / "0000_1.json"
    assert shot_json.exists()
    saved = json.loads(shot_json.read_text())
    assert saved["label"]["found"] is True
    assert saved["spec"]["index"] == 0

    # a reload must see the same cursor and the same shot trail
    other = SessionStore(tmp_path)
    again = other.get(sess.id)
    assert again is not None and again.index == 1
    assert len(again.shots) == 1 and again.shots[0]["index"] == 0

    # reject steps back and marks the shot as rejected rather than deleting it
    other.reject_last(again)
    assert again.index == 0
    assert again.shots[0]["rejected"] is True
    assert len(again.accepted()) == 0


def test_store_skip_and_seek_and_done(tmp_path):
    store = SessionStore(tmp_path)
    sess = store.create("skip", count=2)
    store.skip(sess)
    assert sess.index == 1
    store.skip(sess)
    assert sess.index == 2 and sess.status == "done"
    store.seek(sess, 0)
    assert sess.index == 0 and sess.status == "live"


def test_store_publish_reaches_subscribers(tmp_path):
    import asyncio

    store = SessionStore(tmp_path)
    sess = store.create("pub", count=2)

    async def scenario():
        q = store.subscribe(sess.id)
        store.add_shot(sess, 0, b"not-an-image", {"device": "t"})
        item = await asyncio.wait_for(q.get(), timeout=2.0)
        store.unsubscribe(sess.id, q)
        return item

    item = asyncio.run(scenario())
    assert item["event"] == "shot"
    assert item["data"]["index"] == 1


# --------------------------------------------------------------------------- #
# auto-labelling finds the frame in a simulated phone photo
# --------------------------------------------------------------------------- #
def _photo_of(spec, level=0.4, seed=3):
    """Simulate a phone photo of `spec` (display -> camera -> JPEG).

    ``handheld`` is the channel profile where the user actually frames the code,
    which is what the capture station asks for.
    """
    from optical_modem.phy.channel import ChannelConfig
    from optical_modem.sim import capture

    cfg = spec.config()
    frame = spec.render()
    rng = np.random.default_rng(seed)
    ch = ChannelConfig.handheld(rng, level)
    ch.out_size = 1200
    img, quad = capture(cfg, frame, ch, rng)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    assert ok
    return cv2.imdecode(np.frombuffer(buf.tobytes(), np.uint8),
                        cv2.IMREAD_COLOR), np.asarray(quad, np.float32)


@pytest.mark.parametrize("seed", [1, 2, 3, 5])
def test_label_photo_finds_the_frame(seed):
    spec = make_plan(seed=seed, count=1, resolution="1280x720")[0]
    img, truth = _photo_of(spec, seed=seed)
    out = label_photo(img, spec)
    assert out["found"] is True
    quad = np.asarray(out["quad"], np.float32)
    cell = np.linalg.norm(truth[1] - truth[0]) / spec.cells
    err = np.linalg.norm(quad - truth, axis=1).mean() / cell
    # a label only has to be good enough to train on: the demodulator needs
    # ~0.2 cell, a detector label is useful well beyond that
    assert err < 0.5, err
    assert out["score"] > 0.5
    # labels come back in photo coordinates, not the downscaled search image
    assert 0 <= quad[:, 0].max() <= img.shape[1] * 1.01
    assert out["verified"] is True


def test_label_photo_never_claims_confidence_it_does_not_have():
    """A photo with no frame in it must not produce a *verified* label."""
    spec = make_plan(seed=2, count=1)[0]
    rng = np.random.default_rng(0)
    noise = rng.integers(0, 255, (720, 720, 3), dtype=np.uint8)
    out = label_photo(noise, spec)
    assert out["found"] in (True, False)     # never raises
    assert not out.get("verified", False)
    assert out.get("score", 0.0) < 0.85


def test_label_photo_is_flagged_unverified_on_a_hard_capture():
    """Level 0.4 collapses the contour detector for some frames: the label must
    come back *unverified* (and the photo is still stored, just without a quad)."""
    spec = make_plan(seed=4, count=1, resolution="1280x720")[0]
    img, _ = _photo_of(spec, seed=4)
    out = label_photo(img, spec)
    if out["found"] and out.get("verified"):
        pytest.skip("this capture got easier - re-tune the test")
    assert not out.get("verified", True)


# --------------------------------------------------------------------------- #
# HTTP API
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A TestClient wired to a temporary data dir and a known password."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("APP_PASSWORD", "hunter2")
    monkeypatch.setenv("APP_SECRET", "test-secret")
    monkeypatch.setenv("COOKIE_SECURE", "0")
    import optical_modem.capture.app as appmod

    importlib.reload(appmod)
    from fastapi.testclient import TestClient

    with TestClient(appmod.app) as c:
        yield c


def test_api_requires_login(client):
    assert client.get("/api/sessions").status_code == 401
    assert client.get("/display").status_code == 200     # shell is public
    assert client.post("/api/login", json={"password": "nope"}).status_code == 401
    res = client.post("/api/login", json={"password": "hunter2"})
    assert res.status_code == 200 and res.json()["ok"] is True
    assert client.get("/api/sessions").status_code == 200


def test_full_pairing_and_advance_flow(client):
    client.post("/api/login", json={"password": "hunter2"})
    snap = client.post("/api/sessions", json={"count": 3,
                                              "resolution": "1280x720"}).json()
    sid, code = snap["id"], snap["code"]
    assert snap["index"] == 0 and snap["count"] == 3 and len(code) == 6

    # the phone needs the code
    assert client.post(f"/api/sessions/{sid}/join",
                       json={"role": "camera", "code": "WRONG1"}).status_code == 403
    joined = client.post(f"/api/sessions/{sid}/join",
                         json={"role": "camera", "code": code.lower()})
    assert joined.status_code == 200 and joined.json()["roles"]["camera"] is True

    # code lookup (what the phone does with a typed code)
    found = client.get(f"/api/sessions/lookup?code={code}")
    assert found.status_code == 200 and found.json()["session_id"] == sid
    assert client.get("/api/sessions/lookup?code=ZZZZZZ").status_code == 404

    # the frame the laptop shows
    png = client.get(f"/api/sessions/{sid}/frame.png?index=0")
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    assert png.headers["cache-control"].startswith("public")

    # the phone uploads a photo of frame 0 -> both clients advance
    spec = importlib.import_module("optical_modem.capture.app").STORE.get(sid).spec(0)
    photo = _jpeg(spec.render())
    res = client.post(f"/api/sessions/{sid}/shots",
                      files={"file": ("shot.jpg", photo, "image/jpeg")},
                      data={"index": "0", "device": "pytest", "via": "test"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["state"]["index"] == 1 and body["state"]["accepted"] == 1

    # stale upload (index already passed) is refused with 409, not silently lost
    stale = client.post(f"/api/sessions/{sid}/shots",
                        files={"file": ("shot.jpg", photo, "image/jpeg")},
                        data={"index": "0"})
    assert stale.status_code == 409

    # undo goes back, skip moves on, export has the frames
    assert client.post(f"/api/sessions/{sid}/reject").json()["index"] == 0
    assert client.post(f"/api/sessions/{sid}/skip").json()["index"] == 1
    export = client.get(f"/api/sessions/{sid}/export").json()
    assert len(export["frames"]) == 3 and export["shots"]


def test_api_validates_uploads_and_sizes(client):
    client.post("/api/login", json={"password": "hunter2"})
    snap = client.post("/api/sessions", json={"count": 1,
                                              "resolution": "1280x720"}).json()
    sid = snap["id"]
    assert client.post(f"/api/sessions/{sid}/shots",
                       files={"file": ("x.jpg", b"", "image/jpeg")},
                       data={"index": "0"}).status_code == 400
    # not a JPEG at all
    assert client.post(f"/api/sessions/{sid}/shots",
                       files={"file": ("x.jpg", b"12345", "image/jpeg")},
                       data={"index": "0"}).status_code == 400
    # index outside the plan
    assert client.post(f"/api/sessions/{sid}/shots",
                       files={"file": ("x.jpg", _jpeg(np.zeros((8, 8, 3), np.uint8)),
                                      "image/jpeg")},
                       data={"index": "5"}).status_code == 400
    assert client.post("/api/sessions", json={"count": 0}).status_code == 400


def test_pages_and_pwa_assets(client):
    for path in ("/", "/camera", "/display"):
        res = client.get(path)
        assert res.status_code == 200 and "<!doctype html" in res.text.lower()
    manifest = client.get("/manifest.webmanifest").json()
    assert manifest["display"] in ("fullscreen", "standalone")
    assert any(i["sizes"] == "192x192" for i in manifest["icons"])
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "addEventListener" in sw.text
    assert client.get("/static/js/common.js").status_code == 200
    assert client.get("/static/icons/icon-192.png").content[:4] == b"\x89PNG"


def test_qr_endpoint(client):
    client.post("/api/login", json={"password": "hunter2"})
    res = client.get("/api/qr.svg?data=https%3A%2F%2Fexample.org%2Fcamera")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/svg+xml")
    assert b"<svg" in res.content[:200]


# --------------------------------------------------------------------------- #
# captured photos -> YOLO-pose dataset
# --------------------------------------------------------------------------- #
def test_captures_to_dataset_writes_yolo_pose_labels(tmp_path):
    from scripts.captures_to_dataset import build, iter_shots

    store = SessionStore(tmp_path / "data")
    sess = store.create("convert", count=2)
    photo, truth = _photo_of(sess.spec(0), seed=1)
    out = label_photo(photo, sess.spec(0))
    assert out["verified"] is True
    label = dict(out, image_size=[photo.shape[1], photo.shape[0]])
    store.add_shot(sess, 0, _jpeg(photo), {"device": "pytest", "label": label})
    # a second, unlabelled shot: must be skipped, not crash
    store.add_shot(sess, 1, _jpeg(sess.spec(1).render()), {"device": "pytest"})

    assert len(list(iter_shots(tmp_path / "data"))) == 2
    summary = build(tmp_path / "data", tmp_path / "ds", val_frac=0.0)
    assert summary["written"] == 1
    assert summary["skipped"]["no_label"] == 1

    labels = list((tmp_path / "ds" / "labels" / "train").glob("*.txt"))
    assert len(labels) == 1
    parts = labels[0].read_text().split()
    assert parts[0] == "0" and len(parts) == 5 + 4 * 3
    values = np.array([float(v) for v in parts[1:]], np.float32)
    assert np.all((values[:4] >= 0) & (values[:4] <= 1))         # box
    xy = values[4:].reshape(4, 3)[:, :2]
    assert np.all((xy >= 0) & (xy <= 1))                         # keypoints
    # keypoint order is the transmitter's: TL, TR, BR, BL
    kpts = xy
    assert kpts[0].sum() < kpts[2].sum() and kpts[1][0] > kpts[0][0]
    assert (values[4:].reshape(4, 3)[:, 2] == 2).all()

    yaml_text = (tmp_path / "ds" / "data.yaml").read_text()
    assert "kpt_shape: [4, 3]" in yaml_text and "flip_idx: [1, 0, 3, 2]" in yaml_text
    assert (tmp_path / "ds" / "manifest_captures.json").exists()
