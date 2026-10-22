"""Local stand-in for Redis, for demos and CI where no daemon is installed.

Speaks the real Redis protocol over TCP (fakeredis), so the suite's RedisBus
cannot tell the difference: pub/sub, SET NX EX locks and the capped event list
all behave as they would against redis:7-alpine in docker-compose.

    python3 scripts/dev_redis.py 6380      # then: REDIS_URL=redis://127.0.0.1:6380/0
"""
from __future__ import annotations

import sys
import threading

import fakeredis


def main(argv: list[str]) -> int:
    port = int(argv[1]) if len(argv) > 1 else 6380
    host = argv[2] if len(argv) > 2 else "127.0.0.1"
    srv = fakeredis.TcpFakeServer((host, port), server_type="redis")
    print(f"[dev-redis] listening on {host}:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    threading.current_thread().name = "dev-redis"
    raise SystemExit(main(sys.argv))
