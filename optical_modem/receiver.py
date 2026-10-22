"""Unified receiver: YOLO26 localisation + shared demodulation/FEC.

``HybridReceiver`` is what the GUI, CLI and benchmark use.

* ``mode="yolo"``     – YOLO26 finds the frame, classical code decodes it.
* ``mode="classical"``– contour detection (no neural network needed).
* ``mode="auto"``     – YOLO first, classical contours as a fallback.

In every case the demodulator is identical, which is what makes the benchmark
fair: the only thing that changes is *where the four corners come from*.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from .config import ModemConfig
from .phy.classical import ClassicalReceiver, FrameResult, QuadDecoder
from .phy.locate import Quad


@dataclass
class ReceiveReport:
    results: list[FrameResult] = field(default_factory=list)
    detections: list = field(default_factory=list)
    mode: str = ""
    timings: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return any(r.ok for r in self.results)

    @property
    def payload(self) -> bytes:
        for r in self.results:
            if r.ok:
                return r.payload
        return b""

    def best(self) -> FrameResult | None:
        return self.results[0] if self.results else None


class HybridReceiver:
    def __init__(self, cfg: ModemConfig | None = None, mode: str = "auto",
                 yolo_weights: str | None = None, yolo_imgsz: int = 448,
                 device: str = "cpu", refine: bool = True, min_ring: float = 0.55):
        self.cfg = cfg or ModemConfig()
        self.mode = mode
        self.decoder = QuadDecoder(self.cfg, refine=refine)
        self.classical = ClassicalReceiver(self.cfg, refine=refine, min_ring=min_ring)
        self.yolo = None
        self.yolo_error: str | None = None
        if mode in ("auto", "yolo"):
            from .detect.yolo import YoloFrameDetector

            try:
                self.yolo = YoloFrameDetector(
                    weights=yolo_weights or "models/yolo26n_optical/weights/best.pt",
                    imgsz=yolo_imgsz, device=device,
                )
            except Exception as exc:
                if mode == "yolo":
                    raise
                self.yolo_error = str(exc)

    # ---- public ----------------------------------------------------------
    def decode(self, img: np.ndarray, modulations: list[str] | None = None,
               max_attempts: int = 6) -> ReceiveReport:
        t0 = time.perf_counter()
        report = ReceiveReport(mode=self.mode)
        timings = {}

        quads: list[Quad] = []
        seeds: list[Quad] | None = None
        if self.yolo is not None and self.mode in ("auto", "yolo"):
            td = time.perf_counter()
            dets = self.yolo.detect(img)
            timings["yolo"] = time.perf_counter() - td
            report.detections = dets
            seeds = [d.quad for d in dets] or None
            # the pose head localises the frame to ~1 cell, which is not enough
            # to sample a 32x32 grid: use it as a region seed and let the
            # ring-scored hypotheses below resolve the exact lattice
            if self.mode == "yolo":
                quads.extend(seeds or [])

        if self.mode in ("auto", "classical"):
            tc = time.perf_counter()
            cq = self.classical.quads_from_image(img, seeds=seeds)
            if self.mode == "auto":
                # also keep the unrestricted search: whichever hypothesis
                # decodes wins, so auto is never worse than either half
                cq = cq + self.classical.quads_from_image(img)
            timings["contours"] = time.perf_counter() - tc
            quads.extend(cq)
            if not cq and seeds:
                quads.extend(seeds)  # nothing found in the ROI: try as-is

        if not quads:
            report.timings = timings
            return report

        mods = modulations or (
            [self.cfg.modulation] if not self.cfg.auto_detect_modulation
            else None
        )
        # score YOLO quads first: they are usually far better than contours
        report.results = self.classical.decode(
            img, quads=quads, seeds=seeds, modulations=mods,
            max_attempts=max_attempts,
        )
        for r in report.results:
            r.detector = "yolo26" if r.detector == "classical" and self.yolo and report.detections else r.detector
        report.timings = {**timings, "total": time.perf_counter() - t0}
        return report

    # kept for API compatibility / tests
    def detect(self, img: np.ndarray):
        if self.yolo is None:
            return []
        return self.yolo.detect(img)
