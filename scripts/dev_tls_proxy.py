"""A tiny TLS-terminating reverse proxy, to reproduce a Traefik/Coolify deploy.

Why this exists: "it works on http, not on https" is nearly always a *proxy*
question — which scheme the app thinks it is on, which forwarded headers arrive,
and whether a stream gets buffered. This proxy puts the app behind real TLS with
the same headers Traefik sends, so the whole login / cookie / SSE path can be
reproduced (and regression-tested) without a domain or a certificate authority.

    # 1. a throw-away certificate
    openssl req -x509 -newkey rsa:2048 -nodes -keyout /tmp/key.pem \\
        -out /tmp/cert.pem -days 2 -subj "/CN=localhost" \\
        -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

    # 2. terminate TLS on 8443, forward to the app on 8200
    python3 scripts/dev_tls_proxy.py --listen 8443 --upstream 127.0.0.1:8200 \\
        --cert /tmp/cert.pem --key /tmp/key.pem

    # 3. behave like a browser
    curl -k -c /tmp/j https://127.0.0.1:8443/healthz

Options that mirror the real thing:

  --proto https|http        value of X-Forwarded-Proto (default https)
  --no-forwarded-proto      send no X-Forwarded-Proto at all (a broken proxy)
  --redirect-http 302|301   answer plain-HTTP on --plain-port with a redirect,
                            exactly like Traefik's redirectscheme middleware
  --buffer                  hold the response until it ends (a proxy with
                            response buffering): SSE stops working, as it does
                            behind a misconfigured middleware
  --strip-cookie-secure     rewrite Set-Cookie to drop "Secure" (some proxies do)
"""
from __future__ import annotations

import argparse
import asyncio
import ssl
import sys

HEAD_LIMIT = 64 * 1024


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--listen", type=int, default=8443)
    ap.add_argument("--plain-port", type=int, default=8080)
    ap.add_argument("--upstream", default="127.0.0.1:8200")
    ap.add_argument("--cert", default="/tmp/cert.pem")
    ap.add_argument("--key", default="/tmp/key.pem")
    ap.add_argument("--proto", default="https", choices=["http", "https"])
    ap.add_argument("--no-forwarded-proto", action="store_true")
    ap.add_argument("--redirect-http", default=None, choices=["301", "302", "308"])
    ap.add_argument("--buffer", action="store_true")
    ap.add_argument("--strip-cookie-secure", action="store_true")
    return ap.parse_args(argv)


async def _read_head(reader: asyncio.StreamReader) -> tuple[bytes, bytes]:
    """Read up to and including the blank line that ends the request head."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = await reader.read(2048)
        if not chunk:
            return buf, b""
        buf += chunk
        if len(buf) > HEAD_LIMIT:
            break
    head, _, rest = buf.partition(b"\r\n\r\n")
    return head + b"\r\n\r\n", rest


def _rewrite(head: bytes, args: argparse.Namespace, client: tuple) -> bytes:
    lines = head.decode("latin-1").split("\r\n")
    method, path, version = (lines[0].split(" ") + ["", "", ""])[:3]
    out = [f"{method} {path} {version}"]
    hosts = []
    drop = {"x-forwarded-proto", "x-forwarded-for", "x-forwarded-host",
            "x-forwarded-port", "x-real-ip", "connection"}
    for line in lines[1:]:
        if not line:
            continue
        name = line.split(":", 1)[0].strip().lower()
        if name in drop:
            continue
        if name == "host":
            hosts.append(line.split(":", 1)[1].strip())
            out.append(line)                      # keep Host: the vhost
            continue
        out.append(line)
    host = hosts[0] if hosts else "127.0.0.1"
    if not args.no_forwarded_proto:
        out.append(f"X-Forwarded-Proto: {args.proto}")
    out.append(f"X-Forwarded-For: {client[0]}")
    out.append(f"X-Forwarded-Host: {host}")
    out.append("X-Forwarded-Port: %d" % (args.listen if args.proto == "https"
                                        else args.plain_port))
    out.append("X-Real-IP: " + client[0])
    out.append("Connection: close")
    return ("\r\n".join(out) + "\r\n\r\n").encode("latin-1")


def _fix_response_head(head: bytes, args: argparse.Namespace) -> bytes:
    text = head.decode("latin-1")
    if args.strip_cookie_secure:
        text = "\n".join(
            line.replace("; Secure", "").replace("; secure", "")
            if line.lower().startswith("set-cookie") else line
            for line in text.split("\n"))
    return text.encode("latin-1")


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 args: argparse.Namespace) -> None:
    client = writer.get_extra_info("peername") or ("?", 0)
    try:
        head, rest = await _read_head(reader)
        if not head:
            return
        up_host, _, up_port = args.upstream.partition(":")
        up_r, up_w = await asyncio.open_connection(up_host, int(up_port or 80))
        up_w.write(_rewrite(head, args, client) + rest)
        await up_w.drain()

        # request body (uploads): copy until the client stops talking
        async def pump_request() -> None:
            try:
                while True:
                    data = await reader.read(65536)
                    if not data:
                        break
                    up_w.write(data)
                    await up_w.drain()
            except Exception:
                pass
            finally:
                try:
                    up_w.write_eof()
                except Exception:
                    pass

        req_task = asyncio.create_task(pump_request())

        first = True
        buffered = b""
        while True:
            chunk = await up_r.read(65536)
            if not chunk:
                break
            if first:
                body_at = chunk.find(b"\r\n\r\n")
                if body_at >= 0:
                    head_part = chunk[:body_at + 4]
                    chunk = _fix_response_head(head_part, args) + chunk[body_at + 4:]
                first = False
            if args.buffer:
                buffered += chunk
                continue
            writer.write(chunk)
            await writer.drain()
        if args.buffer and buffered:
            writer.write(buffered)
            await writer.drain()
        req_task.cancel()
        up_w.close()
    except Exception as exc:                        # never crash the proxy
        print(f"[tls-proxy] {client[0]}: {exc}", file=sys.stderr, flush=True)
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def plain_redirect(reader: asyncio.StreamReader,
                         writer: asyncio.StreamWriter,
                         args: argparse.Namespace) -> None:
    """Traefik's redirectscheme middleware, including its status code."""
    try:
        head, _ = await _read_head(reader)
        if not head:
            return
        first = head.split(b"\r\n", 1)[0].decode("latin-1")
        parts = first.split(" ")
        path = parts[1] if len(parts) > 1 else "/"
        host = ""
        for line in head.decode("latin-1").split("\r\n"):
            if line.lower().startswith("host:"):
                host = line.split(":", 1)[1].strip()
        if ":" in host:
            host = host.split(":")[0]
        location = f"https://{host or '127.0.0.1'}:{args.listen}{path}"
        code = args.redirect_http
        body = f"redirecting to {location}\n".encode()
        writer.write(
            f"HTTP/1.1 {code} Moved\nLocation: {location}\r\n"
            f"Content-Type: text/plain\r\nContent-Length: {len(body)}\r\n"
            f"Connection: close\r\n\r\n".encode() + body)
        await writer.drain()
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def main_async(args: argparse.Namespace) -> None:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(args.cert, args.key)
    tls = await asyncio.start_server(
        lambda r, w: handle(r, w, args), "127.0.0.1", args.listen, ssl=ctx)
    servers = [tls]
    if args.redirect_http:
        plain = await asyncio.start_server(
            lambda r, w: plain_redirect(r, w, args), "127.0.0.1", args.plain_port)
        servers.append(plain)
        print(f"[tls-proxy] http://127.0.0.1:{args.plain_port} -> "
              f"{args.redirect_http} https://…:{args.listen}", flush=True)
    print(f"[tls-proxy] https://127.0.0.1:{args.listen} -> {args.upstream} "
          f"(X-Forwarded-Proto: {'absent' if args.no_forwarded_proto else args.proto}"
          f"{', buffering' if args.buffer else ''})", flush=True)
    await asyncio.gather(*(s.serve_forever() for s in servers))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
