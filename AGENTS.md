# Optical Modem — screen-to-camera data transfer with YOLO26 frame decoding

A software-defined optical modem: a screen displays frames of coloured cells, a
camera reads them, and the receiver reconstructs the bytes. Stack: packetizer
→ CRC16 → FEC (Reed-Solomon / LDPC) → grid → screen → channel simulation →
localisation (YOLO26-pose detector or classical contours) → alignment → colour
calibration → LLR demodulation → FEC decode → CRC verify. Runs as one FastAPI
suite (uvicorn, one worker) plus optional Postgres 16 + Redis 7 via
`docker-compose.yml`; degrades to SQLite + in-process bus without them.

## Dev environment

Python 3.12+, no Node build step. From the repo root:

```bash
pip install -r requirements.txt          # modem CLI, GUI, tests
pip install -r requirements-suite.txt    # full suite: torch CPU, sqlalchemy, psycopg, redis
pip install -r requirements-web.txt      # capture station only (no torch)

export PYTHONPATH=$(pwd)            # required for `python -m optical_modem`
export DATA_DIR=/tmp/optical-data   # any writable dir; /data inside Docker
export APP_PASSWORD=demo            # required by /login
export APP_SECRET=demo-secret       # session cookie HMAC
export COOKIE_SECURE=0              # `auto` behind TLS; `0` for plain-HTTP LAN
export TRUST_PROXY=1                # `1` behind Coolify/Traefik; `0` if bare
```

## Build & test (exact invocations)

```bash
make install          # pip install -r requirements.txt
make test             # python -m pytest -q        (207 tests, ~100 s on 2 cores)
make info             # python -m optical_modem info
make render           # python -m optical_modem render --text "hello optical modem" --out frame.png
make sim              # python -m optical_modem sim --level 0.6 --n 10   (TX→camera→RX)
make gui              # python -m optical_modem gui --port 8889
make dataset          # python -m optical_modem dataset --train 1200 --val 200
make train            # python -m optical_modem.data.train_yolo --epochs 12 --imgsz 384 --batch 4 --workers 0
make bench            # python -m optical_modem bench --n 12   (writes docs/BENCHMARKS.md)
make clean            # rm -rf .pytest_cache **/__pycache__ runs
```

Manual equivalents:

```bash
python -m optical_modem receive capture.jpg
python -m optical_modem send --file README.md --out frames/
python -m uvicorn optical_modem.suite.app:app --host 127.0.0.1 --port 8200 --workers 1   # local suite (no Docker)
python scripts/suite_smoke.py --base http://localhost:8200 --password demo   # end-to-end smoke test
```

CLI entry: `python -m optical_modem <subcommand>` where subcommand is one of
`info | render | receive | sim | send | bench | gui | train | dataset`. All
take `--modulation` (default `rgb8`) and `--ecc` (default `rs16`).

## Conventions (observed, not assumed)

* **One uvicorn worker only.** Sessions, transfers and the job registry live
  in process (`suite/app.py`, `suite/jobs.py`). Two workers → a phone joins a
  session the display cannot see, and `404` on
  `/api/suite/s2c/<id>/frame.png`.
* **`DATA_DIR` must be set** outside Docker (the capture app raises
  `PermissionError: /data` otherwise). State layout:
  `datasets/<name>/`, `runs/<name>/`, `models/<name>/`, `jobs/<id>/`,
  `sessions/<id>/`, `secret.key`.
* **Rendered frames are never resized** — native PNG, `object-fit: contain`.
  Downscaling destroys sub-cell registration.
* **YOLO26 runs at `imgsz=448`.** 640 shrinks keypoints ~0.7× on square inputs
  (Ultralytics 8.4 quirk). Verify changes with `python -m optical_modem sim --level 0`.
* **Keypoints are grid corners** (`0=TL 1=TR 2=BR 3=BL`, `vis ∈ {0,1,2}`),
  normalised to the captured photo. Negative or >1 coords are silently dropped
  by Ultralytics; the synth generator clamps and skips degenerate views.
* **`header:ValueError` is a demodulation failure, not a framing bug.** Pair it
  with `ring_score` — ≈1.0 means geometry is right, the bits are not.
* **No lint/format config exists** (no ruff, pre-commit, or pyproject). Match
  the surrounding file's style; imports grouped stdlib → third-party → local.
* Commits are short imperative messages (git log: "Bind uvicorn to 0.0.0.0…",
  "Add HTTPS diagnostic tools…", "Initial release for beta testing").

## Layout

```
optical_modem/
  config.py        ModemConfig, palettes, ECC profiles, capacity
  phy/             render, channel, locate, align, demod, classical
  protocol/        packet, ecc, frames, finder
  detect/yolo.py   YOLO26-pose adapter (4 keypoints = grid corners)
  receiver.py      HybridReceiver (mode = auto | yolo | classical)
  sim.py           prepare_frame, capture
  data/synth.py    synthetic dataset generator
  data/train_yolo.py CPU trainer → runs/<name>/weights/best.pt
  suite/           FastAPI app + jobs / db / bus / registry / datasets / s2c / static
  capture/         capture station (display + camera PWA)
  gui/             standalone web GUI
  cli.py           argparse entry (python -m optical_modem)
tests/             207 unit tests (phy, protocol, receiver, finder, store, bus, net, transfer, synth)
scripts/           suite_smoke, evaluate, captures_to_dataset, run_benchmarks, check_https, ui_preview
docs/              PROTOCOL, MODEL, BENCHMARKS, CAPTURE_APP, SUITE_HANDOFF (read this first when handed the repo)
models/yolo26n-pose.pt   bundled CPU detector (~7.9 MB)
```

## Pitfalls

* **[Errno 98] address already in use** — the old process is still listening.
  Stop it properly (SIGTERM to the process group); `fuser -k` can leave the listener behind.
* **`COOKIE_SECURE=1` breaks plain-HTTP LAN testing** — browsers drop the `Secure` cookie silently, login "does nothing". Default `auto` is correct.
* **`FORCE_HTTPS=1` behind a proxy causes redirect loops** — two redirectors. Leave `0` while Coolify/Traefik owns http→https; `1` only for bare `docker run -p 80:8000`.
* **Bundled `yolo26n-pose.pt` was trained on the v0.2 renderer** (flat tiles, no sub-cell patterns). It localises but decodes poorly through the detector; retraining on v0.3 frames is pending.
* **The web process must stay torch-free** — expensive work runs as subprocesses via `suite/jobs.py`. Do not import ultralytics/torch at module scope in `suite/app.py`; `describe_env()` reads versions from package metadata on purpose (torch ≈ 350 MB RSS otherwise).
* **`header:ValueError` with `ring_score ≈ 1.0` at channel level ≥ 0.35** is the highest-value open task (≈25–50 % of hard frames lost); see `docs/SUITE_HANDOFF.md` §10 for the ranked fix list.
* **Phone camera (`getUserMedia`) and service workers need HTTPS** — plain http can never be the production answer; deploy with a domain + TLS (Coolify Let's Encrypt) or test locally with `scripts/dev_tls_proxy.py`.
