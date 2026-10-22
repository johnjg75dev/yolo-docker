"""Collect the numbers behind ``docs/BENCHMARKS.md`` into ``docs/bench_data.json``.

Everything is CPU only.  The cost is dominated by the receiver's deep pose
search on captures that will not decode, so the default sample sizes are
deliberately small and honest about their binomial error (``n=4`` per cell is
+-25 points).

    python scripts/collect_bench_data.py              # defaults (~10-20 min)
    python scripts/collect_bench_data.py --quick      # 3 levels, n=3 (minutes)
    python scripts/collect_bench_data.py --only robustness,transfer

Sections
--------
robustness   success vs impairment level for classical / yolo26 / auto
modulations  payload capacity and success for all 13 modulations
eccs         payload capacity and success for every FEC profile
transfer     lossy multi-frame file transfer with resume
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import time
from pathlib import Path

import numpy as np

from optical_modem.bench import bench_robustness, make_trial
from optical_modem.config import MODULATIONS, ModemConfig
from optical_modem.phy.channel import ChannelConfig
from optical_modem.protocol.packet import DecodedFrame, Packetizer
from optical_modem.receiver import HybridReceiver
from optical_modem.sim import capture, prepare_frame

WEIGHTS = "models/yolo26n_optical/weights/best.pt"
OUT = Path("docs/bench_data.json")
RESOLUTION = "1280x720"

LEVELS = (0.0, 0.1, 0.2, 0.35, 0.5)
LEVELS_QUICK = (0.0, 0.2, 0.5)
ALL_SECTIONS = ("robustness", "modulations", "eccs", "transfer")


def cfg_for(mod: str, ecc: str = "rs16", resolution: str = RESOLUTION) -> ModemConfig:
    """The config the benchmark measures.

    Auto-detection is off on purpose: these sections measure *one* modulation,
    and detection scores all 13 of them against every candidate quad.
    """
    return dataclasses.replace(
        ModemConfig.for_resolution(resolution, modulation=mod, ecc=ecc),
        auto_detect_modulation=False,
    )


# --------------------------------------------------------------------------- #
# sections
# --------------------------------------------------------------------------- #
def collect_robustness(levels, n, resolution=RESOLUTION, modulation="rgb8",
                       ecc="rs16", weights=WEIGHTS, seed=11):
    cfg = cfg_for(modulation, ecc, resolution)
    rows = bench_robustness(levels, n=n, modes=("classical", "yolo", "auto"),
                            cfg=cfg, weights=weights, seed=seed)
    return {
        "level_s": list(levels),
        "n": n,
        "modulation": modulation,
        "ecc": ecc,
        "resolution": resolution,
        "rows": [{"mode": r.mode, "level": r.level, "success": r.success,
                  "ms": r.ms_mean, "payload_bytes": r.payload_bytes,
                  "ring_mean": r.ring_mean} for r in rows],
    }


def _rate(cfg, levels, n, rng, mode="classical"):
    payload, screen, _ = make_trial(cfg, None, rng)
    rx = HybridReceiver(cfg, mode=mode)
    rates = {}
    for level in levels:
        ok = 0
        for _ in range(n):
            ch = ChannelConfig.random(rng, level)
            if cfg.display is not None:
                ch.out_size = 1080
            img, _ = capture(cfg, screen, ch, rng)
            rep = rx.decode(img)
            ok += bool(rep.ok and rep.payload == payload)
        rates[f"{level:.2f}"] = ok / n
    return len(payload), rates


def collect_modulations(levels=(0.10, 0.25), n=4, resolution=RESOLUTION,
                        ecc="rs16", seed=31):
    rows = []
    for mod in MODULATIONS:
        cfg = cfg_for(mod, ecc, resolution)
        n_bytes, rates = _rate(cfg, levels, n, np.random.default_rng(seed))
        rows.append({"modulation": mod, "bits_per_cell": cfg.mod().bits_per_cell,
                     "n_bytes": n_bytes, "rates": rates})
        print(f"[collect] {mod}: {rates}", flush=True)
    return {"levels_s": list(levels), "n": n, "ecc": ecc,
            "resolution": resolution, "rows": rows}


def collect_eccs(levels=(0.25, 0.45), n=4, resolution=RESOLUTION,
                 modulation="rgb8", seed=41,
                 profiles=("rs8", "rs16", "rs32", "rs64", "ldpc12", "ldpc23",
                           "none")):
    rows = []
    for ecc in profiles:
        cfg = cfg_for(modulation, ecc, resolution)
        n_bytes, rates = _rate(cfg, levels, n, np.random.default_rng(seed))
        rows.append({"ecc": ecc, "max_payload": n_bytes, "rates": rates})
        print(f"[collect] {ecc}: {rates}", flush=True)
    return {"levels_s": list(levels), "n": n, "ecc": list(profiles),
            "modulation": modulation, "resolution": resolution, "rows": rows}


def collect_transfer(level=0.35, drop=0.35, n_bytes=4000, rounds_max=5,
                     resolution=RESOLUTION, modulation="rgb8", ecc="rs16",
                     seed=7):
    cfg = cfg_for(modulation, ecc, resolution)
    rng = np.random.default_rng(seed)
    data = bytes(rng.integers(0, 256, n_bytes, dtype=np.uint8))
    frames = list(Packetizer(cfg).frames_for(data))
    rx = HybridReceiver(cfg, mode="classical")
    sink = Packetizer(cfg)
    delivered, result, pending, rounds = 0, None, list(frames), 0
    t0 = time.perf_counter()
    while pending and rounds < rounds_max:
        rounds += 1
        lost = []
        for hdr, body in pending:
            ch = ChannelConfig.random(rng, level)
            if cfg.display is not None:
                ch.out_size = 1080
            img, _ = capture(cfg, prepare_frame(cfg, hdr, body), ch, rng)
            rep = rx.decode(img)
            if rep.ok and rep.payload == body:
                delivered += 1
                result = sink.push(DecodedFrame(rep.results[0].header,
                                                rep.payload, True, 0, True)
                                   ) or result
            else:
                lost.append((hdr, body))
            if rng.random() < drop:
                lost.append((hdr, body))
        pending = lost
        print(f"[collect] transfer round {rounds}: {len(pending)} pending",
              flush=True)
    dt = time.perf_counter() - t0
    return {"bytes": len(data), "frames": len(frames), "rounds": rounds,
            "seconds": round(dt, 1), "delivered": delivered,
            "complete": result == data,
            "bytes_per_s": round(len(data) / dt, 1), "level": level,
            "drop": drop, "modulation": modulation, "ecc": ecc,
            "resolution": resolution}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true", help="3 levels, n=3")
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--levels", default=None,
                    help="comma separated impairment levels for section 1")
    ap.add_argument("--only", default=",".join(ALL_SECTIONS))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    n = 3 if args.quick else args.n
    levels = (LEVELS_QUICK if args.quick else LEVELS)
    if args.levels:
        levels = tuple(float(v) for v in args.levels.split(","))
    sections = [s.strip() for s in args.only.split(",") if s.strip()]

    out = Path(args.out)
    data = json.loads(out.read_text()) if out.exists() else {}
    data["meta"] = {"generated": time.strftime("%Y-%m-%d %H:%M"),
                    "n": n, "platform": "CPU only, 2 cores, no GPU"}

    t0 = time.time()
    if "robustness" in sections:
        data["robustness"] = collect_robustness(levels, n)
        print(f"[collect] robustness done ({time.time() - t0:.0f}s)", flush=True)
    if "modulations" in sections:
        data["modulations"] = collect_modulations(n=n)
        print(f"[collect] modulations done ({time.time() - t0:.0f}s)", flush=True)
    if "eccs" in sections:
        data["eccs"] = collect_eccs(n=n)
        print(f"[collect] eccs done ({time.time() - t0:.0f}s)", flush=True)
    if "transfer" in sections:
        data["transfer"] = collect_transfer()
        print(f"[collect] transfer done ({time.time() - t0:.0f}s)", flush=True)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1) + "\n")
    print(f"wrote {out} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
