"""Answer "why doesn't it work over https?" from the outside, in one command.

Run it against the address users type — from your laptop, or from inside the
server.  It walks the same path a browser does (redirect, certificate, cookies,
API, streaming) and prints one verdict per step with the fix attached.

    python3 scripts/check_https.py --base https://s2c.example.com --password … 
    python3 scripts/check_https.py --base http://127.0.0.1:8000 --local

Checks, in the order that failures usually appear:

 1. DNS + TCP + TLS handshake, certificate hostname and expiry
 2. the plain-http → https redirect (and its status code — 302 downgrades a POST
    to a GET, which breaks the login form)
 3. `/api/net/diagnose`: does Traefik send X-Forwarded-Proto, does the app trust
    it, would the cookie be Secure for this scheme
 4. login + an authenticated API call (cookie actually stored and sent back)
 5. Server-Sent Events actually stream (a buffering proxy silently breaks them)

Exit code 0 = all good, 1 = at least one real problem (printed with a fix).
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

OK, WARN, BAD = "ok  ", "warn", "FAIL"


class Result:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, status: str, what: str, detail: str = "") -> None:
        self.rows.append((status, what, detail))

    @property
    def failed(self) -> bool:
        return any(s == BAD for s, _, _ in self.rows)

    def render(self) -> str:
        width = max((len(w) for _, w, _ in self.rows), default=10)
        lines = [f"{s}  {w:<{width}}  {d}" for s, w, d in self.rows]
        return "\n".join(lines)


def make_opener(jar: http.cookiejar.CookieJar | None = None,
                insecure: bool = False) -> urllib.request.OpenerDirector:
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    handlers = [urllib.request.HTTPSHandler(context=ctx)]
    if jar is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(jar))
    return urllib.request.build_opener(*handlers)


def request(opener, url: str, method: str = "GET", payload: dict | None = None,
            timeout: float = 15.0) -> tuple[int, dict, bytes]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=timeout) as res:
            body = res.read()
            try:
                return res.status, json.loads(body), body
            except ValueError:
                return res.status, {}, body
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body), body
        except ValueError:
            return exc.code, {}, body


def check_tls(base: str, res: Result, insecure: bool) -> None:
    parts = urlsplit(base)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        socket.create_connection((host, port), timeout=8).close()
        res.add(OK, "tcp", f"{host}:{port} reachable")
    except OSError as exc:
        res.add(BAD, "tcp", f"{host}:{port} unreachable ({exc}) — DNS, firewall "
                            f"or the proxy is not listening on that port")
        return
    if parts.scheme != "https":
        return
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=8) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as tls:
                cert = tls.getpeercert() or {}
                issuer = dict(x[0] for x in cert.get("issuer", []))
                not_after = cert.get("notAfter", "?")
                res.add(OK, "tls", f"{tls.version()} · issuer {issuer.get('commonName', '?')}"
                                    f" · expires {not_after}")
    except ssl.SSLCertVerificationError as exc:
        res.add(BAD, "tls", f"certificate rejected: {exc.verify_message} — Traefik has "
                            f"not issued a valid cert for this host (ACME needs DNS + "
                            f"port 80, or use the DNS challenge)")
    except Exception as exc:
        res.add(BAD, "tls", f"handshake failed: {exc}")


def check_redirect(base: str, res: Result, insecure: bool,
                   plain_port: int | None = None) -> None:
    """http -> https must be a 307/308 for anything that can POST."""
    parts = urlsplit(base)
    if parts.scheme != "https":
        res.add(WARN, "redirect", "testing an http base: skipping the upgrade check")
        return
    host = parts.hostname or parts.netloc
    netloc = f"{host}:{plain_port}" if plain_port else host
    plain = f"http://{netloc}/api/suite/diagnose"

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):  # never follow: we want the 3xx
            return None

    opener = urllib.request.build_opener(urllib.request.HTTPHandler(), _NoRedirect())
    req = urllib.request.Request(plain, method="GET")
    try:
        with opener.open(req, timeout=10) as r:
            res.add(WARN, "redirect", f"plain http answered {r.status} instead of "
                                      f"redirecting to https")
            return
    except urllib.error.HTTPError as exc:      # any 3xx that we refuse to follow
        location = exc.headers.get("location", "")
        code = exc.code
        exc.close()
    except Exception as exc:
        res.add(WARN, "redirect", f"plain http not reachable ({exc})")
        return
    if not location.startswith("https://"):
        res.add(BAD, "redirect", f"{code} → {location or '(no Location)'} does not "
                                 f"point at https")
        return
    if code in (301, 302):
        res.add(WARN, "redirect", f"{code} → https (fine for GET, but a POST "
                                  f"becomes a GET: prefer 308 in Traefik's "
                                  f"redirectscheme middleware)")
    else:
        res.add(OK, "redirect", f"{code} → {location}")


def check_diagnose(base: str, res: Result, insecure: bool) -> dict:
    opener = make_opener(insecure=insecure)
    status, data, _ = request(opener, base + "/api/suite/diagnose")
    if status != 200 or not data:
        # older builds only have the capture app's endpoint
        status, data, _ = request(opener, base + "/api/net/diagnose")
    if status != 200 or not data:
        res.add(BAD, "diagnose", f"GET /api/suite/diagnose → {status}; the app is "
                                 f"not answering behind this proxy")
        return {}
    browser_scheme = "https" if base.startswith("https://") else "http"
    seen = data.get("scheme")
    if seen and seen != browser_scheme:
        res.add(BAD, "scheme", f"this request reached the app as {seen!r} but the "
                               f"browser is on {browser_scheme!r} — the proxy is not "
                               f"passing the scheme through (or the app was started "
                               f"without --proxy-headers)")
    elif seen:
        res.add(OK, "scheme", f"app and browser both see {seen!r}")
    for c in data.get("checks", []):
        res.add(OK if c["ok"] else BAD, c["id"], c["detail"] +
                ("" if c["ok"] else f"  →  {c['fix']}"))
    res.add(OK, "build", data.get("build", "?"))
    if data.get("database", {}).get("ok") is False:
        res.add(BAD, "database", str(data["database"].get("error", ""))[:120])
    return data


def check_login(base: str, res: Result, insecure: bool, password: str | None) -> None:
    if not password:
        res.add(WARN, "login", "no --password given: skipping the authenticated checks")
        return
    jar = http.cookiejar.CookieJar()
    opener = make_opener(jar, insecure=insecure)
    status, data, _ = request(opener, base + "/api/login", "POST",
                              {"password": password})
    if status != 200:
        res.add(BAD, "login", f"POST /api/login → {status} {data.get('detail', '')}")
        return
    cookies = [(c.name, bool(c.secure)) for c in jar]
    if not cookies:
        res.add(BAD, "login", "the server answered 200 but the browser got no cookie "
                              "— a Secure cookie over plain http is dropped")
        return
    secure = cookies[0][1]
    status, _, _ = request(opener, base + "/api/suite/progress")
    if status == 200:
        res.add(OK, "login", f"cookie stored (Secure={secure}), authenticated API 200")
    else:
        res.add(BAD, "login", f"cookie stored but the API answered {status} — the "
                              f"cookie is not being sent back (Secure/Path/domain)")


def check_sse(base: str, res: Result, insecure: bool, password: str | None) -> None:
    if not password:
        return
    jar = http.cookiejar.CookieJar()
    opener = make_opener(jar, insecure=insecure)
    request(opener, base + "/api/login", "POST", {"password": password})
    req = urllib.request.Request(base + "/api/events")
    started = time.time()
    try:
        with opener.open(req, timeout=8) as r:
            first = r.readline()
            elapsed = time.time() - started
            if r.headers.get("content-type", "").startswith("text/event-stream"):
                res.add(OK if elapsed < 5 else WARN, "sse",
                        f"streaming ({elapsed:.2f}s to first byte: {first[:24]!r})")
            else:
                res.add(WARN, "sse", f"unexpected content-type {r.headers.get('content-type')}")
    except Exception as exc:
        res.add(WARN, "sse", f"the event stream did not open ({exc}) — a buffering "
                             f"proxy middleware will do this; the UI falls back to polling")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="diagnose an https deployment")
    ap.add_argument("--base", required=True, help="e.g. https://s2c.example.com")
    ap.add_argument("--password", default=None)
    ap.add_argument("--plain-port", type=int, default=None,
                    help="port that serves plain http (default 80; use 8080 when "
                         "testing a proxy on a high port)")
    ap.add_argument("--local", action="store_true",
                    help="skip certificate verification (self-signed test proxy)")
    args = ap.parse_args(argv)
    base = args.base.rstrip("/")
    res = Result()
    print(f"checking {base}\n")
    check_tls(base, res, args.local)
    check_redirect(base, res, args.local, args.plain_port)
    check_diagnose(base, res, args.local)
    check_login(base, res, args.local, args.password)
    check_sse(base, res, args.local, args.password)
    print(res.render())
    print()
    if res.failed:
        print("VERDICT: at least one real problem (see FAIL above). Most https "
              "failures are one of: certificate not issued, proxy headers not "
              "trusted, COOKIE_SECURE forced against the scheme, or a stale "
              "service worker.")
        return 1
    if base.startswith("https://"):
        print("VERDICT: https looks correct end to end.")
    else:
        print("VERDICT: plain http looks correct. Note that the camera "
              "(getUserMedia) and service workers only work in a secure context, "
              "so a real deployment still needs TLS — put this behind Traefik and "
              "re-run with --base https://YOUR_DOMAIN.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
