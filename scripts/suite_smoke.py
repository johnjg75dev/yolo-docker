#!/usr/bin/env python3
"""End-to-end smoke test of a running suite, over plain HTTP.

Everything here is what a browser would do, in order — log in, generate a small
synthetic dataset, merge it, train for a couple of epochs, evaluate, promote the
model, transmit a message and read it back — so if this passes, the whole
pipeline in the container works.

    python scripts/suite_smoke.py --base http://localhost:8000 --password secret
    python scripts/suite_smoke.py --base https://suite.example.com --password "$PW" \
        --train-epochs 3 --train-imgsz 320 --synth-train 40 --synth-val 12

It prints one line per stage and exits non-zero on the first failure. Nothing is
deleted: the datasets/runs it creates are named ``smoke-*`` so you can inspect
them in the UI afterwards (and delete them there).

Notes
-----
* Every write endpoint returns *before* the job finishes.  This script therefore
  treats a POST as "see ``job.id``" and then polls ``/api/jobs/<id>``.
* Training is the only slow stage.  On a 2-4 core CPU box, 40 synthetic images
  and 2 epochs at 320 px is about a minute; on 8 GB of RAM you can afford more.
* Run it from the repository root with ``PYTHONPATH=.`` set, or just
  ``python scripts/suite_smoke.py`` (the script fixes ``sys.path`` itself).
"""
from __future__ import annotations

import argparse
import json
import http.cookiejar
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class Suite:
    def __init__(self, base: str, password: str, timeout: float = 620.0):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self.password = password

    # ---- plumbing --------------------------------------------------------
    def call(self, path: str, payload=None, raw: bytes | None = None,
             ctype: str = "application/json", method: str | None = None):
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode() if ctype == "application/json" else payload
            headers["Content-Type"] = ctype
        if raw is not None:
            data = raw
            headers["Content-Type"] = ctype
        req = urllib.request.Request(self.base + path, data=data, headers=headers,
                                     method=method or ("POST" if data is not None else "GET"))
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                body = resp.read()
                if resp.headers.get_content_type() == "application/json":
                    return json.loads(body)
                return body
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:400].decode("utf-8", "replace")
            raise SystemExit(f"FAIL {path}: HTTP {exc.code} {detail}") from None

    def login(self):
        self.call("/api/login", {"password": self.password})
        me = self.call("/api/suite/status")
        print(f"ok   login                      active model: "
              f"{(me.get('active') or {}).get('name') or 'bundled'}")
        return me

    # ---- jobs ------------------------------------------------------------
    def wait(self, job: dict, stage: str, seconds: float = 3600) -> dict:
        jid = (job.get("job") or job)["id"]
        t0 = time.time()
        while time.time() - t0 < seconds:
            snap = self.call(f"/api/jobs/{jid}?lines=8")
            if not snap["live"]:
                if snap["status"] != "ok":
                    tail = "\n".join((snap.get("tail") or "").splitlines()[-6:])
                    raise SystemExit(
                        f"FAIL {stage} ({snap['status']}): {snap.get('error')}\n{tail}")
                print(f"ok   {stage:<26} {snap['elapsed']:>6.1f}s  "
                      f"{json.dumps(snap.get('metrics') or {})[:90]}")
                return snap
            time.sleep(2.0)
        raise SystemExit(f"FAIL {stage}: still running after {seconds:.0f}s")

    # ---- stages ----------------------------------------------------------
    def stage_state(self, tag: str) -> str | None:
        """Health, project CRUD, database-backed artefact ownership, event feed."""
        health = self.call("/healthz")
        db, bus = health.get("database") or {}, health.get("bus") or {}
        assert db.get("ok") is not False, f"database unhealthy: {db}"
        print(f"ok   healthz                 db={db.get('kind')} ok={db.get('ok')} "
              f"bus={bus.get('kind')}")
        if not db.get("kind") or db.get("kind") == "disabled":
            print("     (database disabled: SUITE_DB=0 — skipping project checks)")
            return None
        slug = f"{tag}-proj"
        existing = {p["slug"] for p in
                    self.call("/api/suite/projects")["projects"]}
        if slug not in existing:
            self.call("/api/suite/projects",
                      {"slug": slug, "title": f"smoke {tag}", "tags": ["smoke"]})
        proj = self.call(f"/api/suite/projects/{slug}")["project"]
        assert proj["status"] == "development", proj
        self.call(f"/api/suite/projects/{slug}", {"status": "finished"}, method="PATCH")
        done = self.call(f"/api/suite/projects/{slug}")["project"]
        assert done["status"] == "finished", done
        self.call(f"/api/suite/projects/{slug}", {"status": "development"},
                  method="PATCH")
        # a bad status is a 400, not a crash
        try:
            self.call("/api/suite/projects",
                      {"slug": f"{tag}-bad", "status": "nope"})
        except SystemExit:                      # call() turns 4xx into SystemExit
            pass
        else:
            raise AssertionError("an invalid project status was accepted")
        print(f"ok   project {slug:<13} development -> finished -> development")
        return slug

    def stage_state_artefacts(self, project: str, dataset: str) -> None:
        """After the dataset exists: it must be owned and marked ready."""
        row = next((d for d in self.call("/api/suite/datasets")["datasets"]
                    if d["name"] == dataset), None)
        assert row is not None, f"{dataset} vanished from the database"
        assert row.get("project") == project, \
            f"{dataset} is not owned by {project}: {row.get('project')}"
        assert row.get("status") == "ready", \
            f"{dataset} should be ready after its job finished: {row.get('status')}"
        print(f"ok   dataset row             source={row.get('source')} "
              f"status={row.get('status')} project={row.get('project')}")
        state = self.call("/api/suite/state")
        assert state["counts"]["datasets"] >= 1, state["counts"]
        events = self.call("/api/suite/events?limit=20")["events"]
        kinds = {e.get("kind") for e in events}
        assert kinds & {"job.finished", "job.queued"}, f"no job events: {kinds}"
        print(f"ok   state                   counts={state['counts']} "
              f"events={sorted(kinds)[:4]}")

    def stage_dataset(self, name: str, train: int, val: int,
                      project: str | None = None) -> None:
        ds = self.call("/api/suite/datasets")
        if any(d["name"] == name for d in ds["datasets"]):
            print(f"ok   dataset {name} exists (reused)")
            return
        job = self.call("/api/suite/datasets/synth",
                        {"name": name, "train": train, "val": val, "seed": 1,
                         "level": 1.0, "project": project})
        self.wait(job, f"synth {name}")

    def stage_merge(self, name: str, sources: list[str]) -> None:
        ds = self.call("/api/suite/datasets")
        if any(d["name"] == name for d in ds["datasets"]):
            print(f"ok   dataset {name} exists (reused)")
            return
        job = self.call("/api/suite/datasets/merge", {"name": name, "sources": sources})
        self.wait(job, f"merge -> {name}")

    def stage_train(self, dataset: str, run: str, epochs: int, imgsz: int,
                    batch: int) -> dict:
        defaults = self.call("/api/suite/train/defaults")
        runs = {r["name"]: r for r in defaults["runs"]}
        if run in runs and runs[run].get("has_weights"):
            print(f"ok   run {run} already trained (reused)")
            return runs[run]
        sugg = defaults["suggested"]
        job = self.call("/api/suite/train", {
            "dataset": dataset, "name": run,
            "epochs": epochs or sugg["epochs"], "imgsz": imgsz or sugg["imgsz"],
            "batch": batch or sugg["batch"], "device": sugg["device"],
            "workers": sugg["workers"], "model": sugg["model"],
        })
        return self.wait(job, f"train {run} ({epochs} ep @{imgsz})")

    def stage_evaluate(self, run: str, dataset: str, n: int) -> None:
        job = self.call(f"/api/suite/runs/{run}/evaluate",
                        {"n": n, "imgsz": 448, "levels": "0.2,0.6", "dataset": dataset})
        self.wait(job, f"evaluate {run}")
        report = self.call(f"/api/suite/runs/{run}/eval")
        for level, modes in report.get("levels", {}).items():
            cells = []
            for mode, v in modes.items():
                if isinstance(v, dict):
                    rate = v.get("decode_rate")
                    rate = f"{100 * float(rate):.0f}%" if isinstance(rate, (int, float)) else "?"
                    cells.append(f"{mode}={rate}")
                else:
                    cells.append(f"{mode}={v}")
            print(f"     level {level}: " + "  ".join(cells))

    def stage_promote(self, run: str) -> None:
        out = self.call("/api/suite/models/promote", {"run": run})
        print(f"ok   promote {run:<14} active={out['active']['name']}")

    def stage_transfer(self, text: str) -> dict:
        tx = self.call("/api/suite/s2c/transmit", {
            "text": text, "name": "smoke.txt", "modulation": "rgb8",
            "ecc": "rs32", "resolution": "1280x720"})
        t = tx["transfer"]
        print(f"ok   transmit                   {t['frames']} frames, {t['bytes']} B, "
              f"{t['cells']}² cells @{t['cell_px']}px, {t['payload_per_frame']} B/frame")
        return t

    def stage_loopback(self, tid: str, level: float = 0.2) -> None:
        self.call(f"/api/suite/s2c/{tid}/reset", {})
        rep = self.call(f"/api/suite/s2c/{tid}/loopback", {"level": level, "frames": 2})
        print(f"ok   loopback @L{level}            decoded {rep['decoded']}/"
              f"{len(rep['frames'])} frames, corner err {rep['corner_err_median']}")
        for row in rep["frames"]:
            print(f"     frame {row['frame']}: ok={row['ok']} "
                  f"ring={row['ring_score']} {row['reason'] or ''}")

    def stage_phone_path(self, tid: str) -> None:
        """The receiving phone: download the displayed frame, upload it back."""
        png = self.call(f"/api/suite/s2c/{tid}/frame.png?index=0")
        boundary = "----smoke"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                "filename=\"shot.png\"\r\nContent-Type: image/png\r\n\r\n").encode()
        body += png + f"\r\n--{boundary}--\r\n".encode()
        res = self.call(f"/api/suite/s2c/{tid}/receive", raw=body,
                        ctype=f"multipart/form-data; boundary={boundary}")
        ok = bool(res.get("decode", {}).get("ok"))
        print(f"ok   receive (photo upload)     decoded={ok} "
              f"complete={res['result'].get('complete')} "
              f"received={res['transfer'].get('received')}")
        if res["result"].get("complete"):
            blob = self.call(f"/api/suite/s2c/{tid}/payload")
            print(f"ok   payload download           {len(blob)} bytes "
                  f"sha1_ok={res['result'].get('sha1_ok')}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="end-to-end smoke test of the optical suite")
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--password", default=None, help="default: $APP_PASSWORD")
    ap.add_argument("--tag", default=time.strftime("smoke-%H%M"))
    ap.add_argument("--synth-train", type=int, default=40)
    ap.add_argument("--synth-val", type=int, default=12)
    ap.add_argument("--train-epochs", type=int, default=2)
    ap.add_argument("--train-imgsz", type=int, default=320)
    ap.add_argument("--train-batch", type=int, default=2)
    ap.add_argument("--eval-n", type=int, default=6)
    ap.add_argument("--skip-train", action="store_true")
    args = ap.parse_args(argv)

    import os

    password = args.password or os.environ.get("APP_PASSWORD")
    if not password:
        raise SystemExit("pass --password or set APP_PASSWORD")

    s = Suite(args.base, password)
    before = (s.login().get("active") or {}).get("name")
    project = s.stage_state(args.tag)
    s.stage_dataset(f"{args.tag}-synth", args.synth_train, args.synth_val,
                    project=project)
    if project:
        s.stage_state_artefacts(project, f"{args.tag}-synth")
    s.stage_merge(f"{args.tag}-train", [f"{args.tag}-synth"])
    if args.skip_train:
        run = (s.call("/api/suite/status").get("active") or {}).get("name")
    else:
        s.stage_train(f"{args.tag}-train", f"{args.tag}-run",
                      args.train_epochs, args.train_imgsz, args.train_batch)
        s.stage_evaluate(f"{args.tag}-run", f"{args.tag}-train", args.eval_n)
        s.stage_promote(f"{args.tag}-run")
        run = f"{args.tag}-run"
    if before and before != run:
        # leave the box as we found it: a 1-epoch smoke model is not a detector
        s.call(f"/api/suite/models/{before}/activate", {})
        print(f"ok   reactivate {before:<13} (smoke model stays available to inspect)")
    transfer = s.stage_transfer("hello from the screen, over photons")
    s.stage_loopback(transfer["id"])
    s.stage_phone_path(transfer["id"])
    print(f"\nPASS  model in use: {run or 'bundled'}")
    print("      open /s2c on the laptop and /camera on the phone to try the real thing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
