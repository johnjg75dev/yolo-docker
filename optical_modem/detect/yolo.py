"""YOLO26 frame detector: neural localisation of the coded grid.

The model is a ``yolo26*-pose`` checkpoint fine-tuned on synthetic captures.
It predicts one object per frame (class ``optical_frame``) with four keypoints
that *are* the four corners of the coded grid, in the transmitter's own
orientation::

    kpt 0 = grid TL, 1 = grid TR, 2 = grid BR, 3 = grid BL

That single prediction replaces the whole contour-hypothesis stage of the
classical receiver: no threshold tuning, no aspect/angle heuristics, and it
works with partial occlusion, gradients and cluttered backgrounds.

The output is a list of :class:`~optical_modem.phy.locate.Quad` objects, so the
downstream chain (calibration-ring alignment, demodulation, FEC) is byte-for-byte
the same as in the classical path.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..phy.locate import Quad, order_quad


@dataclass
class Detection:
    quad: Quad
    confidence: float
    keypoints: np.ndarray  # (4,3) x,y,visibility in pixels
    bbox: np.ndarray  # (4,) x1,y1,x2,y2
    timings: dict = field(default_factory=dict)


class YoloFrameDetector:
    """Wraps an Ultralytics YOLO26 pose model as a frame localiser."""

    def __init__(self, weights: str | Path = "models/yolo26n_optical/weights/best.pt",
                 imgsz: int = 448, conf: float = 0.25, device: str = "cpu",
                 nms_free: bool = True, verbose: bool = False):
        try:
            from ultralytics import YOLO
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "ultralytics is required for the YOLO detector: pip install ultralytics"
            ) from exc
        self.weights = str(weights)
        if not Path(self.weights).exists():
            raise FileNotFoundError(
                f"YOLO weights not found: {self.weights}\n"
                "train one with: python -m optical_modem.data.train_yolo"
            )
        self.model = YOLO(self.weights)
        self.imgsz = imgsz
        self.conf = conf
        self.device = device
        self.nms_free = nms_free
        self.verbose = verbose
        self.last_timings: dict = {}

    # ---- inference -------------------------------------------------------
    def detect(self, img: np.ndarray, max_det: int = 4) -> list[Detection]:
        t0 = time.perf_counter()
        results = self.model.predict(
            img, imgsz=self.imgsz, conf=self.conf, device=self.device,
            max_det=max_det, verbose=self.verbose, nms=self.nms_free,
        )
        t_infer = time.perf_counter() - t0
        out: list[Detection] = []
        for r in results:
            if r.keypoints is None or r.boxes is None:
                continue
            kxy = r.keypoints.xy.cpu().numpy()  # (n,4,2)
            kconf = (
                r.keypoints.conf.cpu().numpy()
                if r.keypoints.conf is not None
                else np.ones(kxy.shape[:2], np.float32)
            )
            boxes = r.boxes.xyxy.cpu().numpy()
            scores = r.boxes.conf.cpu().numpy()
            for i in range(kxy.shape[0]):
                pts = np.asarray(kxy[i], dtype=np.float32)
                if pts.shape[0] < 4:
                    continue
                quad_pts = order_quad(pts[:4]) if not self._well_ordered(pts[:4]) else pts[:4]
                det = Detection(
                    quad=Quad(quad_pts.astype(np.float32), float(scores[i]), "yolo26"),
                    confidence=float(scores[i]),
                    keypoints=np.column_stack([pts[:4], kconf[i][:4]]),
                    bbox=boxes[i],
                )
                out.append(det)
        self.last_timings = {"yolo": t_infer}
        out.sort(key=lambda d: -d.confidence)
        return out

    @staticmethod
    def _well_ordered(pts: np.ndarray) -> bool:
        """True if the 4 points already look like TL,TR,BR,BL (convex, clockwise)."""
        p = np.asarray(pts, np.float32)
        # cross products of consecutive edges must share a sign (convex polygon)
        cross = []
        for i in range(4):
            a, b, c = p[i], p[(i + 1) % 4], p[(i + 2) % 4]
            cross.append(np.cross(b - a, c - b))
        cross = np.sign(cross)
        if not (np.all(cross >= 0) or np.all(cross <= 0)):
            return False
        # TL should have the smallest x+y, BR the largest
        s = p.sum(axis=1)
        return bool(np.argmin(s) == 0 and np.argmax(s) == 2)

    # ---- convenience -----------------------------------------------------
    def quads(self, img: np.ndarray) -> list[Quad]:
        return [d.quad for d in self.detect(img)]

    def overlay(self, img: np.ndarray, dets: list[Detection],
                extra_quad: np.ndarray | None = None) -> np.ndarray:
        """Draw detections for the GUI preview."""
        import cv2

        out = img.copy()
        names = ["TL", "TR", "BR", "BL"]
        for d in dets:
            q = d.quad.pts.astype(np.int32)
            cv2.polylines(out, [q.reshape(-1, 1, 2)], True, (0, 255, 0), 2)
            for (x, y), nm in zip(d.quad.pts, names):
                cv2.circle(out, (int(x), int(y)), 4, (0, 140, 255), -1)
                cv2.putText(out, nm, (int(x) + 6, int(y) - 6), cv2.FONT_HERSHEY_SIMPLEX,
                            0.45, (0, 140, 255), 1, cv2.LINE_AA)
            x1, y1, x2, y2 = d.bbox.astype(int)
            cv2.rectangle(out, (x1, y1), (x2, y2), (255, 120, 0), 1)
            cv2.putText(out, f"frame {d.confidence:.2f}", (x1, max(12, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 120, 0), 1, cv2.LINE_AA)
        if extra_quad is not None:
            cv2.polylines(out, [np.asarray(extra_quad, np.int32).reshape(-1, 1, 2)],
                          True, (255, 0, 255), 2)
        return out
