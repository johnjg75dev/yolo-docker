#!/usr/bin/env bash
# Launch the web GUI (transmitter preview, camera receiver, live detection overlay).
set -e
cd "$(dirname "$0")"
export PYTHONPATH="$PWD"
exec python3 -m optical_modem gui --host 127.0.0.1 --port "${1:-8889}"
