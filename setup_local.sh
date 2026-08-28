#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$PROJECT_ROOT/setup_local.py" "$@"
