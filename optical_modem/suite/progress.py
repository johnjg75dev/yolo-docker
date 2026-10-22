"""Guided-flow logic: where is this box, and what should the user do next?

One place decides the answer for the dashboard rail, the wizard, the nav ticks
and the CLI, so they can never disagree.  Everything here is derived from what is
actually on disk / in the database — no separate "onboarding" state to get stale.

A stage is *done* when the artefact it produces exists, not when a page was
visited.  A stage is *current* when it is the first unfinished one; stages after
it are ``blocked`` and the UI says why (e.g. "no dataset yet"), which is the
detail that makes a wizard feel like a guide instead of a menu.
"""
from __future__ import annotations

from typing import Any

#: How many synthetic frames the one-click dataset builds.  Big enough to train
#: something usable on a 4-core box (~2 min), small enough to be a fair first
#: step.  The Train page uses the same number as its default.
SYNTH_TRAIN, SYNTH_VAL = 120, 24

STAGES: list[dict[str, Any]] = [
    {
        "id": "env",
        "title": "Check the box",
        "short": "box",
        "icon": "chip",
        "href": "/guide#env",
        "why": "Every later step runs torch/ultralytics in a subprocess. If they are "
               "missing, the Train button fails 20 s after you press it — cheaper "
               "to find out now.",
        "cta": "See what this box has",
    },
    {
        "id": "project",
        "title": "Name the work",
        "short": "project",
        "icon": "folder",
        "href": "/projects",
        "why": "A project is the durable record: datasets, runs, models and transfers "
               "are filed under it, and it is either in development or finished. "
               "Two minutes now saves 'which run was the good one?' later.",
        "cta": "Create a project",
    },
    {
        "id": "dataset",
        "title": "Build a dataset",
        "short": "data",
        "icon": "grid",
        "href": "/dataset",
        "why": "The detector learns to find the four grid corners from examples. "
               "Synthetic frames are instant and cover geometry; phone photos add "
               "real blur, glare and colour shift.",
        "cta": "Create a dataset",
    },
    {
        "id": "train",
        "title": "Train the detector",
        "short": "train",
        "icon": "brain",
        "href": "/train",
        "why": "Fine-tune yolo26n-pose on your dataset. CPU-only is fine: the model "
               "is small, and 448 px is the resolution the receiver runs at.",
        "cta": "Start training",
    },
    {
        "id": "verify",
        "title": "Verify and promote",
        "short": "promote",
        "icon": "check",
        "href": "/models",
        "why": "Promoting makes a run the *active* model: the one the S2C receiver "
               "and the capture auto-labeller use. Verify first — corner error and "
               "decode rate are the two numbers that matter.",
        "cta": "Promote a run",
    },
    {
        "id": "send",
        "title": "Send something",
        "short": "send",
        "icon": "signal",
        "href": "/s2c",
        "why": "The point of the whole pipeline: bytes leave the laptop screen as "
               "coloured cells and arrive through a camera. Phones need HTTPS for "
               "the camera, so use the deployed URL, not localhost.",
        "cta": "Open the S2C app",
    },
]

#: one-line explanations for values shown in the rail / wizard
ICONS = {s["id"]: s["icon"] for s in STAGES}


def _stage(stage: dict, done: bool, detail: str, checklist: list[dict],
           action: dict | None = None, blockers: list[str] | None = None) -> dict:
    return {
        "id": stage["id"], "title": stage["title"], "short": stage["short"],
        "icon": stage["icon"], "href": stage["href"], "why": stage["why"],
        "cta": stage["cta"], "detail": detail, "done": bool(done),
        "checklist": checklist, "action": action,
        "blockers": blockers or [],
    }


def build(*, env: dict, datasets: list[dict], runs: list[dict], models: list[dict],
          active: dict | None, projects: list[dict], transfers: list[dict],
          sessions: list[dict], jobs: list[dict], db_enabled: bool = True,
          suggested: dict | None = None) -> dict:
    """Return ``{stages, next, percent, done, summary}`` for this box."""
    images = sum(int(d.get("images") or 0) for d in datasets)
    with_weights = [r for r in runs if r.get("has_weights")]
    finished_projects = [p for p in projects if p.get("status") == "finished"]
    complete_transfers = [t for t in transfers if t.get("complete")]
    shots = sum(int(s.get("accepted") or 0) for s in sessions)
    live = [j for j in jobs if j.get("live")]
    last_job = jobs[0] if jobs else None

    stages: list[dict] = []

    # ---- 1. environment ---------------------------------------------------
    torch_ok = bool(env.get("torch"))
    ult_ok = bool(env.get("ultralytics"))
    free_ok = env.get("disk_free_bytes") is None or env["disk_free_bytes"] > 3e9
    stages.append(_stage(
        STAGES[0], torch_ok and ult_ok,
        f"torch {env.get('torch') or '—'} · ultralytics {env.get('ultralytics') or '—'}",
        [{"label": "torch importable", "done": torch_ok},
         {"label": "ultralytics importable", "done": ult_ok},
         {"label": "≥ 3 GB free for a training run", "done": free_ok},
         {"label": f"{int(env.get('cpu_count') or 0)} CPU core(s) detected",
          "done": bool(env.get("cpu_count"))}],
        action={"kind": "link", "href": "/guide#env"}))

    # ---- 2. project -------------------------------------------------------
    if not db_enabled:
        detail = "database disabled (SUITE_DB=0) — projects unavailable"
    elif projects:
        detail = (f"{len(projects)} project(s), {len(finished_projects)} finished: "
                  + ", ".join(p["slug"] for p in projects[:3]))
    else:
        detail = "no project yet — artefacts will be unassigned"
    stages.append(_stage(
        STAGES[1], bool(projects), detail,
        [{"label": "a project exists", "done": bool(projects)},
         {"label": "one is marked finished", "done": bool(finished_projects)}],
        action={"kind": "link", "href": "/projects"} if projects else
               {"kind": "link", "href": "/projects", "label": "Create the first project"}))

    # ---- 3. dataset -------------------------------------------------------
    merged = [d for d in datasets
              if str(d.get("name", "")).startswith(("train", "merged", "merge"))]
    real = [d for d in datasets if int(d.get("images") or 0) and
            (d.get("source") in ("captures",) or "capt" in str(d.get("name", "")))]
    stages.append(_stage(
        STAGES[2], images > 0,
        f"{images} labelled image(s) in {len(datasets)} dataset(s)"
        + (f", {shots} phone shots captured" if shots else ""),
        [{"label": "any dataset with images", "done": images > 0},
         {"label": "a merged dataset to train on", "done": bool(merged) or
          any(int(d.get("images") or 0) >= 30 for d in datasets)},
         {"label": "phone captures added (optional, more robust)",
          "done": bool(shots) or bool(real)}],
        action={"kind": "post", "endpoint": "/api/suite/datasets/synth",
                "label": f"Generate {SYNTH_TRAIN}+{SYNTH_VAL} synthetic frames",
                "payload": {"train": SYNTH_TRAIN, "val": SYNTH_VAL, "seed": 1,
                            "level": 1.0},
                "then": "/dataset"} if images == 0 else
               {"kind": "link", "href": "/dataset", "label": "Inspect the dataset"}))

    # ---- 4. train ---------------------------------------------------------
    best = None
    for r in with_weights:
        v = (r.get("metrics") or {}).get("box_map50_95")
        if v is not None and (best is None or v > best):
            best = v
    train_detail = (f"{len(with_weights)} run(s) with weights"
                    + (f" · best box mAP50-95 {best:.3f}" if best is not None else "")
                    ) if with_weights else "no trained run yet"
    plan = dict(suggested or {})
    train_payload: dict[str, Any] = {}
    if merged or datasets:
        train_payload["dataset"] = (merged or datasets)[0]["name"]
        for key in ("epochs", "imgsz", "batch", "workers", "device", "model"):
            if plan.get(key) is not None:
                train_payload[key] = plan[key]
    stage_train = _stage(
        STAGES[3], bool(with_weights), train_detail,
        [{"label": "a training run finished", "done": bool(with_weights)},
         {"label": "weights/best.pt exists", "done": bool(with_weights)},
         {"label": "box mAP50-95 ≥ 0.5", "done": bool(best is not None and best >= 0.5)}],
        action={"kind": "post", "endpoint": "/api/suite/train",
                "label": (f"Train {train_payload['dataset']} — "
                          f"{train_payload.get('epochs', 40)} epochs @ "
                          f"{train_payload.get('imgsz', 448)}px"),
                "payload": train_payload, "then": "/train"} if train_payload else
               {"kind": "link", "href": "/dataset", "label": "Build a dataset first"})
    stages.append(stage_train)

    # ---- 5. verify / promote ---------------------------------------------
    stages.append(_stage(
        STAGES[4], bool(active),
        f"active model: {active['name']}" if active else "no model promoted yet",
        [{"label": "a run with weights exists", "done": bool(with_weights)},
         {"label": "a model is active", "done": bool(active)},
         {"label": "the S2C loopback decoded a frame",
          "done": any(t.get("received") for t in transfers)}],
        action={"kind": "link", "href": "/models", "label": "Choose the model to promote"}
        if not active else {"kind": "link", "href": "/models", "label": "See the registry"}))

    # ---- 6. send ----------------------------------------------------------
    stages.append(_stage(
        STAGES[5], bool(complete_transfers),
        (f"{len(complete_transfers)} complete transfer(s), "
         f"{len(transfers)} total") if transfers else "nothing sent yet",
        [{"label": "a transfer was created", "done": bool(transfers)},
         {"label": "a frame was decoded (loopback or phone)", "done":
          any(t.get("received") for t in transfers)},
         {"label": "a payload arrived complete and checksum-matched",
          "done": bool(complete_transfers)}],
        action={"kind": "link", "href": "/s2c", "label": "Open the S2C app"}))

    done_count = sum(1 for s in stages if s["done"])
    first_open = next((i for i, s in enumerate(stages) if not s["done"]), None)
    for i, s in enumerate(stages):
        s["current"] = (i == first_open)
        s["blocked"] = bool(first_open is not None and i > first_open)
    nxt = stages[first_open] if first_open is not None else None
    if nxt is not None:
        # the reason the *following* stage is not actionable yet, said plainly
        blockers = []
        for s in stages[first_open + 1:]:
            blockers.append(f"{s['title']}: {s['detail']}")
        nxt = {**nxt, "blocked_by": blockers[:3]}

    return {
        "stages": stages,
        "next": nxt,
        "percent": round(100 * done_count / len(stages)),
        "done": done_count,
        "total": len(stages),
        "complete": first_open is None,
        "counts": {"datasets": len(datasets), "images": images, "runs": len(runs),
                   "models": len(models), "projects": len(projects),
                   "transfers": len(transfers), "shots": shots,
                   "complete_transfers": len(complete_transfers),
                   "jobs_running": len(live)},
        "last_job": ({k: last_job.get(k) for k in
                      ("id", "kind", "title", "status", "progress", "live")}
                     if last_job else None),
    }
