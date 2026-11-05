#!/usr/bin/env bash
# RackReach Map - macOS / Linux launcher
set -euo pipefail
cd "$(dirname "$0")"
exec python3 run.py "$@"
