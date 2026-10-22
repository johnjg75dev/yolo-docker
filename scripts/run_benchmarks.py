"""Run the benchmark suite and write ``docs/BENCHMARKS.md``.

Two steps, so they can be repeated independently:

    python scripts/collect_bench_data.py     # measure -> docs/bench_data.json
    python scripts/render_benchmarks_md.py   # render  -> docs/BENCHMARKS.md

This wrapper runs both, and adds the optional per-stage ablation table
(section 3 of the older document), which is slow and off by default.

    python scripts/run_benchmarks.py              # everything (~10-20 min)
    python scripts/run_benchmarks.py --quick      # 3 levels, n=3
    python scripts/run_benchmarks.py --ablation    # include the stage ablation
"""
from __future__ import annotations

import argparse
import subprocess
import sys


def run(*cmd: str) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true", help="3 levels, n=3")
    ap.add_argument("--n", type=int, default=None)
    ap.add_argument("--only", default=None,
                    help="comma separated sections (robustness,modulations,eccs,transfer)")
    ap.add_argument("--ablation", action="store_true",
                    help="also run the per-stage ablation (very slow)")
    args = ap.parse_args()

    collect = [sys.executable, "scripts/collect_bench_data.py"]
    if args.quick:
        collect.append("--quick")
    if args.n:
        collect += ["--n", str(args.n)]
    if args.only:
        collect += ["--only", args.only]
    run(*collect)

    if args.ablation:
        run(sys.executable, "scripts/run_ablation.py")
    run(sys.executable, "scripts/render_benchmarks_md.py")


if __name__ == "__main__":
    main()
