#!/usr/bin/env bash
# Bootstrap script for ollama-usage-proxy
# Builds PyInstaller executables and runs the proxy.
# Usage:
#   ./scripts/run.sh [proxy arguments...]
# Example:
#   ./scripts/run.sh --port 11435 --log-level debug

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$PROJECT_DIR/dist"

PROXY_BIN="$DIST_DIR/ollama-proxy/ollama-proxy"
REPORT_BIN="$DIST_DIR/ollama-report/ollama-report"

# Build if executables don't exist
if [ ! -f "$PROXY_BIN" ] || [ ! -f "$REPORT_BIN" ]; then
    echo "PyInstaller builds not found. Building..."

    # Activate venv if available
    if [ -d "$PROJECT_DIR/.venv" ]; then
        source "$PROJECT_DIR/.venv/bin/activate"
    fi

    python3 -m pip install pyinstaller -q 2>/dev/null

    pyinstaller \
        --clean \
        --name ollama-proxy \
        --add-data "src/ollama_usage_proxy/default_config.toml:ollama_usage_proxy" \
        --add-data "src/ollama_usage_proxy/default_prices.toml:ollama_usage_proxy" \
        --hidden-import=ollama_usage_proxy.config \
        --hidden-import=ollama_usage_proxy.db \
        --hidden-import=ollama_usage_proxy.usage \
        --hidden-import=ollama_usage_proxy.models \
        --hidden-import=ollama_usage_proxy.pricing \
        --hidden-import=tomli \
        --exclude-module=tkinter \
        "$PROJECT_DIR/src/ollama_usage_proxy/app.py"

    pyinstaller \
        --clean \
        --name ollama-report \
        --add-data "src/ollama_usage_proxy/default_config.toml:ollama_usage_proxy" \
        --add-data "src/ollama_usage_proxy/default_prices.toml:ollama_usage_proxy" \
        --hidden-import=ollama_usage_proxy.pricing \
        --hidden-import=tomli \
        --exclude-module=tkinter \
        "$PROJECT_DIR/src/ollama_usage_proxy/report_main.py"

    echo "Build complete."

    # Smoke test: verify executables respond to --help
    echo "Running smoke tests..."
    if ! "$PROXY_BIN" --help >/dev/null 2>&1; then
        echo "ERROR: proxy executable failed smoke test" >&2
        exit 1
    fi
    echo "  ollama-proxy: OK"

    if ! "$REPORT_BIN" --help >/dev/null 2>&1; then
        echo "ERROR: report executable failed smoke test" >&2
        exit 1
    fi
    echo "  ollama-report: OK"

    # Quick health check: start proxy on port 20000, hit /health, then stop
    echo "Testing live proxy on port 20000..."
    "$PROXY_BIN" --port 20000 --log-level error &
    PROXY_PID=$!
    sleep 1

    if curl -sf http://127.0.0.1:20000/health >/dev/null 2>&1; then
        echo "  Health check: OK"
    else
        echo "WARNING: Proxy health check failed (may be expected if Ollama is not running)"
    fi

    kill "$PROXY_PID" 2>/dev/null || true
    wait "$PROXY_PID" 2>/dev/null || true
fi

# Run the proxy, passing through any extra arguments.
# Default to port 20000 for build/testing to avoid conflict with a running proxy on 11435.
if [[ ! " $* " =~ " --port " ]]; then
    exec "$PROXY_BIN" --port 20000 "$@"
else
    exec "$PROXY_BIN" "$@"
fi