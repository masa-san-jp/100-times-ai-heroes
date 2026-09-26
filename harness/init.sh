#!/bin/bash
# 100 Times AI Heroes - Ollama版 初期化スクリプト
# M4 MacBook Pro (128GB) 向け

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "======================================"
echo "100 Times AI Heroes - Ollama Setup"
echo "======================================"

# .envは実行せず、指定キーの値だけを読み取る
read_env_value() {
    local key="$1"
    local file="$2"
    sed -n "s/^${key}=//p" "$file" | head -n 1 | tr -d '\r' | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

# 1. 環境設定
echo ""
echo "[1/6] Checking configuration..."
if [ ! -f ".env" ]; then
    echo "  Creating .env from template..."
    cp .env.example .env
    echo "  OK: .env created from .env.example"
else
    echo "  OK: .env exists"
fi

if [ -n "${OLLAMA_MODEL:-}" ]; then
    MODEL="$OLLAMA_MODEL"
else
    MODEL="$(read_env_value OLLAMA_MODEL .env)"
    if [ -z "$MODEL" ]; then
        MODEL="gpt-oss:20b"
    fi
fi

if [ -n "${OLLAMA_HOST:-}" ]; then
    OLLAMA_URL="$OLLAMA_HOST"
else
    OLLAMA_URL="$(read_env_value OLLAMA_HOST .env)"
    if [ -z "$OLLAMA_URL" ]; then
        OLLAMA_URL="http://127.0.0.1:11434"
    fi
fi
# ollama CLI reads OLLAMA_HOST, so point it at the same host we check.
export OLLAMA_HOST="$OLLAMA_URL"
echo "  Model: $MODEL"
echo "  Host: $OLLAMA_URL"

# 2. Python環境確認
echo ""
echo "[2/6] Checking Python environment..."
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: Python3 not found"
    exit 1
fi
if ! PYTHON_VERSION=$(python3 --version 2>&1); then
    echo "ERROR: Python3 could not be run"
    exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "ERROR: Python 3.10 or newer is required; found $PYTHON_VERSION"
    exit 1
fi
echo "  OK: $PYTHON_VERSION"

if [ ! -x ".venv/bin/python" ]; then
    echo "  Creating .venv..."
    python3 -m venv .venv
fi
VENV_PYTHON=".venv/bin/python"
if ! "$VENV_PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "ERROR: .venv must use Python 3.10 or newer"
    exit 1
fi
echo "  OK: $VENV_PYTHON"

# 3. 依存パッケージインストール
echo ""
echo "[3/6] Installing dependencies..."
if [ -f "requirements-dev.txt" ]; then
    "$VENV_PYTHON" -m pip install -q -r requirements-dev.txt
    echo "  OK: Dependencies installed in .venv"
else
    echo "  SKIP: requirements-dev.txt not found"
fi

# 4. Ollama確認
echo ""
echo "[4/6] Checking Ollama installation..."
if ! command -v ollama >/dev/null 2>&1; then
    echo "ERROR: Ollama not found. Install with: brew install ollama"
    exit 1
fi
echo "  OK: Ollama installed"

# 5. Ollamaサーバー確認
echo ""
echo "[5/6] Checking Ollama server..."
if ! curl -s "${OLLAMA_URL}/api/tags" > /dev/null 2>&1; then
    echo "  Starting Ollama server..."
    ollama serve &
    sleep 3
fi
if ! curl -s "${OLLAMA_URL}/api/tags" > /dev/null 2>&1; then
    echo "ERROR: Ollama server is not reachable at ${OLLAMA_URL}/api/tags"
    exit 1
fi
echo "  OK: Ollama server running"

# 6. モデル確認（自動ダウンロードはしない）
echo ""
echo "[6/6] Checking model ($MODEL)..."
if ! ollama list | awk -v expected="$MODEL" '$1 == expected { found = 1 } END { exit(found ? 0 : 1) }'; then
    echo "ERROR: $MODEL is not installed."
    echo "Run this once while online: ollama pull $MODEL"
    exit 1
fi
echo "  OK: $MODEL available"

# 完了
echo ""
echo "======================================"
echo "Setup complete!"
echo "======================================"
echo ""
echo "Next steps:"
echo "  1. Review .env (Google Sheets credentials are not required)"
echo "  2. For the complete local setup, run: bash setup_local.sh"
echo "  3. Run: bash run_local.sh --iterations 1"
echo ""
