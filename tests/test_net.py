"""Tests for reverse-proxy awareness (``optical_modem/net.py``).

The bug these lock down: the container is always reached over plain HTTP by
Traefik, so anything that reads the *socket* to decide scheme/cookies/redirects
is wrong in production and right on a laptop — which is exactly the shape of
"it works on http but not https".
"""
from __future__ import annotations

import importlib

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from optical_modem import net


def make_request(headers: dict[str, str], scheme: str = "http") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": scheme,
        "path": "/", "raw_path": b"/", "query_string": b"", "root_path": "",
        "headers": raw, "client": ("127.0.0.1", 1234), "server": ("app", 80),
    }
    return Request(scope)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("COOKIE_SECURE", "TRUST_PROXY", "FORCE_HTTPS", "PUBLIC_URL"):
        monkeypatch.delenv(name, raising=False)
    yield


# --------------------------------------------------------------------- scheme
def test_scheme_defaults_to_scope_when_nothing_is_forwarded():
    assert net.client_scheme(make_request({})) == "http"
    assert net.client_scheme(make_request({}, scheme="https")) == "https"


def test_scheme_comes_from_x_forwarded_proto():
    req = make_request({"X-Forwarded-Proto": "https"})
    assert net.client_scheme(req) == "https"
    assert net.is_secure(req) is True


def test_scheme_takes_the_first_hop_of_a_chain():
    # "http, https" means the client's own leg was http: the *first* entry wins.
    assert net.client_scheme(make_request({"X-Forwarded-Proto": "http, https"})) == "http"
    assert net.client_scheme(make_request({"X-Forwarded-Proto": "https, http"})) == "https"


def test_scheme_understands_rfc7239_forwarded():
    req = make_request({"Forwarded": 'for=203.0.113.9;proto=https;host=s2c.example.com'})
    assert net.client_scheme(req) == "https"


def test_garbage_forwarded_proto_is_ignored_not_trusted():
    assert net.client_scheme(make_request({"X-Forwarded-Proto": "ftp"})) == "http"


def test_trust_proxy_zero_ignores_forwarded_headers(monkeypatch):
    monkeypatch.setenv("TRUST_PROXY", "0")
    assert net.client_scheme(make_request({"X-Forwarded-Proto": "https"})) == "http"


def test_host_prefers_forwarded_host_then_host():
    assert net.client_host(make_request(
        {"X-Forwarded-Host": "s2c.example.com", "Host": "app:8889"})) == "s2c.example.com"
    assert net.client_host(make_request({"Host": "app:8889"})) == "app:8889"


# -------------------------------------------------------------------- cookies
def test_cookie_auto_is_secure_exactly_on_https():
    assert net.cookie_secure(make_request({"X-Forwarded-Proto": "https"})) is True
    assert net.cookie_secure(make_request({"X-Forwarded-Proto": "http"})) is False
    # the old default: ``Secure`` over a plain-HTTP socket → the browser drops it
    # and every request is a 401, which looked like "login does nothing".
    assert net.cookie_secure(make_request({})) is False


def test_cookie_mode_one_and_zero_override_the_scheme(monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "1")
    assert net.cookie_secure(make_request({})) is True
    monkeypatch.setenv("COOKIE_SECURE", "0")
    assert net.cookie_secure(make_request({"X-Forwarded-Proto": "https"})) is False


def test_unknown_cookie_mode_behaves_like_auto(monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "yes-please")
    assert net.cookie_secure(make_request({"X-Forwarded-Proto": "https"})) is True


# ------------------------------------------------------------------ redirects
def test_force_https_is_off_by_default_and_only_fires_on_http(monkeypatch):
    assert net.wants_https_redirect(make_request({})) is False
    monkeypatch.setenv("FORCE_HTTPS", "1")
    assert net.wants_https_redirect(make_request({})) is True
    assert net.wants_https_redirect(make_request({"X-Forwarded-Proto": "https"})) is False


def test_relative_location_strips_our_own_origin():
    assert net._relative_location("http://s2c.example.com/api/login", "http",
                                  "s2c.example.com") == "/api/login"
    assert net._relative_location("https://s2c.example.com/a?b=1", "https",
                                  "s2c.example.com") == "/a?b=1"


def test_relative_location_leaves_other_hosts_and_relative_input_alone():
    assert net._relative_location("http://elsewhere.test/x", "http",
                                  "s2c.example.com") == "http://elsewhere.test/x"
    assert net._relative_location("/already", "http", "s2c.example.com") == "/already"


def test_public_url_is_normalised(monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "https://s2c.example.com/")
    assert net.public_url() == "https://s2c.example.com"


# ------------------------------------------------------------------ middleware
def build_app() -> Starlette:
    async def boom(request: Request):
        return RedirectResponse("http://s2c.example.com/new", status_code=307)

    async def scheme(request: Request):
        return PlainTextResponse(request.scope["scheme"] + "|" + str(request.url_for("scheme")))

    app = Starlette(routes=[
        Route("/redirect", boom),
        Route("/scheme", scheme, name="scheme"),
    ])
    net.install(app)
    return app


def test_middleware_rewrites_downgrading_location_to_relative():
    """The loop: app says http, proxy says https, browser ping-pongs forever."""
    client = TestClient(build_app(), base_url="http://app:8889")
    res = client.get("/redirect", headers={"X-Forwarded-Proto": "https",
                                           "X-Forwarded-Host": "s2c.example.com"},
                     follow_redirects=False)
    assert res.headers["location"] == "/new"


def test_middleware_sets_scope_scheme_without_uvicorn_proxy_headers():
    client = TestClient(build_app(), base_url="http://app:8889")
    body = client.get("/scheme", headers={"X-Forwarded-Proto": "https",
                                          "X-Forwarded-Host": "s2c.example.com"}).text
    scheme, *rest = body.split("|")
    assert scheme == "https"
    assert "|".join(rest).startswith("https://s2c.example.com/scheme")


def test_middleware_is_idempotent():
    app = build_app()
    before = len(app.user_middleware)
    net.install(app)
    assert len(app.user_middleware) == before


# -------------------------------------------------------------- build stamps
def test_build_id_is_eight_hex_and_stable():
    first = net.build_id()
    assert len(first) == 8 and all(c in "0123456789abcdef" for c in first)
    assert net.build_id() == first


def test_build_id_changes_when_an_asset_changes(monkeypatch):
    monkeypatch.setattr(net, "_BUILD", None)
    original = net.build_id()
    monkeypatch.setattr(net, "_BUILD", None)
    monkeypatch.setattr(net, "static_roots", lambda: [])
    assert net.build_id() != original


def test_stamp_build_replaces_every_placeholder():
    html = '<meta name="om-build" content="__BUILD__">__BUILD__'
    out = net.stamp_build(html)
    assert "__BUILD__" not in out
    assert out.count(net.build_id()) == 2


# ------------------------------------------------------------------ diagnosis
def test_diagnose_reports_a_healthy_https_proxy():
    req = make_request({"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.9",
                        "X-Forwarded-Host": "s2c.example.com"}, scheme="https")
    data = net.diagnose(req)
    checks = {c["id"]: c for c in data["checks"]}
    assert data["scheme"] == "https" and data["secure_context"] is True
    assert checks["forwarded_proto"]["ok"] is True
    assert checks["cookie_secure"]["ok"] is True
    assert checks["redirects_relative"]["ok"] is True
    assert data["problems"] == []


def test_diagnose_flags_a_proxy_that_forwards_everything_except_the_scheme():
    """The real misconfiguration: X-Forwarded-For/Host arrive, proto does not."""
    data = net.diagnose(make_request({"X-Forwarded-For": "203.0.113.9",
                                      "X-Forwarded-Host": "s2c.example.com"},
                                     scheme="http"))
    checks = {c["id"]: c for c in data["checks"]}
    assert data["behind_proxy"] is True
    assert checks["forwarded_proto"]["ok"] is False
    assert "X-Forwarded-Proto" in checks["forwarded_proto"]["fix"]
    assert "forwarded_proto" in data["problems"]
    assert data["ok"] is False


def test_diagnose_does_not_false_alarm_when_nothing_is_forwarded():
    """Reached directly (a laptop, `docker run -p`): no headers is normal."""
    data = net.diagnose(make_request({}, scheme="http"))
    checks = {c["id"]: c for c in data["checks"]}
    assert data["behind_proxy"] is False
    assert checks["forwarded_proto"]["ok"] is True
    assert checks["cookie_secure"]["ok"] is True


def test_diagnose_flags_secure_cookie_on_a_plain_http_socket(monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "1")
    data = net.diagnose(make_request({}, scheme="http"))
    checks = {c["id"]: c for c in data["checks"]}
    assert checks["cookie_secure"]["ok"] is False
    assert "COOKIE_SECURE" in checks["cookie_secure"]["fix"]


def test_diagnose_explains_the_secure_context_rule_for_the_camera():
    """getUserMedia needs https; this is why the capture page "does not work"."""
    data = net.diagnose(make_request({}, scheme="http"))
    checks = {c["id"]: c for c in data["checks"]}
    assert checks["secure_context"]["ok"] is False
    assert data["secure_context"] is False


def test_diagnose_is_json_serialisable():
    import json

    json.dumps(net.diagnose(make_request({"X-Forwarded-Proto": "https"}, scheme="https")))
