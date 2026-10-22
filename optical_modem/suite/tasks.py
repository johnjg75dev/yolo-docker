"""Subprocess entry points for the suite's heavyweight work.

The web app never does this work in-process: it spawns a job (see
:mod:`optical_modem.suite.jobs`) that runs one of these commands, so a training
run or a 5 000-image synthesis never blocks the event loop and leaves no torch
allocations behind.

    # synthetic dataset (renders + camera-channel degradation)
    python -m optical_modem.suite.tasks synth --out /data/datasets/synth-001 \
        --train 300 --val 60 --seed 1

    # capture-station photos -> dataset
    python -m optical_modem.suite.tasks captures --data-dir /data \
        --out /data/datasets/real-001 --verified-only

    # merge several datasets into the one to train on
    python -m optical_modem.suite.tasks merge --out /data/datasets/train-001 \
        --source /data/datasets/synth-001 --source /data/datasets/real-001

    # train / evaluate are the existing scripts:
    python -m optical_modem.data.train_yolo --data .../data.yaml --epochs 40
    python scripts/evaluate.py --weights .../best.pt --out .../eval.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _print(obj) -> None:
    print(json.dumps(obj, indent=1), flush=True)


def cmd_synth(args) -> int:
    from .datasets import synth_dataset

    summary = synth_dataset(args.out, n_train=args.train, n_val=args.val,
                            seed=args.seed, level=args.level,
                            workers=args.workers)
    _print(summary)
    return 0


def cmd_captures(args) -> int:
    from .datasets import captures_dataset

    summary = captures_dataset(args.data_dir, args.out, val_frac=args.val_frac,
                               verified_only=args.verified_only,
                               min_score=args.min_score)
    _print(summary)
    return 0 if summary.get("written") else 1


def cmd_merge(args) -> int:
    from .datasets import merge_datasets

    summary = merge_datasets(args.source, args.out, move=args.move)
    _print(summary)
    return 0


def cmd_info(args) -> int:
    from .registry import Registry, describe_env

    reg = Registry(args.data_dir)
    _print({"env": describe_env(), "datasets": reg.list_datasets(),
            "models": reg.list_models(), "active": reg.active(),
            "disk": reg.disk()})
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("synth", help="generate a synthetic dataset")
    p.add_argument("--out", required=True)
    p.add_argument("--train", type=int, default=300)
    p.add_argument("--val", type=int, default=60)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--level", type=float, default=1.0,
                   help="channel severity multiplier (0.5 = clean, 1.3 = harsh)")
    p.add_argument("--workers", type=int, default=None)
    p.set_defaults(func=cmd_synth)

    p = sub.add_parser("captures", help="capture photos -> dataset")
    p.add_argument("--data-dir", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--val-frac", type=float, default=0.15)
    p.add_argument("--verified-only", action="store_true")
    p.add_argument("--min-score", type=float, default=0.0)
    p.set_defaults(func=cmd_captures)

    p = sub.add_parser("merge", help="merge datasets into one")
    p.add_argument("--out", required=True)
    p.add_argument("--source", action="append", required=True)
    p.add_argument("--move", action="store_true")
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("info", help="print the registry state as JSON")
    p.add_argument("--data-dir", default="/data")
    p.set_defaults(func=cmd_info)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
