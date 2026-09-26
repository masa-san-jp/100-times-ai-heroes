#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

PYTHON="${PYTHON:-python3}"
export PIP_DISABLE_PIP_VERSION_CHECK=1
if ! PYTHON_VERSION="$("$PYTHON" --version 2>&1)"; then
    echo "ERROR: Python executable not found or could not be run: $PYTHON" >&2
    exit 1
fi
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "ERROR: Python 3.10 or newer is required; found $PYTHON_VERSION" >&2
    exit 1
fi

VENV_DIR="$REPO_ROOT/.venv-ci"
if [ ! -x "$VENV_DIR/bin/python" ]; then
    "$PYTHON" -m venv "$VENV_DIR"
fi

CI_PYTHON="$VENV_DIR/bin/python"
"$CI_PYTHON" -m pip install -q -r requirements-dev.txt

"$CI_PYTHON" -m compileall -q \
    -x '(^|/)(\.venv[^/]*/|\.runtime/|data/|20240916-AI-Art-GP-3-Charactor-v1\.0\.py$)' \
    .
"$CI_PYTHON" -m pytest -q
