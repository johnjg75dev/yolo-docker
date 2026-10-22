"""The end-to-end suite: dataset -> model -> screen-to-camera transfer.

Stages, all in one container behind one login:

* **dataset** — synthetic frames (:mod:`optical_modem.data.synth`), real captures
  from the capture station (:mod:`optical_modem.capture`), and a merge step
  (:mod:`optical_modem.suite.datasets`);
* **train** — YOLO26-pose fine-tuning as a background job
  (:mod:`optical_modem.suite.jobs`);
* **verify** — corner error / decode rate / loopback decode
  (:mod:`optical_modem.suite.s2c`);
* **model registry** — promote a run to *active* (:mod:`optical_modem.suite.registry`);
* **S2C** — the actual application: send bytes over a screen, read them with a
  camera (:mod:`optical_modem.suite.s2c`).

The FastAPI app is :mod:`optical_modem.suite.app`.
"""
from .registry import Registry, describe_env  # noqa: F401
from .s2c import S2C, make_config  # noqa: F401

__all__ = ["Registry", "S2C", "make_config", "describe_env"]
