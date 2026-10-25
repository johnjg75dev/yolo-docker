"""Central logging setup for the suite.

Controlled by environment variables (read once at import time):

* ``SUITE_LOG_LEVEL`` / ``LOG_LEVEL`` — one of DEBUG, INFO, WARNING, ERROR,
  CRITICAL.  Defaults to ``DEBUG`` (verbose) so that Coolify's runtime log
  view shows every action out of the box.
* ``SUITE_LOG_VERBOSE`` — ``0``/``false``/``off`` disables verbose (DEBUG)
  output even when the level above is left at its default.  Kept for
  backwards-friendly "turn it off" usage.

All output goes to stdout so Coolify (and ``docker logs``) captures it.
"""
from __future__ import annotations

import logging
import os
import sys
import time


def _level() -> int:
    raw = (os.environ.get("SUITE_LOG_LEVEL")
           or os.environ.get("LOG_LEVEL")
           or "").strip().upper()
    if not raw:
        verbose = os.environ.get("SUITE_LOG_VERBOSE", "1").strip().lower()
        raw = "DEBUG" if verbose not in ("0", "false", "off", "no") else "INFO"
    return getattr(logging, raw, logging.DEBUG)


class _Fmt(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        # "Jan 01, 2027 06:32P"  (single-letter AM/PM to match the requested style)
        t = self.converter(record.created)
        ampm = "A" if t.tm_hour < 12 else "P"
        return time.strftime("%b %d, %Y %I:%M", t) + ampm

    def format(self, record):
        record.asctime = self.formatTime(record, self.datefmt)
        return super().format(record)


def configure(name: str = "suite") -> logging.Logger:
    """Configure the root logger once and return the named logger."""
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_Fmt(
            "[%(levelname)s] %(asctime)s [%(name)s] - %(message)s"))
        root.addHandler(handler)
    root.setLevel(_level())
    # Quiet noisy third-party loggers unless explicitly debug.
    for noisy in ("urllib3", "httpx", "httpcore", "redis", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return logging.getLogger(name)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
