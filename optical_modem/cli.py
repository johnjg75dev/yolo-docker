"""Command line interface.

    python -m optical_modem info
    python -m optical_modem render --text "hello" --out frame.png
    python -m optical_modem render --resolution 1920x1080 --modulation rgb32t16
    python -m optical_modem sim --level 0.6 --n 20            # TX->camera->RX
    python -m optical_modem receive capture.png
    python -m optical_modem send --file README.md --ecc rs16  # render a sequence
    python -m optical_modem bench --out docs/BENCHMARKS.md
    python -m optical_modem gui --port 8000
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def _cfg(a):
    """Build a config from CLI flags.

    ``--resolution`` switches to the display path: the symbol grid is fitted to
    the screen and ``--cell-px`` becomes the *symbol scale factor* (pixels per
    cell), which is what a person fiddling with the transmitter actually
    controls.
    """
    from .config import FrameLayout, ModemConfig

    resolution = getattr(a, "resolution", None)
    if resolution and resolution.lower() not in ("", "none", "frame"):
        cfg = ModemConfig.for_resolution(
            resolution, modulation=a.modulation, ecc=a.ecc,
            symbol_px=a.cell_px or 12, cells=getattr(a, "grid", None) or None,
        )
    else:
        cfg = ModemConfig(modulation=a.modulation, ecc=a.ecc)
        if getattr(a, "grid", None):
            m = cfg.mod()
            cfg.layout = FrameLayout(cells=a.grid, cps=m.cells_per_symbol,
                                     ring=1, header_rows=4)
        if getattr(a, "cell_px", None):
            cfg.cell_px = a.cell_px
    cfg.interleave = not a.no_interleave
    return cfg


# --------------------------------------------------------------------------- #
def cmd_info(a):
    from .config import ECC_PROFILES, MODULATIONS, ModemConfig
    from .config import DisplayProfile, pattern_min_distance, min_palette_distance

    print("modulation  colours  patterns  bits/cell  d_colour  d_pattern")
    for name, m in MODULATIONS.items():
        print(f"{name:10s} {m.n_colors:8d} {m.n_patterns:9d} {m.bits:10.1f} "
              f"{min_palette_distance(m.palette):9.1f} "
              f"{(pattern_min_distance(m.pattern_mask) if m.pattern_bits else 0):10d}"
              f"{'  (dp)' if m.pair else ''}")

    print("\nstandalone frame capacity (payload bytes per frame)")
    print(f"{'modulation':10s} " + " ".join(f"{e:>8s}" for e in ECC_PROFILES))
    for mod in MODULATIONS:
        cells = []
        for ecc in ECC_PROFILES:
            d = ModemConfig(modulation=mod, ecc=ecc).describe()
            cells.append(f"{d['max_user_payload']:8d}")
        print(f"{mod:10s} " + " ".join(cells))

    for res in ("1280x720", "1920x1080", "2560x1440", "3840x2160"):
        disp = DisplayProfile.parse(res)
        n_cells, px = disp.fit()
        print(f"\n{disp.label}: {n_cells}x{n_cells} cells at {px} px  (symbol scale)")
        print(f"{'modulation':10s} {'cells':>5s} {'px':>3s} {'payload B':>10s} {'kB/s @10fps':>12s}")
        for mod in MODULATIONS:
            cfg = ModemConfig.for_resolution(res, modulation=mod)
            d = cfg.describe()
            payload = d["max_user_payload"]
            print(f"{mod:10s} {d['cells']:5d} {cfg.cell_px:3d} {payload:10d} "
                  f"{payload * 10 / 1024:12.1f}")
    return 0


def cmd_render(a):
    import cv2

    from .protocol.packet import FrameHeader
    from .sim import prepare_frame

    cfg = _cfg(a)
    payload = a.text.encode() if a.text else bytes(range(64))
    mod = cfg.mod()
    d = cfg.describe()
    hdr = FrameHeader(mod.mod_id, cfg.ecc_profile().ecc_id, 0, a.seq,
                      len(payload) + 2)
    img = prepare_frame(cfg, hdr, payload[: d["max_user_payload"]])
    cv2.imwrite(a.out, img)
    where = f" on {cfg.display.label}" if cfg.display else ""
    print(f"wrote {a.out}  ({img.shape[1]}x{img.shape[0]}{where}, "
          f"{d['cells']}x{d['cells']} cells @ {cfg.cell_px} px, "
          f"{d['max_user_payload']} B/frame, {a.modulation}/{a.ecc})")
    return 0


def cmd_receive(a):
    import cv2

    from .phy.channel import ChannelConfig
    from .receiver import HybridReceiver

    img = cv2.imread(a.image)
    if img is None:
        print(f"cannot read {a.image}", file=sys.stderr)
        return 2
    rx = HybridReceiver(_cfg(a), mode=a.mode, yolo_weights=a.weights,
                        yolo_imgsz=a.imgsz)
    rep = rx.decode(img)
    print(json.dumps({
        "ok": rep.ok,
        "results": [r.as_dict() for r in rep.results],
        "detections": len(rep.detections),
        "timings_ms": {k: round(v * 1000, 1) for k, v in rep.timings.items()},
    }, indent=1))
    if rep.ok and a.out_payload:
        Path(a.out_payload).write_bytes(rep.payload)
        print(f"payload written to {a.out_payload}")
    return 0 if rep.ok else 1


def cmd_sim(a):
    from .phy.channel import ChannelConfig
    from .protocol.packet import FrameHeader
    from .receiver import HybridReceiver
    from .sim import capture, prepare_frame

    cfg = _cfg(a)
    rng = np.random.default_rng(a.seed)
    cap = cfg.describe()["max_user_payload"]
    payload = bytes(rng.integers(0, 256, min(cap, a.bytes), dtype=np.uint8))
    hdr = FrameHeader(cfg.mod().mod_id, cfg.ecc_profile().ecc_id, 0, 1,
                      len(payload) + 2)
    frame = prepare_frame(cfg, hdr, payload)
    rx = HybridReceiver(cfg, mode=a.mode, yolo_weights=a.weights,
                        yolo_imgsz=a.imgsz)
    ok, times = 0, []
    for i in range(a.n):
        img, _ = capture(cfg, frame, ChannelConfig.random(rng, a.level), rng)
        rep = rx.decode(img)
        times.append(rep.timings.get("total", 0.0))
        if rep.ok and rep.payload == payload:
            ok += 1
    print(f"{a.mode:9s} level={a.level:.2f}  {ok}/{a.n} decoded  "
          f"{np.mean(times)*1000:.0f} ms/frame  "
          f"({cfg.modulation}/{cfg.ecc}, {len(payload)} B/frame)")
    return 0 if ok else 1


def cmd_send(a):
    """Render every frame of a file as PNG (the transmitter's job)."""
    import cv2

    from .protocol.packet import Packetizer
    from .sim import prepare_frame

    cfg = _cfg(a)
    data = Path(a.file).read_bytes()
    pkt = Packetizer(cfg)
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    n = 0
    for hdr, payload in pkt.frames_for(data):
        img = prepare_frame(cfg, hdr, payload)
        cv2.imwrite(str(outdir / f"frame_{n:05d}.png"), img)
        n += 1
    print(f"{len(data)} B -> {n} frames in {outdir} "
          f"({cfg.describe()['max_user_payload']} B/frame, {cfg.modulation}/{cfg.ecc})")
    return 0


def cmd_bench(a):
    from .bench import run_benchmark, write_report

    rows = run_benchmark(
        levels=[float(x) for x in a.levels.split(",")],
        n=a.n, modes=[m.strip() for m in a.modes.split(",")],
        weights=a.weights, imgsz=a.imgsz, seed=a.seed,
        with_display=not a.no_display, with_matrix=a.matrix,
        with_ablation=a.ablation, resolution=a.resolution,
        modulation=a.modulation,
    )
    write_report(rows, a.out)
    for r in rows:
        print(f"{r.mode:9s} {r.resolution:10s} {r.modulation:9s} {r.ecc:6s} "
              f"cells={r.cells:3d} L={r.level:.2f} ok={r.success*100:5.0f}% "
              f"{r.ms_mean:6.0f}ms {r.payload_bytes:6d}B")
    print(f"report written to {a.out}")
    return 0


def cmd_gui(a):
    from .gui.app import main as gui_main

    return gui_main(port=a.port, host=a.host)


def cmd_train(a):
    from .data.train_yolo import main as train_main

    return train_main(sys.argv[2:])


def cmd_dataset(a):
    from .data.synth import build_dataset

    print(json.dumps(build_dataset(a.out, a.train, a.val,
                                   (a.min_level, a.max_level),
                                   a.workers, a.seed), indent=1))
    return 0


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="optical_modem", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--modulation", default="rgb8")
        sp.add_argument("--ecc", default="rs16")
        sp.add_argument("--resolution", default="frame",
                        help="display to fit the code to: 1920x1080, 720p, 4k, "
                             "or 'frame' for a standalone square frame")
        sp.add_argument("--grid", type=int, default=None,
                        help="cells per side (default: whatever fits)")
        sp.add_argument("--cell-px", type=int, default=None, dest="cell_px",
                        help="symbol scale factor: pixels per cell")
        sp.add_argument("--no-interleave", action="store_true")
        sp.add_argument("--mode", default="auto",
                        choices=["auto", "yolo", "classical"])
        sp.add_argument("--weights", default="models/yolo26n_optical/weights/best.pt")
        sp.add_argument("--imgsz", type=int, default=448)

    sp = sub.add_parser("info", help="show the modulation/ECC capacity table")
    sp.set_defaults(func=cmd_info)

    sp = sub.add_parser("render", help="render one frame to an image")
    common(sp)
    sp.add_argument("--text", default="hello optical modem")
    sp.add_argument("--seq", type=int, default=0)
    sp.add_argument("--out", default="frame.png")
    sp.set_defaults(func=cmd_render)

    sp = sub.add_parser("receive", help="decode an image captured from a screen")
    common(sp)
    sp.add_argument("image")
    sp.add_argument("--out-payload", default=None)
    sp.set_defaults(func=cmd_receive)

    sp = sub.add_parser("sim", help="simulated TX -> camera -> RX run")
    common(sp)
    sp.add_argument("--level", type=float, default=0.6)
    sp.add_argument("--n", type=int, default=10)
    sp.add_argument("--bytes", type=int, default=200)
    sp.add_argument("--seed", type=int, default=0)
    sp.set_defaults(func=cmd_sim)

    sp = sub.add_parser("send", help="render a file as a sequence of frames")
    common(sp)
    sp.add_argument("--file", required=True)
    sp.add_argument("--out", default="frames")
    sp.set_defaults(func=cmd_send)

    sp = sub.add_parser("bench", help="run the receiver benchmark")
    sp.add_argument("--levels", default="0.0,0.3,0.6,0.9")
    sp.add_argument("--n", type=int, default=8)
    sp.add_argument("--modes", default="classical")
    sp.add_argument("--resolution", default="1920x1080")
    sp.add_argument("--modulation", default="rgb32")
    sp.add_argument("--no-display", action="store_true")
    sp.add_argument("--matrix", action="store_true")
    sp.add_argument("--ablation", action="store_true")
    sp.add_argument("--weights", default="models/yolo26n_optical/weights/best.pt")
    sp.add_argument("--imgsz", type=int, default=448)
    sp.add_argument("--seed", type=int, default=0)
    sp.add_argument("--out", default="docs/BENCHMARKS.md")
    sp.set_defaults(func=cmd_bench)

    sp = sub.add_parser("dataset", help="generate the synthetic training set")
    sp.add_argument("--out", default="datasets/optical")
    sp.add_argument("--train", type=int, default=1200)
    sp.add_argument("--val", type=int, default=200)
    sp.add_argument("--min-level", type=float, default=0.1)
    sp.add_argument("--max-level", type=float, default=1.2)
    sp.add_argument("--workers", type=int, default=None)
    sp.add_argument("--seed", type=int, default=1)
    sp.set_defaults(func=cmd_dataset)

    sp = sub.add_parser("train", help="train the YOLO26 frame detector")
    sp.set_defaults(func=cmd_train)

    sp = sub.add_parser("gui", help="launch the web GUI")
    sp.add_argument("--port", type=int, default=8889)
    sp.add_argument("--host", default="127.0.0.1")
    sp.set_defaults(func=cmd_gui)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    return a.func(a) or 0


if __name__ == "__main__":
    raise SystemExit(main())
