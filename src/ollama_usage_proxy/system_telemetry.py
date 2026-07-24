"""GPU / hardware telemetry gatherers.

Strictly limited to Linux hosts with NVIDIA GPUs via ``nvidia-ml-py``.  On any
other platform (or when the library cannot initialise) the monitor exposes
``active = False`` so that callers can skip hardware reads without crashing.
"""

from __future__ import annotations

import logging
import platform
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class GPUSnapshot:
    """Single point-in-time read of GPU telemetry."""

    gpu_temp_c: Optional[float] = None
    gpu_power_w: Optional[float] = None
    gpu_util_pct: Optional[float] = None


# ── Platform guard ───────────────────────────────────────────────────────

_running_on_linux = platform.system() == "Linux"


def _try_import_nvmllib():
    """Lazy-import NVML (nvidia-ml-py) so non-Linux hosts don't fail at module load."""
    if not _running_on_linux:
        return None
    try:
        import pynvml
        return pynvml
    except ImportError:
        logger.debug("NVML not available – GPU telemetry disabled")
        return None
    except Exception as exc:  # defensive: some environments raise RuntimeError on import
        logger.debug("NVML initialisation failed (%s) – GPU telemetry disabled", exc)
        return None


# ── NVIDIA monitor ──────────────────────────────────────────────────────


class LinuxNvidiaMonitor:
    """Reads GPU metrics via NVML on Linux.

    Parameters
    ----------
    nvmllib:
        An optional pre-imported ``NVML`` reference.  When *None* the
        monitor will attempt to import it lazily.

    Attributes
    ----------
    active:
        ``True`` only when NVML initialised successfully and at least one
        GPU handle is available.
    """

    def __init__(self, nvmllib=None):
        self._nvml = nvmllib if nvmllib is not None else _try_import_nvmllib()
        self._handles: list = []
        self.active = False

        if self._nvml is None:
            logger.info("GPU telemetry offline (not Linux / NVML unavailable)")
            return

        try:
            self._nvml.nvmlInit()
            device_count = self._nvml.nvmlDeviceGetCount()
            if device_count <= 0:
                logger.info("GPU telemetry offline (no NVIDIA devices detected)")
                return
            for idx in range(device_count):
                handle = self._nvml.nvmlDeviceGetHandleByIndex(idx)
                self._handles.append(handle)
            self.active = True
            logger.info("GPU telemetry online (%d NVIDIA device(s))", device_count)
        except Exception as exc:
            logger.info("GPU telemetry offline (NVML error: %s)", exc)

    # ── public API ─────────────────────────────────────────────────────

    def get_telemetry(self) -> GPUSnapshot | None:
        """Return aggregated telemetry across all visible NVIDIA GPUs.

        Returns ``None`` when the monitor is not active.
        """
        if not self.active or self._nvml is None:
            return None

        try:
            total_temp = 0.0
            total_power_mw = 0  # milliwatts internally
            total_util = 0.0

            for handle in self._handles:
                # Temperature (°C)
                temp = self._nvml.nvmlDeviceGetTemperature(
                    handle, self._nvml.NVML_TEMPERATURE_GPU
                )
                total_temp += float(temp)

                # Power draw (milliwatts -> watts)
                power_mw = self._nvml.nvmlDeviceGetPowerUsage(handle)
                total_power_mw += int(power_mw)

                # GPU core utilisation (%)
                util = self._nvml.nvmlDeviceGetUtilizationRates(handle)
                total_util += float(util.gpu)

            device_count = len(self._handles)

            return GPUSnapshot(
                gpu_temp_c=round(total_temp / device_count, 1),
                gpu_power_w=round(total_power_mw / device_count / 1000.0, 2),
                gpu_util_pct=round(total_util / device_count, 1),
            )
        except Exception as exc:
            logger.debug("Failed to read GPU telemetry: %s", exc)
            return None

    def close(self):
        """Release NVML resources."""
        if self._nvml is not None and self.active:
            try:
                self._nvml.nvmlShutdown()
            except Exception as exc:
                logger.debug("nvmlShutdown warning: %s", exc)
            finally:
                self.active = False
                self._handles.clear()

    def __del__(self):
        self.close()


# ── Module-level singleton ───────────────────────────────────────────────

_monitor: LinuxNvidiaMonitor | None = None


def get_monitor() -> LinuxNvidiaMonitor | None:
    """Return (creating if necessary) a module-level GPU monitor instance.

    Returns ``None`` when the platform is unsuitable so callers never need
    to wrap this in a ``try/except``.
    """
    global _monitor
    if _monitor is None:
        _monitor = LinuxNvidiaMonitor()
    return _monitor if _monitor.active else None