"""Tests for the rate limiter and proxy-aware client IP (audit H-4).

Both bugs this module fixes were reproduced against a live uvicorn server:
30 attempts with a rotated ``X-Forwarded-For`` produced **0** refusals, and 300
distinct one-shot IPs left 300 entries resident forever.
"""

from __future__ import annotations

import ipaddress
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from optical_modem.ratelimit import Throttle, client_ip, trusted_proxy_networks  # noqa: E402


class _Headers(dict):
    def get(self, key, default=None):
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


class _Client:
    def __init__(self, host):
        self.host = host


class _Request:
    def __init__(self, peer: str, xff: str = ""):
        self.client = _Client(peer) if peer else None
        self.headers = _Headers({"x-forwarded-for": xff} if xff else {})


# --------------------------------------------------------------------------- #
# client_ip
# --------------------------------------------------------------------------- #
def test_untrusted_peer_cannot_spoof(monkeypatch):
    """With an allow-list set, a public peer's XFF must be ignored."""
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "10.0.0.0/8,127.0.0.1/32")
    req = _Request("203.0.113.9", "1.2.3.4")
    assert client_ip(req) == "203.0.113.9", "spoofed XFF must not override the peer"


def test_trusted_peer_honours_xff(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    req = _Request("127.0.0.1", "203.0.113.9")
    assert client_ip(req) == "203.0.113.9"


def test_walks_past_intermediaries_we_own(monkeypatch):
    """The nearest untrusted hop is the one to key on."""
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "10.0.0.0/8,127.0.0.1/32")
    req = _Request("127.0.0.1", "203.0.113.9, 10.0.0.5, 10.0.0.6")
    assert client_ip(req) == "203.0.113.9"


def test_no_allowlist_keeps_legacy_behaviour(monkeypatch):
    """Unconfigured = previous behaviour, so existing deployments keep working."""
    monkeypatch.delenv("TRUSTED_PROXY_CIDRS", raising=False)
    assert trusted_proxy_networks() == []
    req = _Request("203.0.113.9", "1.2.3.4")
    assert client_ip(req) == "1.2.3.4"


def test_trust_forwarded_off(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    req = _Request("127.0.0.1", "203.0.113.9")
    assert client_ip(req, trust_forwarded=False) == "127.0.0.1"


def test_no_peer_falls_back(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "10.0.0.0/8")
    assert client_ip(_Request(None, "1.2.3.4")) in ("?", "1.2.3.4")


def test_malformed_cidr_is_ignored(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "not-a-cidr,10.0.0.0/8")
    assert [str(n) for n in trusted_proxy_networks()] == ["10.0.0.0/8"]


# --------------------------------------------------------------------------- #
# Throttle
# --------------------------------------------------------------------------- #
def test_per_key_limit():
    t = Throttle(limit=3, window=60.0)
    for _ in range(3):
        assert t.check("1.2.3.4")
        t.hit("1.2.3.4")
    assert not t.check("1.2.3.4"), "4th attempt must be refused"
    assert t.check("5.6.7.8"), "a different key is unaffected"


def test_window_expires(monkeypatch):
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    t = Throttle(limit=1, window=10.0)
    t.hit("k")
    assert not t.check("k")
    monkeypatch.setattr(time, "time", lambda: now + 11.0)
    assert t.check("k"), "window must expire"


def test_is_bounded():
    """The leak: 10x more distinct keys than the cap must not grow the store."""
    t = Throttle(limit=5, window=60.0, max_keys=100)
    for i in range(1000):
        t.hit(f"10.0.0.{i}")
    assert len(t) == 100, f"store grew to {len(t)} entries"


def test_global_ceiling_stops_a_distributed_spray():
    """Rotating IPs must be refused even though no single key trips its limit.

    This is the exact bypass that was reproduced against the live server:
    30 wrong-password attempts with a rotated X-Forwarded-For produced 0
    refusals, because the old limiter only ever counted per-IP.
    """
    t = Throttle(limit=10, window=60.0, max_keys=100_000, global_limit=200)

    def attempt(ip):
        if t.check(ip):
            t.hit(ip)
            return True
        return False

    allowed = sum(1 for i in range(1000) if attempt(f"198.18.{i // 256}.{i % 256}"))
    assert allowed == 200, f"expected the global ceiling to bite at 200, got {allowed}"


def test_global_window_recovers(monkeypatch):
    """The ceiling is a window, not a permanent lockout."""
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    t = Throttle(limit=50, window=10.0, global_limit=5)
    for i in range(5):
        assert t.check(f"k{i}")
        t.hit(f"k{i}")
    assert not t.check("k-new"), "ceiling reached"
    monkeypatch.setattr(time, "time", lambda: now + 11.0)
    assert t.check("k-new"), "ceiling must expire with the window"


def test_clear_after_success():
    t = Throttle(limit=1, window=60.0)
    t.hit("k")
    assert not t.check("k")
    t.clear("k")
    assert t.check("k")


def test_check_does_not_record():
    t = Throttle(limit=1, window=60.0)
    assert t.check("k") and t.check("k"), "check() must be side-effect free"
    t.hit("k")
    assert not t.check("k")


def test_eviction_removes_expired_windows(monkeypatch):
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    t = Throttle(limit=1, window=5.0)
    t.hit("old")
    monkeypatch.setattr(time, "time", lambda: now + 10.0)
    t.hit("new")
    assert len(t) == 1, "the expired key should have been evicted"


def test_stats_shape():
    t = Throttle()
    s = t.stats()
    assert {"keys", "limit", "window", "max_keys", "global_limit", "global_attempts"} <= set(s)


def test_throttle_from_env(monkeypatch):
    monkeypatch.setenv("LOGIN_LIMIT", "7")
    monkeypatch.setenv("LOGIN_WINDOW", "30")
    from optical_modem.ratelimit import throttle_from_env
    t = throttle_from_env("LOGIN")
    assert t.limit == 7 and t.window == 30.0
