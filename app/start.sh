#!/usr/bin/env bash
# RackReach - macOS / Linux launcher
set -euo pipefail
cd "$(dirname "$0")"
exec python3 run.py "$@"
