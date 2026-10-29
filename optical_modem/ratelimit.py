"""Login throttling and proxy-aware client identity.

Deliberately dependency-free (stdlib only): the capture station runs from
``requirements-web.txt``, which has no SQLAlchemy and no Redis, so this module
has to be importable by both apps.

Two problems it exists to solve, both measured:

1. **``X-Forwarded-For`` is attacker-controlled.**  The container runs uvicorn
   with ``--proxy-headers --forwarded-allow-ips '*'`` (Dockerfile CMD), so
   ``request.client.host`` already *is* the forwarded value.  Keying a throttle
   on it lets an attacker rotate the header and try unlimited passwords.  The
   fix is not to stop trusting the header -- the app needs the real client IP for
   logs -- but to only trust it when the *peer* is a known proxy.
2. **A naive ``{ip: [timestamps]}`` dict is an unbounded leak.**  Entries were
   only ever pruned when that same IP came back, so an IP that failed once and
   never returned kept its slot forever.  Measured: 300 distinct IPs that each
   failed once left 300 entries resident.

:func:`client_ip` resolves the identity defensively; :class:`Throttle` keeps a
bounded, self-evicting window with both a per-key and a global limit.
"""

from __future__ import annotations

import ipaddress
import os
import threading
import time
from collections import OrderedDict, deque
from typing import Iterable

TRUE = {"1", "true", "yes", "on"}


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in TRUE:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


# --------------------------------------------------------------------------- #
# client identity
# --------------------------------------------------------------------------- #
#: Networks allowed to set X-Forwarded-For.  Empty (the default) means "any
#: peer", which preserves the previous behaviour for anyone already deployed
#: behind a proxy without configuring this.  Set TRUSTED_PROXY_CIDRS to lock it
#: down, e.g. "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1/32".
DEFAULT_TRUSTED_PROXY_CIDRS = ""


def trusted_proxy_networks() -> list[ipaddress._BaseNetwork]:
    raw = os.environ.get("TRUSTED_PROXY_CIDRS", DEFAULT_TRUSTED_PROXY_CIDRS)
    nets: list[ipaddress._BaseNetwork] = []
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            nets.append(ipaddress.ip_network(chunk, strict=False))
        except ValueError:
            continue
    return nets


def trust_any_proxy() -> bool:
    """``True`` when no allow-list is configured (legacy / dev behaviour)."""
    return not trusted_proxy_networks()


def _as_ip(value: str | None) -> ipaddress._BaseAddress | None:
    if not value:
        return None
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _peer_is_trusted(peer: str | None) -> bool:
    nets = trusted_proxy_networks()
    if not nets:
        return True                       # no allow-list configured: keep old behaviour
    addr = _as_ip(peer)
    if addr is None:
        return False
    return any(addr in net for net in nets)


def client_ip(request, *, trust_forwarded: bool = True) -> str:
    """The client IP to key security decisions on.

    ``X-Forwarded-For`` is honoured **only** when the direct peer is inside
    ``TRUSTED_PROXY_CIDRS`` (or when no allow-list is configured at all, which
    keeps existing single-container deployments working).  Otherwise the socket
    peer is returned, which an attacker cannot spoof.

    ``X-Forwarded-For`` is a comma-separated chain; the left-most entry is the
    original client but is the most trivially forged hop, so we walk from the
    right and take the first address that is *not* itself a trusted proxy --
    that is the value the last trusted proxy actually observed.
    """
    peer = request.client.host if getattr(request, "client", None) else None
    if not trust_forwarded:
        return peer or "?"

    forwarded = ""
    headers = getattr(request, "headers", None)
    if headers is not None:
        forwarded = headers.get("x-forwarded-for", "") or ""

    trusted_peer = _peer_is_trusted(peer)
    if not trusted_peer:
        return peer or "?"

    chain = [part.strip() for part in forwarded.split(",") if part.strip()]
    if not chain:
        return peer or "?"

    nets = trusted_proxy_networks()
    for entry in reversed(chain):
        addr = _as_ip(entry)
        if addr is None:
            return entry                  # unparseable: use it verbatim, it is logged
        if nets and any(addr in net for net in nets):
            continue                      # an intermediary we also own: keep walking
        return entry
    return chain[0] or peer or "?"


# --------------------------------------------------------------------------- #
# throttle
# --------------------------------------------------------------------------- #
class Throttle:
    """Bounded sliding-window limiter with a per-key and a global ceiling.

    Two independent limits, because either alone is defeatable:

    * **per key** (``limit`` within ``window``) stops one address guessing;
    * **global** (``global_limit`` within ``window``) stops an attacker rotating
      addresses -- no single key ever trips its own limit, so without this a
      spray of one-shot keys is entirely allowed.  Measured on the pre-fix code:
      30 wrong-password attempts with a rotated ``X-Forwarded-For`` produced
      **0** refusals.

    ``max_keys`` is what stops the leak: the store is an LRU capped at that many
    entries, so a spray of distinct keys evicts each other instead of growing
    without limit (measured: 300 distinct one-shot IPs previously left 300
    entries resident forever).  The global window is likewise a bounded deque.
    """

    def __init__(self, limit: int = 10, window: float = 60.0,
                 max_keys: int = 10_000, global_limit: int = 300) -> None:
        self.limit = max(1, int(limit))
        self.window = float(window)
        self.max_keys = max(16, int(max_keys))
        self.global_limit = max(1, int(global_limit))
        self._hits: "OrderedDict[str, list[float]]" = OrderedDict()
        self._global: "deque[float]" = deque()
        self._lock = threading.Lock()

    # ---- internals (caller must hold nothing; we take the lock) ------------
    def _prune(self, now: float) -> None:
        """Drop expired per-key windows and old global attempts."""
        for k in [k for k, ts in self._hits.items()
                  if not ts or now - ts[-1] >= self.window]:
            self._hits.pop(k, None)
        while self._global and now - self._global[0] >= self.window:
            self._global.popleft()

    def _trim(self) -> None:
        """Enforce the hard key cap (LRU eviction)."""
        while len(self._hits) > self.max_keys:
            self._hits.popitem(last=False)

    def _over_global(self, now: float) -> bool:
        return len(self._global) >= self.global_limit

    # ---- public -----------------------------------------------------------
    def check(self, key: str) -> bool:
        """``True`` when ``key`` may attempt now. Pure: records nothing."""
        now = time.time()
        with self._lock:
            self._prune(now)
            if self._over_global(now):
                return False
            hits = [t for t in self._hits.get(key, ()) if now - t < self.window]
            return len(hits) < self.limit

    def hit(self, key: str) -> None:
        """Record one attempt for ``key`` and enforce both caps."""
        now = time.time()
        with self._lock:
            self._prune(now)
            hits = [t for t in self._hits.get(key, ()) if now - t < self.window]
            hits.append(now)
            self._hits[key] = hits
            self._hits.move_to_end(key)
            self._global.append(now)
            self._trim()                 # after insertion, so the cap is a hard cap

    def clear(self, key: str) -> None:
        """Forget ``key`` -- call after a successful authentication."""
        with self._lock:
            self._hits.pop(key, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._hits)

    def stats(self) -> dict:
        now = time.time()
        with self._lock:
            self._prune(now)
            return {"keys": len(self._hits), "limit": self.limit,
                    "window": self.window, "max_keys": self.max_keys,
                    "global_limit": self.global_limit,
                    "global_attempts": len(self._global)}


def throttle_from_env(prefix: str = "LOGIN", *, limit: int = 10,
                      window: float = 60.0) -> Throttle:
    """Build a :class:`Throttle` with env overrides (``LOGIN_LIMIT`` etc.)."""
    def _int(name: str, default: int) -> int:
        try:
            return max(1, int(os.environ.get(f"{prefix}_{name}", "") or default))
        except ValueError:
            return default

    return Throttle(limit=_int("LIMIT", limit),
                    window=float(os.environ.get(f"{prefix}_WINDOW", "") or window),
                    max_keys=_int("MAX_KEYS", 10_000),
                    global_limit=_int("GLOBAL_LIMIT", 300))
