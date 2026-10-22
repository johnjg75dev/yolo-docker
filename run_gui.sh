#!/usr/bin/env bash
# Launch the web GUI (transmitter preview, camera receiver, live detection overlay).
set -e
cd "$(dirname "$0")"
export PYTHONPATH="$PWD"
exec python3 -m optical_modem gui --host 0.0.0.0 --port "${1:-8000}"
