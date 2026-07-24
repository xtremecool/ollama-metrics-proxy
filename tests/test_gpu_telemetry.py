"""Integration tests for GPU telemetry collection on Linux + NVIDIA.

These tests only run when the environment actually has:
1. A Linux host (platform.system() == 'Linux')
2. nvidia-ml-py installed and importable as 'pynvml'
3. At least one NVIDIA GPU detected by NVML

On CI or non-NVIDIA machines the entire test class is skipped gracefully.
"""

from __future__ import annotations

import platform

import pytest

_linux = platform.system() == "Linux"


def _nvidia_available():
    """Return True when NVML can initialise and find at least one GPU."""
    if not _linux:
        return False
    try:
        import pynvml
    except ImportError:
        return False

    try:
        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        pynvml.nvmlShutdown()
        return count > 0
    except Exception:
        return False


_nvidia = _nvidia_available()


@pytest.mark.skipif(not _linux, reason="Requires a Linux host for GPU telemetry")
class TestGpuTelemetryLinux:
    """Tests that only run on Linux."""

    def test_running_on_linux(self):
        """Confirm we are actually on Linux."""
        assert platform.system() == "Linux"

    def test_pynvml_importable(self):
        """nvidia-ml-py must be importable as 'pynvml'."""
        import pynvml  # noqa: F811

        assert hasattr(pynvml, "nvmlInit")


@pytest.mark.skipif(
    not _nvidia, reason="Requires Linux with NVIDIA GPU and NVML drivers"
)
class TestGpuTelemetryNvidia:
    """Integration tests that query real GPU hardware.

    These tests are skipped on CI / non-NVIDIA machines so the suite still
    passes everywhere, but validate end-to-end telemetry collection when run
    locally on a machine with an NVIDIA card.
    """

    def test_nvml_initializes(self):
        """NVML should initialise without error."""
        import pynvml

        pynvml.nvmlInit()
        try:
            count = pynvml.nvmlDeviceGetCount()
            assert count >= 1, f"Expected at least 1 GPU, got {count}"
        finally:
            pynvml.nvmlShutdown()

    def test_linux_nvidia_monitor_active(self):
        """LinuxNvidiaMonitor should be active on a machine with NVIDIA GPU."""
        # Reset the module-level singleton so we get a fresh instance
        from src.ollama_usage_proxy import system_telemetry

        system_telemetry._monitor = None

        monitor = system_telemetry.get_monitor()
        assert monitor is not None, "get_monitor() should return a monitor on NVIDIA Linux"
        assert monitor.active, "Monitor should be active when NVIDIA GPU is present"
        monitor.close()

    def test_get_telemetry_returns_snapshot(self):
        """get_telemetry() should return a GPUSnapshot with real values."""
        from src.ollama_usage_proxy import system_telemetry
        from src.ollama_usage_proxy.system_telemetry import GPUSnapshot

        system_telemetry._monitor = None

        monitor = system_telemetry.get_monitor()
        assert monitor is not None and monitor.active

        snapshot = monitor.get_telemetry()
        assert snapshot is not None, "get_telemetry should return a snapshot, not None"
        assert isinstance(snapshot, GPUSnapshot)

        # Temperature: reasonable range 20-100 °C for an NVIDIA GPU
        assert snapshot.gpu_temp_c is not None
        assert 20 <= snapshot.gpu_temp_c <= 100, (
            f"GPU temperature {snapshot.gpu_temp_c}°C out of expected range"
        )

        # Power draw: reasonable range 5-600 W
        assert snapshot.gpu_power_w is not None
        assert 5 <= snapshot.gpu_power_w <= 600, (
            f"GPU power {snapshot.gpu_power_w}W out of expected range"
        )

        # Utilization: 0-100%
        assert snapshot.gpu_util_pct is not None
        assert 0 <= snapshot.gpu_util_pct <= 100, (
            f"GPU utilization {snapshot.gpu_util_pct}% out of expected range"
        )

        monitor.close()

    def test_multiple_telemetry_reads_succeed(self):
        """Multiple sequential reads should all return valid snapshots."""
        from src.ollama_usage_proxy import system_telemetry

        system_telemetry._monitor = None
        monitor = system_telemetry.get_monitor()
        assert monitor is not None and monitor.active

        for _ in range(5):
            snap = monitor.get_telemetry()
            assert snap is not None
            assert snap.gpu_temp_c is not None
            assert snap.gpu_power_w is not None
            assert snap.gpu_util_pct is not None

        monitor.close()

    def test_device_name_retrievable(self):
        """Should be able to retrieve at least one GPU device name."""
        import pynvml

        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            name = pynvml.nvmlDeviceGetName(handle)
            # Name can be a string or bytes depending on NVML version
            assert name is not None, "Device name should not be None"
            name_str = name if isinstance(name, str) else name.decode()
            assert len(name_str) > 0, "Device name should not be empty"
        finally:
            pynvml.nvmlShutdown()

    def test_telemetry_stored_in_database(self):
        """Telemetry read from GPU should persist to SQLite and be retrievable."""
        import tempfile
        import time

        from src.ollama_usage_proxy.db import (
            SystemMetricsPoint,
            initialize_schema,
            insert_system_metrics,
            get_recent_system_metrics,
        )
        from src.ollama_usage_proxy import system_telemetry

        # Fresh monitor
        system_telemetry._monitor = None
        monitor = system_telemetry.get_monitor()
        assert monitor is not None and monitor.active

        snap = monitor.get_telemetry()
        assert snap is not None

        # Write to a temp database
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            initialize_schema(tmp.name)

            point = SystemMetricsPoint(
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                gpu_temp_c=snap.gpu_temp_c,
                gpu_power_w=snap.gpu_power_w,
                gpu_util_pct=snap.gpu_util_pct,
            )
            insert_system_metrics(tmp.name, point)

            results = get_recent_system_metrics(tmp.name, limit=10)
            assert len(results) == 1
            assert results[0].gpu_temp_c == snap.gpu_temp_c
            assert results[0].gpu_power_w == snap.gpu_power_w
            assert results[0].gpu_util_pct == snap.gpu_util_pct

        monitor.close()


# ── Summary test (always runs, reports skip status) ───────────────────────


class TestGpuTelemetryAvailability:
    """Always-runs summary of GPU telemetry availability."""

    def test_linux_check(self):
        """Report whether running on Linux."""
        assert platform.system() in ("Linux", "Darwin", "Windows"), "Unknown platform"

    def test_pynvml_available_when_linux(self):
        """If on Linux, pynvml should be importable (nvidia-ml-py installed)."""
        if not _linux:
            pytest.skip("Not on Linux")

        import pynvml  # noqa: F811

        assert hasattr(pynvml, "nvmlInit")