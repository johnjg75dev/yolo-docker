"""Generate the images used in the documentation.

    python scripts/make_assets.py            # all assets
    python scripts/make_assets.py --no-yolo  # skip model-dependent overlays
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from optical_modem.config import ModemConfig
from optical_modem.phy.channel import ChannelConfig, simulate
from optical_modem.phy.render import render_frame
from optical_modem.protocol.packet import FrameHeader, Packetizer

OUT = Path("docs/img")
WEIGHTS = Path("models/yolo26n_optical/weights/best.pt")


def save(name, img):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    cv2.imwrite(str(path), img)
    print("wrote", path)
    return path


def frame_pair(level=0.55, seed=3):
    cfg = ModemConfig(modulation="rgb8", ecc="rs16", cell_px=10)
    payload = b"optical modem: screen-to-camera transfer"
    hdr = FrameHeader(2, 2, 0, 1, len(payload) + 2)
    frame = render_frame(cfg, hdr, payload)
    save("frame_clean.png", frame)
    rng = np.random.default_rng(seed)
    ch = ChannelConfig.random(rng, level)
    ch.out_size = 640
    cam, quad = simulate(frame, ch, rng)
    save("frame_capture.png", cam)
    return cfg, frame, cam, quad


def detection_overlay(cfg, cam):
    if not WEIGHTS.exists():
        print("skip overlay: no weights")
        return
    from optical_modem.detect.yolo import YoloFrameDetector

    det = YoloFrameDetector(weights=WEIGHTS, imgsz=448)
    dets = det.detect(cam)
    if not dets:
        print("skip overlay: nothing detected")
        return
    vis = det.overlay(cam, dets)
    save("frame_detected.png", vis)
    # zoom into the first cell block for the docs
    q = dets[0].quad.pts
    x0, y0 = q.min(axis=0).astype(int) - 4
    x1, y1 = q.max(axis=0).astype(int) + 4
    crop = vis[max(0, y0):y1, max(0, x0):x1]
    if crop.size:
        crop = cv2.resize(crop, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_NEAREST)
        save("frame_detected_zoom.png", crop)


def recovered_overlay(cfg, cam, payload_text="optical modem"):
    """Final decode: recovered grid quad + rectified grid, side by side."""
    from optical_modem.phy.classical import sample_at
    from optical_modem.receiver import HybridReceiver

    rx = HybridReceiver(cfg, mode="auto", yolo_weights=WEIGHTS, yolo_imgsz=448)
    rep = rx.decode(cam)
    vis = cam.copy()
    if rep.ok:
        q = np.asarray(rep.results[0].quad, np.int32)
        cv2.polylines(vis, [q.reshape(-1, 1, 2)], True, (0, 255, 0), 2)
        for i, (x, y) in enumerate(q):
            cv2.circle(vis, (int(x), int(y)), 5, (255, 0, 255), -1)
        cv2.putText(vis, f"decoded {rep.results[0].modulation}/{rep.results[0].ecc}"
                         f" ring={rep.results[0].ring_score:.2f}",
                    (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2, cv2.LINE_AA)
        # rectified grid next door
        grid = cfg.layout.grid
        ss = max(1, cam.shape[1] // grid)
        warped = sample_at(cam, rep.results[0].quad, grid, supersample=ss,
                           inset=0.0)
        big = cv2.resize(warped.astype(np.uint8), (cam.shape[1], cam.shape[1]),
                         interpolation=cv2.INTER_NEAREST)
        pair = np.hstack([vis, big])
    else:
        pair = vis
    save("frame_recovered.png", pair)
    return rep

def palette_chart():
    """Draw each palette with its bit patterns (modulation reference chart)."""
    from optical_modem.config import MODULATIONS

    rows, width = [], 760
    row_h = 74
    canvas = np.full((row_h * len(MODULATIONS) + 20, width, 3), 18, np.uint8)
    for r, (name, mod) in enumerate(MODULATIONS.items()):
        y = 10 + r * row_h
        cv2.putText(canvas, f"{name}  {mod.bits} bit/cell", (12, y + 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 220, 240), 1, cv2.LINE_AA)
        sw = 56
        for i, (rgb, label) in enumerate(zip(mod.palette, mod.labels)):
            x = 12 + i * (sw + 8)
            cv2.rectangle(canvas, (x, y + 26), (x + sw, y + 26 + sw),
                          (int(rgb[2]), int(rgb[1]), int(rgb[0])), -1)
            cv2.rectangle(canvas, (x, y + 26), (x + sw, y + 26 + sw), (60, 70, 80), 1)
            cv2.putText(canvas, format(int(label), f"0{mod.bits}b"), (x + 4, y + 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (150, 200, 255), 1, cv2.LINE_AA)
    save("palettes.png", canvas)


def transfer_gif(text="Optical modem demo!", level=0.45):
    """A short GIF of a multi-frame transmission with simulated captures."""
    from PIL import Image

    cfg = ModemConfig(modulation="rgb8", ecc="rs16", cell_px=8)
    data = text.encode()
    pkt = Packetizer(cfg)
    rng = np.random.default_rng(5)
    frames = []
    for hdr, body in pkt.frames_for(data):
        img = render_frame(cfg, hdr, body)
        cam, _ = simulate(img, ChannelConfig.random(rng, level), rng)
        frames.append(img)
        frames.append(cam)
    # ping-pong so the GIF loops smoothly
    seq = frames + frames[::-1]
    imgs = [Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)).resize((320, 320))
            for f in seq]
    OUT.mkdir(parents=True, exist_ok=True)
    gif = OUT / "transfer.gif"
    imgs[0].save(gif, save_all=True, append_images=imgs[1:], duration=320, loop=0)
    print("wrote", gif)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-yolo", action="store_true")
    a = ap.parse_args(argv)
    cfg, frame, cam, _ = frame_pair(level=0.5)
    if not a.no_yolo:
        detection_overlay(cfg, cam)
        recovered_overlay(cfg, cam)
    palette_chart()
    transfer_gif()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
