"""Reverse-proxy awareness: scheme, cookies, redirects, build stamps, diagnosis.

The suite is always deployed behind something that terminates TLS (Traefik via
Coolify, nginx, Caddy).  That proxy talks **plain HTTP** to this process, so every
absolute URL and every cookie flag the app produces has to be derived from
``X-Forwarded-Proto`` — not from the socket.  Getting that wrong produces the
single most confusing failure mode in this codebase:

    browser --https--> Traefik --http--> app
                                        app: "please use http://host/api/login"
    browser --http---> Traefik: "please use https://…"
    browser --https--> Traefik ... infinite loop (ERR_TOO_MANY_REDIRECTS)

and, for cookies, the quieter variant: a ``Secure`` cookie over plain HTTP is
silently dropped by the browser, so every page loads and every API call 401s.

This module is the single place that answers:

* :func:`client_scheme` — what scheme the *user's browser* is on.
* :func:`cookie_secure` — whether this response's cookie may be ``Secure``
  (``COOKIE_SECURE=auto`` = secure exactly when the browser is on https).
* :class:`ProxySchemeMiddleware` — makes ``scope["scheme"]`` right even when
  uvicorn was not started with ``--proxy-headers``, and forces redirects to be
  **relative** so a redirect can never downgrade https to http.
* :func:`diagnose` — the verdict list behind ``/api/net/diagnose``.
* :func:`build_id` — a stamp that changes whenever the code or static files do,
  used to version the service worker so a deploy cannot leave a stale shell
  (service workers only run in secure contexts, which is why "it works on http
  but not https" is so often a stale cache: http has no service worker at all).
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

TRUE = {"1", "true", "yes", "on"}
FALSE = {"0", "false", "no", "off"}


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in TRUE:
        return True
    if raw in FALSE:
        return False
    return default


def trust_forwarded() -> bool:
    """``TRUST_PROXY=0`` for the (rare) case of the app being exposed directly."""
    return _env_flag("TRUST_PROXY", True)


def _first(value: str | None) -> str:
    return (value or "").split(",")[0].strip().lower()


def _headers(scope_or_request) -> dict[str, str]:
    """Lower-cased header map from an ASGI scope or a Starlette ``Request``."""
    if hasattr(scope_or_request, "scope"):
        items = scope_or_request.scope.get("headers") or []
    else:
        items = scope_or_request.get("headers") or []
    out: dict[str, str] = {}
    for key, value in items:
        k = key.decode("latin-1").lower() if isinstance(key, bytes) else str(key).lower()
        v = value.decode("latin-1") if isinstance(value, bytes) else str(value)
        out[k] = f"{out[k]},{v}" if k in out else v
    return out


def client_scheme(request) -> str:
    """``"https"`` when the *browser* is on https, even if the socket is not.

    Order: ``X-Forwarded-Proto`` (first hop wins) → ``Forwarded: proto=`` →
    the ASGI scope (which uvicorn already fixed up if ``--proxy-headers``).
    """
    if trust_forwarded():
        hdrs = _headers(request)
        proto = _first(hdrs.get("x-forwarded-proto"))
        if proto in ("http", "https"):
            return proto
        forwarded = hdrs.get("forwarded", "")
        for part in forwarded.split(";"):
            part = part.strip()
            if part.lower().startswith("proto="):
                proto = part.split("=", 1)[1].strip().strip('"').lower()
                if proto in ("http", "https"):
                    return proto
    scope = getattr(request, "scope", request)
    scheme = scope.get("scheme") if isinstance(scope, dict) else None
    return scheme or "http"


def client_host(request) -> str:
    hdrs = _headers(request)
    if trust_forwarded():
        host = (hdrs.get("x-forwarded-host") or "").split(",")[0].strip()
        if host:
            return host
    scope = getattr(request, "scope", request)
    if isinstance(scope, dict):
        for key, value in scope.get("headers") or []:
            if key == b"host":
                return value.decode("latin-1")
    return ""


def is_secure(request) -> bool:
    return client_scheme(request) == "https"


def cookie_secure(request) -> bool:
    """Whether the session cookie should carry ``Secure`` for *this* request.

    ``COOKIE_SECURE`` accepts ``auto`` (default), ``1`` (always) and ``0``
    (never).  ``auto`` is the setting that cannot break a deployment: https gets
    a hardened cookie, plain-http LAN testing still logs in.
    """
    mode = os.environ.get("COOKIE_SECURE", "auto").strip().lower()
    if mode in FALSE:
        return False
    if mode in TRUE:
        return True
    return is_secure(request)


def wants_https_redirect(request) -> bool:
    """``FORCE_HTTPS=1``: bounce plain http to https inside the app.

    Off by default — the proxy (Traefik's redirectscheme) owns that decision, and
    two redirectors is how loops start.  Turn it on only if the container is
    exposed directly.
    """
    return _env_flag("FORCE_HTTPS", False) and not is_secure(request)


def public_url() -> str:
    """``PUBLIC_URL`` (e.g. ``https://s2c.example.com``) if configured."""
    return os.environ.get("PUBLIC_URL", "").strip().rstrip("/")


# --------------------------------------------------------------------------- #
# ASGI middleware: correct scheme, and redirects that cannot downgrade
# --------------------------------------------------------------------------- #
def _relative_location(location: str, scheme: str, host: str) -> str:
    """Turn a same-host absolute redirect into a path-relative one.

    Why this matters: Starlette's trailing-slash redirect builds an absolute URL
    from ``scope["scheme"]``.  If that is ``http`` behind an https proxy, the
    browser is sent to ``http://…``, the proxy sends it back to https, and the
    app sends it to http again — a loop that only exists over https.  A relative
    ``Location: /path`` keeps whatever scheme the browser already had, so the
    loop is impossible regardless of proxy configuration.
    """
    if not location or location.startswith(("/", "#", "?")):
        return location
    parts = urlsplit(location)
    if parts.scheme not in ("http", "https"):
        return location
    if host and parts.netloc and parts.netloc.lower() != host.lower():
        return location                       # a genuinely different host: leave it
    if not host and not parts.netloc:
        return location
    return urlunsplit(("", "", parts.path or "/", parts.query, parts.fragment))


class ProxySchemeMiddleware:
    """Make ``scope["scheme"]`` truthful and keep redirects scheme-safe.

    Pure ASGI so it costs nothing: no request parsing, no body buffering.  It is
    installed by both apps; installing it twice is harmless because setting the
    scope scheme twice is idempotent.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        hdrs = _headers(scope)
        forwarded = _first(hdrs.get("x-forwarded-proto")) if trust_forwarded() else ""
        forwarded_host = (hdrs.get("x-forwarded-host") or "").split(",")[0].strip()

        if forwarded in ("http", "https"):
            scope = dict(scope)
            scope["scheme"] = forwarded
        if forwarded_host and trust_forwarded() and hdrs.get("host", "") != forwarded_host:
            # url_for()/redirects are built from the Host header, not from
            # X-Forwarded-Host.  If a proxy rewrites Host (or the container is
            # addressed by service name internally), every generated URL would
            # otherwise leak ``http://app:8000`` into pages and emails.
            scope = dict(scope)
            scope["headers"] = [(k, v) for k, v in (scope.get("headers") or [])
                                if k.lower() != b"host"]
            scope["headers"].append((b"host", forwarded_host.encode("latin-1")))
        scheme = scope.get("scheme", "http")
        host = forwarded_host or hdrs.get("host", "")

        # FORCE_HTTPS=1: one redirect, then out of the way
        path = scope.get("path", "/")
        if (_env_flag("FORCE_HTTPS", False) and scheme != "https"
                and not path.startswith("/healthz")):
            body = b"redirecting to https\n"
            target = f"https://{host}{path}"
            query = scope.get("query_string") or b""
            if query:
                target += "?" + query.decode("latin-1")
            await send({"type": "http.response.start", "status": 308,
                        "headers": [(b"location", target.encode()),
                                    (b"content-type", b"text/plain"),
                                    (b"content-length", str(len(body)).encode()),
                                    (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": body})
            return

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                headers = []
                for key, value in message.get("headers") or []:
                    if key.lower() == b"location":
                        text = value.decode("latin-1")
                        text = _relative_location(text, scheme, host)
                        value = text.encode("latin-1")
                    headers.append((key, value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


def install(app) -> None:
    """Attach the middleware once (idempotent, order matters: outermost)."""
    names = {m.cls.__name__ for m in getattr(app, "user_middleware", [])}
    if ProxySchemeMiddleware.__name__ in names:
        return
    app.add_middleware(ProxySchemeMiddleware)


# --------------------------------------------------------------------------- #
# build stamp (service-worker cache busting, "did my deploy apply?")
# --------------------------------------------------------------------------- #
_BUILD: str | None = None


def static_roots() -> Iterable[Path]:
    here = Path(__file__).resolve().parent
    yield here / "capture" / "static"
    yield here / "suite" / "static"
    yield here / "suite" / "app.py"
    yield here / "capture" / "app.py"
    yield here / "net.py"


def build_id() -> str:
    """8 hex chars that change whenever code or static assets change.

    Cheap (file metadata only, ~1 ms for a few hundred files) and computed once
    per process, so it is stable within a container and different after a
    rebuild.  The service worker uses it as its cache version: a deploy that
    changes any asset automatically invalidates the old shell, which is the fix
    for "the app loads but its buttons do nothing" after an update.
    """
    global _BUILD
    if _BUILD is not None:
        return _BUILD
    digest = hashlib.sha1()
    for root in static_roots():
        try:
            if root.is_dir():
                for path in sorted(root.rglob("*")):
                    if path.is_file() and path.suffix in (
                            ".js", ".css", ".html", ".webmanifest", ".png", ".svg"):
                        stat = path.stat()
                        digest.update(f"{path.name}:{stat.st_size}:{int(stat.st_mtime)}"
                                      .encode())
            elif root.is_file():
                stat = root.stat()
                digest.update(f"{root.name}:{stat.st_size}:{int(stat.st_mtime)}".encode())
        except OSError:
            continue
    _BUILD = digest.hexdigest()[:8]
    return _BUILD


def stamp_build(html: str) -> str:
    """Fill the ``__BUILD__`` placeholders in an HTML page.

    The page then carries the build it was rendered from, which lets the client
    notice "the server has been redeployed since this tab loaded" and reload once
    — no polling, no stale shell, no manual hard-refresh after an update.
    """
    return html.replace("__BUILD__", build_id())


# --------------------------------------------------------------------------- #
# diagnosis: what the app can see about the proxy in front of it
# --------------------------------------------------------------------------- #
def diagnose(request) -> dict:
    """Everything needed to explain "why does https not work?", as data.

    Each check is ``{id, ok, detail, fix}``; ``fix`` is written for a human who is
    staring at a broken deployment, not for a log parser.
    """
    hdrs = _headers(request)
    scope = getattr(request, "scope", request) or {}
    scope_scheme = scope.get("scheme") if isinstance(scope, dict) else None
    fwd_proto = _first(hdrs.get("x-forwarded-proto"))
    fwd_for = hdrs.get("x-forwarded-for", "")
    fwd_host = (hdrs.get("x-forwarded-host") or "").split(",")[0].strip()
    scheme = client_scheme(request)
    host = client_host(request)
    secure = is_secure(request)
    cookie_mode = os.environ.get("COOKIE_SECURE", "auto").strip().lower() or "auto"
    behind_proxy = bool(fwd_proto or fwd_for or fwd_host and fwd_host != hdrs.get("host"))
    checks: list[dict] = []

    def check(cid: str, ok: bool, detail: str, fix: str = "") -> None:
        checks.append({"id": cid, "ok": bool(ok), "detail": detail, "fix": fix})

    check("forwarded_proto",
          bool(fwd_proto) or not behind_proxy,
          f"X-Forwarded-Proto: {fwd_proto or '(absent)'}",
          "The proxy must send X-Forwarded-Proto. Traefik does this by default; "
          "if you put a second proxy in front of it, make sure it appends rather "
          "than replaces the header.")

    check("scheme_trusted",
          (not fwd_proto) or scope_scheme == fwd_proto,
          f"app sees scheme={scope_scheme!r}, browser is on {scheme!r}",
          "uvicorn must trust the proxy: start it with "
          "`--proxy-headers --forwarded-allow-ips='*'` (the Dockerfile CMD does "
          "this; a Coolify override of the command may drop it).")

    check("redirects_relative",
          True,
          "absolute redirects are rewritten to path-relative Location headers",
          "Handled by ProxySchemeMiddleware — a redirect can no longer send an "
          "https browser to http:// and start a redirect loop.")

    check("cookie_secure",
          not (cookie_mode in ("1", "true", "yes") and not secure) and
          not (cookie_mode in ("0", "false", "no") and secure and
               public_url().startswith("https")),
          f"COOKIE_SECURE={cookie_mode} → cookie Secure={cookie_secure(request)} "
          f"(browser scheme {scheme})",
          "Use COOKIE_SECURE=auto (default): Secure on https, plain on http. "
          "Forcing 1 while testing over plain http makes the browser drop the "
          "cookie, which looks like 'the password is wrong'.")

    check("secure_context",
          secure or host.split(":")[0] in ("localhost", "127.0.0.1"),
          f"browser origin is {'https' if secure else 'http'}://{host}",
          "Browsers only expose the camera (getUserMedia) and service workers in "
          "a secure context. Over plain http the /camera page cannot work — that "
          "is a browser rule, not an app bug.")

    check("public_url",
          not public_url() or public_url().startswith(("http://", "https://")),
          f"PUBLIC_URL={public_url() or '(unset)'}",
          "Set PUBLIC_URL to the exact https origin users type (e.g. "
          "https://s2c.example.com). It is used for links printed in the UI and "
          "QR codes when the app cannot infer the external origin.")

    check("proxy_hops",
          fwd_for.count(",") <= 2,
          f"X-Forwarded-For: {fwd_for or '(absent)'}",
          "More than one proxy hop is fine, but each proxy must append. A hop "
          "that replaces the header breaks the per-IP login rate limit.")

    problems = [c for c in checks if not c["ok"]]
    return {
        "ok": not problems,
        "scheme": scheme,
        "scope_scheme": scope_scheme,
        "secure_context": secure,
        "host": host,
        "public_url": public_url(),
        "behind_proxy": behind_proxy,
        "trust_proxy": trust_forwarded(),
        "force_https": _env_flag("FORCE_HTTPS", False),
        "cookie_mode": cookie_mode,
        "cookie_secure": cookie_secure(request),
        "headers": {k: v for k, v in hdrs.items()
                    if k.startswith(("x-forwarded", "x-real", "forwarded", "host",
                                     "cf-", "x-scheme"))},
        "checks": checks,
        "problems": [c["id"] for c in problems],
        "build": build_id(),
        "time": time.time(),
        "verdict": ("proxy headers look correct"
                    if not problems else
                    "; ".join(f"{c['id']}: {c['detail']}" for c in problems)),
    }
