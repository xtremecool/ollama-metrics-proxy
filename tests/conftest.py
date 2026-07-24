"""Shared fixtures for unit tests and Playwright E2E tests."""

import sys
import subprocess
import tempfile
import time
import socket
from pathlib import Path

import pytest
import requests


def _find_free_port() -> int:
    """Find an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(url: str, timeout: float = 15.0):
    """Block until the server responds on *url*/health."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(f"{url}/health", timeout=2)
            if resp.status_code == 200:
                return True
        except requests.ConnectionError:
            pass
        time.sleep(0.3)
    raise RuntimeError(f"Server did not start at {url} within {timeout}s")


@pytest.fixture(scope="session")
def temp_db():
    """Create a temporary database for all tests in this session."""
    db_path = Path(tempfile.mkdtemp()) / "test_e2e.db"
    yield db_path
    db_path.unlink(missing_ok=True)
    db_path.parent.rmdir()


@pytest.fixture(scope="session")
def temp_config(tmp_path_factory: pytest.TempPathFactory, temp_db):
    """Create a minimal config file pointing at the session-wide temp database."""
    tmp_dir = tmp_path_factory.mktemp("e2e_config")
    config_file = tmp_dir / "test_config.toml"
    report_dir = tmp_dir / "reports"
    report_dir.mkdir()

    port = _find_free_port()

    config_file.write_text(
        f"""[database]
path = "{temp_db}"

[reporting]
output_dir = "{report_dir}"

[proxy]
listen_host = "127.0.0.1"
listen_port = {port}
ollama_base_url = "http://127.0.0.1:11434"
"""
    )
    return config_file, temp_db, port


@pytest.fixture(scope="session")
def server(temp_config):
    """Start the real FastAPI server in a subprocess for E2E Playwright tests."""
    config_file, _db_file, port = temp_config
    url = f"http://127.0.0.1:{port}"

    # Use a small Python script to boot the app via get_app() instead of
    # relying on a module-level `app` object (which would conflict with
    # unit tests that mock the lifespan).
    # Includes a _mock_data_feeder() async task that pushes synthetic
    # sinusoidal GPU telemetry and periodic token requests into
    # metrics_buffer so chart-rendering tests have predictable data.
    script = Path(__file__).resolve().parent.parent / "scripts" / "_bootstrap_server.py"
    script.write_text(
        f"""\
import os, sys, math, asyncio, threading, time
from datetime import datetime, timezone
sys.path.insert(0, '.')
os.environ["OLLAMA_PROXY_CONFIG"] = "{config_file}"
from src.ollama_usage_proxy.app import (
    get_app, metrics_buffer, _broadcast_request_complete,
)
import uvicorn


def _mock_data_feeder_thread():
    \"\"\"Feed synthetic GPU telemetry in a background thread with its own event loop.\"\"\"
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def feed():
        i = 0
        while True:
            temp = 65 + 10 * math.sin(i * 0.2)
            power = 280 + 70 * math.sin(i * 0.15 + 1)
            util = 60 + 25 * math.sin(i * 0.1 + 2)

            # Push GPU data into ring buffer (synchronous, thread-safe)
            metrics_buffer.push_gpu_data(temp, power, util)

            # Inject a synthetic token request every 5 ticks
            if i % 5 == 0:
                await _broadcast_request_complete(
                    model="llama3", input_tokens=256,
                    output_tokens=512, output_tps=50.0,
                )

            # push live-metrics snapshot over WebSocket so charts receive data
            from src.ollama_usage_proxy.app import _broadcast_live_metrics
            await _broadcast_live_metrics()
            i += 1
            await asyncio.sleep(1.0)

    loop.run_until_complete(feed())


# Start mock data feeder in a daemon thread BEFORE uvicorn so it feeds data
# as soon as WebSocket clients connect after the lifespan starts.
t = threading.Thread(target=_mock_data_feeder_thread, daemon=True)
t.start()

app = get_app()
uvicorn.run(app, host="127.0.0.1", port={port}, log_level="warning")
"""
    )

    proc = subprocess.Popen(
        [
            sys.executable, str(script),
        ],
        env={**__import__("os").environ, "OLLAMA_PROXY_CONFIG": str(config_file)},
        cwd=str(Path(__file__).resolve().parent.parent),
    )

    try:
        _wait_for_server(url)
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        script.unlink(missing_ok=True)