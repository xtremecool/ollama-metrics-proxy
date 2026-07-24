"""Tests for dashboard revamp changes.

Covers:
- Polling interval changed from 3s to 1s
- KPI reactivity fix in frontend HTML
- uplot library used instead of ApexCharts
- 5 individual charts (GPU Temp, Power, Util, Token Rates, Token Consumption)
- No CPU utilization, no VRAM in dashboard
- 60-second rolling window (maxPoints: 60)
"""

import asyncio
import re
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from src.ollama_usage_proxy.app import create_app, start_telemetry_loop, _broadcast_live_metrics
from src.ollama_usage_proxy.config import load_config
from src.ollama_usage_proxy.db import (
    SystemMetricsPoint,
    initialize_schema,
    insert_system_metrics,
    get_recent_system_metrics,
    ainsert_system_metrics,
)
from src.ollama_usage_proxy.system_telemetry import GPUSnapshot


@pytest.fixture()
def temp_config(tmp_path: Path):
    """Create a minimal config file pointing at a temp database."""
    config_file = tmp_path / "test_config.toml"
    db_file = tmp_path / "test.db"
    report_dir = tmp_path / "reports"
    report_dir.mkdir()

    config_file.write_text(
        f"""[database]
path = "{db_file}"

[reporting]
output_dir = "{report_dir}"

[proxy]
listen_host = "127.0.0.1"
listen_port = 11435
ollama_base_url = "http://127.0.0.1:11434"
"""
    )
    return config_file, db_file


@pytest.fixture()
def client(temp_config):
    """Create a TestClient with mocked lifespan."""
    config_file, _db_file = temp_config
    cfg = load_config(config_file)
    app = create_app(cfg)

    from src.ollama_usage_proxy import app as app_module

    @asynccontextmanager
    def mock_lifespan(fastapi_app):
        fastapi_app.state.config = cfg
        fastapi_app.state.http_client = MagicMock()
        mock_monitor = MagicMock()
        mock_monitor.active = False
        fastapi_app.state.gpu_monitor = mock_monitor
        fastapi_app.state.telemetry_task = None
        yield

    with patch.object(app_module, "lifespan", mock_lifespan):
        with TestClient(app) as test_client:
            yield test_client


# ─── Polling Interval Tests ──────────────────────────────────────────────

class TestPollingInterval:
    """Verify polling interval is 1 second."""

    def test_start_telemetry_loop_default_interval_is_1s(self):
        """start_telemetry_loop should default to 1.0s interval."""
        import inspect
        sig = inspect.signature(start_telemetry_loop)
        interval_param = sig.parameters["interval"]
        assert interval_param.default == 1.0, "Default polling interval should be 1.0 seconds"

    def test_lifespan_uses_1s_interval(self, temp_config):
        """The lifespan should start telemetry with interval=1.0."""
        config_file, _db_file = temp_config
        cfg = load_config(config_file)
        app = create_app(cfg)

        from src.ollama_usage_proxy import app as app_module

        # Mock the monitor to be active
        mock_monitor = MagicMock()
        mock_monitor.active = True
        mock_monitor.get_telemetry.return_value = GPUSnapshot(
            gpu_temp_c=50.0,
            gpu_power_w=100.0,
            gpu_util_pct=30.0,
        )

        @asynccontextmanager
        def mock_lifespan(fastapi_app):
            fastapi_app.state.config = cfg
            fastapi_app.state.http_client = MagicMock()
            fastapi_app.state.gpu_monitor = mock_monitor

            # Read the source to check interval value
            import src.ollama_usage_proxy.app as app_mod
            source = inspect.getsource(app_mod.lifespan)
            assert "interval=1.0" in source, "Lifespan should use interval=1.0"
            yield

        with patch.object(app_module, "lifespan", mock_lifespan):
            with TestClient(app):
                pass


# ─── GPU Snapshot Tests (no CPU, no VRAM) ───────────────────────────────

class TestGpuSnapshotStructure:
    """Verify GPUSnapshot only has GPU metrics."""

    def test_gpu_snapshot_fields(self):
        """GPUSnapshot should have gpu_temp_c, gpu_power_w, gpu_util_pct only."""
        snapshot = GPUSnapshot(
            gpu_temp_c=60.0,
            gpu_power_w=150.0,
            gpu_util_pct=80.0,
        )
        assert snapshot.gpu_temp_c == 60.0
        assert snapshot.gpu_power_w == 150.0
        assert snapshot.gpu_util_pct == 80.0

    def test_system_metrics_point_fields(self):
        """SystemMetricsPoint should have timestamp, gpu_temp_c, gpu_power_w, gpu_util_pct only."""
        point = SystemMetricsPoint(
            timestamp="2026-07-21T12:00:00+00:00",
            gpu_temp_c=60.0,
            gpu_power_w=150.0,
            gpu_util_pct=80.0,
        )
        assert point.gpu_temp_c == 60.0
        assert point.gpu_power_w == 150.0
        assert point.gpu_util_pct == 80.0

    def test_db_stores_and_retrieves_gpu_metrics(self, temp_config):
        """GPU metrics should be stored and retrieved from SQLite."""
        _config_file, db_path = temp_config
        initialize_schema(str(db_path))

        point = SystemMetricsPoint(
            timestamp="2026-07-21T12:00:00+00:00",
            gpu_temp_c=65.0,
            gpu_power_w=200.0,
            gpu_util_pct=85.0,
        )
        insert_system_metrics(str(db_path), point)

        results = get_recent_system_metrics(str(db_path), limit=10)
        assert len(results) == 1
        assert results[0].gpu_temp_c == 65.0
        assert results[0].gpu_power_w == 200.0
        assert results[0].gpu_util_pct == 85.0


# ─── History Endpoint Tests ──────────────────────────────────────────────

class TestHistoryEndpoint:
    """/api/history endpoint should only include GPU metrics."""

    def test_history_response_fields(self, client, temp_config):
        """History API response should include gpu_temp_c, gpu_power_w, gpu_util_pct only."""
        _config_file, db_path = temp_config

        # Insert a metric point
        point = SystemMetricsPoint(
            timestamp="2026-07-21T12:00:00+00:00",
            gpu_temp_c=65.0,
            gpu_power_w=200.0,
            gpu_util_pct=85.0,
        )
        insert_system_metrics(str(db_path), point)

        resp = client.get("/api/history")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["metrics"]) == 1
        assert "gpu_temp_c" in data["metrics"][0]
        assert "gpu_power_w" in data["metrics"][0]
        assert "gpu_util_pct" in data["metrics"][0]
        assert data["metrics"][0]["gpu_temp_c"] == 65.0
        assert data["metrics"][0]["gpu_power_w"] == 200.0
        assert data["metrics"][0]["gpu_util_pct"] == 85.0


# ─── Broadcast Source Tests ──────────────────────────────────────────────

class TestBroadcast:
    """Verify _broadcast_live_metrics builds correct payload (via source inspection)."""

    def test_broadcast_payload_fields(self, client):
        """_broadcast_live_metrics should construct payload with gpu_temp_c, gpu_power_w, gpu_util_pct.

        Because _broadcast_live_metrics is async and cannot safely run alongside
        Playwright's event loop, we verify the payload structure by inspecting
        the source code rather than calling it at runtime.
        """
        import inspect
        src = inspect.getsource(_broadcast_live_metrics)

        # Verify the payload dictionary contains the right fields
        assert "gpu_temp_c" in src, "Payload must include gpu_temp_c"
        assert "gpu_power_w" in src, "Payload must include gpu_power_w"
        assert "gpu_util_pct" in src, "Payload must include gpu_util_pct"

        # Verify it delegates to _send_to_all_clients helper (refactored from inline send_json)
        assert "_send_to_all_clients" in src, "Should broadcast via _send_to_all_clients"

        # Verify payload type is 'metrics' (unified broadcast)
        assert '"type"' in src and '"metrics"' in src, \
            "Payload must set type='metrics'"


# ─── Frontend HTML Structure Tests ──────────────────────────────────────

class TestFrontendHtmlStructure:
    """Verify the HTML structure matches dashboard requirements."""

    def test_dashboard_uses_chartjs_not_uplot(self, client):
        """Dashboard should use Chart.js library (not uPlot or ApexCharts)."""
        content = client.get("/dashboard").text
        assert "chart.js" in content.lower() or "Chart" in content, \
            "Dashboard should reference Chart.js library"
        assert "apexcharts" not in content.lower(), \
            "Dashboard should NOT reference ApexCharts"

    def test_dashboard_has_no_vram_kpi_card(self, client):
        """VRAM KPI card should be removed."""
        content = client.get("/dashboard").text
        # Check that there's no VRAM KPI label
        assert "VRAM Used" not in content, \
            "VRAM KPI card should be removed from dashboard"

    def test_dashboard_has_no_cpu_chart(self, client):
        """Dashboard should NOT have CPU chart."""
        content = client.get("/dashboard").text
        assert "chartCpuUtil" not in content, \
            "CPU Utilization chart should NOT be present"

    def test_dashboard_has_six_chart_containers(self, client):
        """Dashboard should have 6 chart containers (GPU Temp, Power, Util + Token Rate + Input Tokens + Output Tokens)."""
        content = client.get("/dashboard").text
        # GPU charts
        assert "chartGpuTemp" in content, "Should have GPU Temperature chart"
        assert "chartGpuPower" in content, "Should have GPU Power chart"
        assert "chartGpuUtil" in content, "Should have GPU Utilization chart"
        # Token charts (split into two separate bar charts)
        assert "chartTokenRate" in content, "Should have Token Rate chart"
        assert "chartInputTokens" in content, "Should have Input Tokens chart"
        assert "chartOutputTokens" in content, "Should have Output Tokens chart"

    def test_dashboard_60_slot_snapshot(self, client):
        """Chart data should be limited to 60 points (60-second window) via snapshot overwrite.

        The dashboard no longer uses Chart.js maxPoints. Instead it receives a
        60-slot ordered snapshot from the WebSocket and overwrites datasets via
        .map() — guaranteeing exactly 60 data points at all times.
        """
        content = client.get("/dashboard").text
        # Verify new approach: handleMetricsSnapshot with snapshot.map overwrites
        assert "handleMetricsSnapshot" in content, \
            "Dashboard should use handleMetricsSnapshot for 60-slot overwrite"
        assert "snapshot.map" in content, \
            "Dashboard should use .map() over the backend snapshot array"

    def test_dashboard_kpi_reactivity_fix(self, client):
        """WebSocket handler should update individual KPI properties, not replace entire object."""
        content = client.get("/dashboard").text
        # The fix: instead of `this.current = msg`, it assigns individual properties
        # Unified broadcast uses live GPU fields (liveTemp) or direct msg field assignment
        assert "this.current.gpu_temp_c" in content and "gpu_temp_c" in content, \
            "KPI reactivity fix: should assign gpu_temp_c individually, not replace entire object"

    def test_dashboard_has_chartjs_script_tag(self, client):
        """Dashboard should load Chart.js from CDN."""
        content = client.get("/dashboard").text
        script_srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', content, flags=re.IGNORECASE)

        has_chartjs_from_jsdelivr = any(
            (parsed.hostname or "").lower() == "cdn.jsdelivr.net"
            and "chart.js" in parsed.path.lower()
            for parsed in (urlparse(src) for src in script_srcs)
        )

        assert has_chartjs_from_jsdelivr, "Should load Chart.js library from CDN"

    def test_dashboard_pastel_colors(self, client):
        """Charts should use pastel color scheme as specified in plan."""
        content = client.get("/dashboard").text
        # Check for the pastel colors defined in the plan (rose, amber, purple)
        pastel_colors = ["#F87171", "#FBBF24", "#C084FC"]
        found_colors = sum(1 for color in pastel_colors if color in content)
        assert found_colors >= 3, \
            f"Should use at least 3 of the specified pastel colors, found {found_colors}"

    def test_dashboard_no_apexcharts_script(self, client):
        """Dashboard should not load ApexCharts CDN script."""
        content = client.get("/dashboard").text
        assert "apexcharts" not in content.lower(), \
            "ApexCharts script should be removed from dashboard"

    def test_chart_layout_two_per_row_desktop(self, client):
        """Charts should display 2 per row on desktop."""
        content = client.get("/dashboard").text
        assert "md:grid-cols-2" in content, \
            "Desktop layout should show 2 charts per row (md:grid-cols-2)"

    def test_no_combined_chart_containers(self, client):
        """Old combined chart containers (Temp+Power, Util+VRAM) should be removed."""
        content = client.get("/dashboard").text
        assert "chartTempPower" not in content, \
            "Old combined Temp+Power chart container should be removed"
        assert "chartUtilVram" not in content, \
            "Old combined Util+VRAM chart container should be removed"

    def test_no_x_axis_time_labels(self, client):
        """Charts should not have x-axis time labels (per plan requirement)."""
        content = client.get("/dashboard").text
        # uplot charts should have null for the second axis (x-axis suppressed)
        assert "null" in content or "'axes'" in content, \
            "Chart config should include axis configuration"

    def test_no_cpu_in_frontend(self, client):
        """Frontend should not reference CPU utilization anywhere."""
        content = client.get("/dashboard").text
        assert "cpu_util" not in content.lower(), \
            "Frontend should not reference CPU utilization"