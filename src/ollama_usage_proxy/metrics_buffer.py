"""In-memory sliding ring buffer for high-frequency telemetry metrics.

Maintains a fixed-size 60-second time-series window using lock-protected
slot bucketing to achieve O(1) memory and allocation overhead.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock
from typing import Any


@dataclass
class MetricSlot:
    """A 1-second metric bucket inside the ring buffer."""

    timestamp: int = 0
    gpu_temp_c: float | None = None
    gpu_power_w: float | None = None
    gpu_util_pct: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    request_count: int = 0

    def reset(self, sec: int) -> None:
        """Reset slot data for a new timestamp second."""
        self.timestamp = sec
        self.gpu_temp_c = None
        self.gpu_power_w = None
        self.gpu_util_pct = None
        self.input_tokens = 0
        self.output_tokens = 0
        self.request_count = 0


class TimeSeriesRingBuffer:
    """Thread-safe 60-second sliding time-series ring buffer.

    Pre-allocates 60 slots and maps absolute Unix timestamps (seconds) to
    slots via `unix_second % 60`. Automatically handles forward-filling
    for minor timing drifts between telemetry polling and UI broadcast.
    """

    BUFFER_SIZE = 60

    def __init__(self) -> None:
        self._lock = Lock()
        self._buffer: list[MetricSlot] = [MetricSlot() for _ in range(self.BUFFER_SIZE)]

    def push_gpu_data(
        self,
        temp_c: float | None,
        power_w: float | None,
        util_pct: float | None,
        timestamp: float | None = None,
    ) -> None:
        """Push a GPU telemetry sample into the slot corresponding to its timestamp."""
        ts = int(timestamp if timestamp is not None else time.time())
        idx = ts % self.BUFFER_SIZE

        with self._lock:
            slot = self._buffer[idx]
            if slot.timestamp != ts:
                slot.reset(ts)

            slot.gpu_temp_c = temp_c
            slot.gpu_power_w = power_w
            slot.gpu_util_pct = util_pct

    def push_ollama_request(
        self,
        completion_time_ms: float,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Accumulate a completed request's tokens into its completion slot."""
        ts = int(completion_time_ms / 1000.0)
        idx = ts % self.BUFFER_SIZE

        with self._lock:
            slot = self._buffer[idx]
            if slot.timestamp != ts:
                slot.reset(ts)

            slot.input_tokens += input_tokens
            slot.output_tokens += output_tokens
            slot.request_count += 1

    def get_ordered_snapshot(self) -> list[dict[str, Any]]:
        """Return exactly 60 time-ordered slots (T-59 … T) as JSON-ready dicts.

        Forward-fills missing/unpolled GPU slots with the most recent valid
        readings to eliminate 1-second timing drift gaps in frontend charts.
        """
        with self._lock:
            current_sec = int(time.time())

            # Seed last known valid GPU readings from any slot in the buffer
            # to handle potential gaps right at the start of the 60s window.
            last_gpu_temp = None
            last_gpu_power = None
            last_gpu_util = None

            # Find the newest valid reading in the ring buffer to initialize state
            for s in self._buffer:
                if s.gpu_temp_c is not None:
                    last_gpu_temp = s.gpu_temp_c
                    last_gpu_power = s.gpu_power_w
                    last_gpu_util = s.gpu_util_pct

            result: list[dict[str, Any]] = []

            for offset in range(59, -1, -1):
                target_sec = current_sec - offset
                idx = target_sec % self.BUFFER_SIZE
                slot = self._buffer[idx]

                if slot.timestamp == target_sec and slot.gpu_temp_c is not None:
                    # Valid fresh slot - update last known readings
                    last_gpu_temp = slot.gpu_temp_c
                    last_gpu_power = slot.gpu_power_w
                    last_gpu_util = slot.gpu_util_pct

                    result.append({
                        "timestamp": slot.timestamp,
                        "gpu_temp_c": slot.gpu_temp_c,
                        "gpu_power_w": slot.gpu_power_w,
                        "gpu_util_pct": slot.gpu_util_pct,
                        "input_tokens": slot.input_tokens,
                        "output_tokens": slot.output_tokens,
                        "request_count": slot.request_count,
                    })
                else:
                    # Skipped/unpolled slot — carry forward last known GPU readings
                    result.append({
                        "timestamp": target_sec,
                        "gpu_temp_c": last_gpu_temp,
                        "gpu_power_w": last_gpu_power,
                        "gpu_util_pct": last_gpu_util,
                        "input_tokens": slot.input_tokens if slot.timestamp == target_sec else 0,
                        "output_tokens": slot.output_tokens if slot.timestamp == target_sec else 0,
                        "request_count": slot.request_count if slot.timestamp == target_sec else 0,
                    })

            return result

