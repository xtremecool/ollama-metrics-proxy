"""Comprehensive unit tests for the TimeSeriesRingBuffer (60-slot circular buffer).

Covers:
- Fixed 60-slot array always returns exactly 60 elements
- GPU telemetry written to correct second bucket via push_gpu_data
- Ollama request tokens accumulated additively via push_ollama_request
- Minute wrap-around handled correctly (timestamp validation resets slots)
- Missing/unpolled slots padded with zeroed defaults
- Thread-safety under concurrent pushes
- Snapshot returns correct JSON structure
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from src.ollama_usage_proxy.metrics_buffer import TimeSeriesRingBuffer, MetricSlot


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def buffer():
    """Return a fresh TimeSeriesRingBuffer."""
    return TimeSeriesRingBuffer()


# ── Helpers ──────────────────────────────────────────────────────────────


def _fake_unix_second(offset: int = 0) -> int:
    """Return a deterministic Unix second based on current time + offset."""
    return int(time.time()) + offset


# ── Buffer structure tests ──────────────────────────────────────────────


class TestBufferStructure:
    """Ring buffer should always return exactly 60 elements."""

    def test_ordered_snapshot_always_60(self, buffer):
        snap = buffer.get_ordered_snapshot()
        assert len(snap) == 60

    def test_all_slots_pre_allocated(self, buffer):
        """Internal buffer should have exactly 60 MetricSlot instances."""
        assert len(buffer._buffer) == 60
        assert all(isinstance(s, MetricSlot) for s in buffer._buffer)

# ── GPU telemetry push tests ────────────────────────────────────────────


class TestPushGpuData:
    """GPU metrics should be written to the correct second bucket."""

    def test_push_gpu_data_writes_to_current_slot(self, buffer):
        with patch("time.time", return_value=1000.0):
            buffer.push_gpu_data(65.0, 200.0, 80.0)

        with patch("time.time", return_value=1000.0):
            snap = buffer.get_ordered_snapshot()

        # Find the slot for second 1000
        target = [s for s in snap if s["timestamp"] == 1000]
        assert len(target) == 1
        assert target[0]["gpu_temp_c"] == 65.0
        assert target[0]["gpu_power_w"] == 200.0
        assert target[0]["gpu_util_pct"] == 80.0

    def test_push_gpu_data_overwrites_previous_sample(self, buffer):
        """Second push to same second should overwrite GPU values."""
        with patch("time.time", return_value=1000.0):
            buffer.push_gpu_data(60.0, 150.0, 70.0)
            buffer.push_gpu_data(70.0, 250.0, 90.0)

        with patch("time.time", return_value=1000.0):
            snap = buffer.get_ordered_snapshot()

        target = [s for s in snap if s["timestamp"] == 1000]
        assert target[0]["gpu_temp_c"] == 70.0
        assert target[0]["gpu_power_w"] == 250.0
        assert target[0]["gpu_util_pct"] == 90.0

    def test_slot_reset_on_new_second(self, buffer):
        """Pushing to a new second should reset token counts from prior minute data."""
        # First write at second 100 (writes to index 40)
        with patch("time.time", return_value=100.0):
            buffer.push_gpu_data(60.0, 150.0, 70.0)

        # Now advance 60+ seconds to same index but different second (second 160 -> index 40)
        with patch("time.time", return_value=160.0):
            buffer.push_gpu_data(65.0, 200.0, 80.0)

        with patch("time.time", return_value=160.0):
            snap = buffer.get_ordered_snapshot()

        target = [s for s in snap if s["timestamp"] == 160]
        assert len(target) == 1
        assert target[0]["gpu_temp_c"] == 65.0
        assert target[0]["input_tokens"] == 0  # reset on minute wrap


# ── Ollama request push tests ───────────────────────────────────────────


class TestPushOllamaRequest:
    """Token counts should be additively accumulated per second."""

    def test_push_single_request(self, buffer):
        buffer.push_ollama_request(
            completion_time_ms=1000.0 * 1000,
            input_tokens=50,
            output_tokens=200,
        )

        with patch("time.time", return_value=1000.0):
            snap = buffer.get_ordered_snapshot()

        target = [s for s in snap if s["timestamp"] == 1000]
        assert len(target) == 1
        assert target[0]["input_tokens"] == 50
        assert target[0]["output_tokens"] == 200
        assert target[0]["request_count"] == 1

    def test_additive_accumulation_same_second(self, buffer):
        """Two requests in the same second should add their token counts."""
        buffer.push_ollama_request(
            completion_time_ms=1000.0 * 1000,
            input_tokens=50,
            output_tokens=200,
        )
        buffer.push_ollama_request(
            completion_time_ms=1000.0 * 1000,
            input_tokens=30,
            output_tokens=100,
        )

        with patch("time.time", return_value=1000.0):
            snap = buffer.get_ordered_snapshot()

        target = [s for s in snap if s["timestamp"] == 1000]
        assert target[0]["input_tokens"] == 80
        assert target[0]["output_tokens"] == 300
        assert target[0]["request_count"] == 2

    def test_different_seconds_not_merged(self, buffer):
        """Requests in different seconds should land in separate slots."""
        buffer.push_ollama_request(
            completion_time_ms=1000.0 * 1000,
            input_tokens=50,
            output_tokens=200,
        )
        buffer.push_ollama_request(
            completion_time_ms=1001.0 * 1000,
            input_tokens=30,
            output_tokens=100,
        )

        with patch("time.time", return_value=1001.0):
            snap = buffer.get_ordered_snapshot()

        slot_1000 = [s for s in snap if s["timestamp"] == 1000]
        slot_1001 = [s for s in snap if s["timestamp"] == 1001]
        assert slot_1000[0]["input_tokens"] == 50
        assert slot_1001[0]["input_tokens"] == 30


# ── Ordered snapshot tests ──────────────────────────────────────────────


class TestOrderedSnapshot:
    """get_ordered_snapshot returns T-59 ... T time-ordered data."""

    def test_timestamps_are_time_ordered(self, buffer):
        with patch("time.time", return_value=1060.0):
            # Write to second 1000
            buffer.push_gpu_data(60.0, 150.0, 70.0)

        with patch("time.time", return_value=1060.0):
            snap = buffer.get_ordered_snapshot()

        first_ts = snap[0]["timestamp"]
        last_ts = snap[-1]["timestamp"]
        assert first_ts == 1060 - 59
        assert last_ts == 1060

    def test_missing_slots_forward_filled_with_last_gpu_readings(self, buffer):
        """Unpolled slots should carry forward the last known GPU readings."""
        with patch("time.time", return_value=1000.0):
            buffer.push_gpu_data(60.0, 150.0, 70.0)

        with patch("time.time", return_value=1000.0):
            snap = buffer.get_ordered_snapshot()

        # All slots should carry forward the GPU readings from second 1000
        for slot in snap:
            if slot["timestamp"] <= 1000:
                # Slots up to and including second 1000 have GPU data
                assert slot["gpu_temp_c"] == 60.0
                assert slot["gpu_power_w"] == 150.0
                assert slot["gpu_util_pct"] == 70.0
            else:
                # Future slots (T+1 .. T+59) have no GPU data yet
                assert slot["gpu_temp_c"] is None

            # Token data is only present in the polled slot
            if slot["timestamp"] != 1000:
                assert slot["input_tokens"] == 0
                assert slot["output_tokens"] == 0

    def test_snapshot_is_json_serializable(self, buffer):
        import json

        with patch("time.time", return_value=1000.0):
            snap = buffer.get_ordered_snapshot()

        # Should not raise
        json.dumps(snap)


# ── Minute wrap-around tests ────────────────────────────────────────────


class TestMinuteWrapAround:
    """Slot reuse after 60 seconds should trigger clean reset."""

    def test_wrap_around_resets_slot(self, buffer):
        """After 60 seconds the same index is reused and should be reset."""
        # Write at second 100 (index = 40)
        with patch("time.time", return_value=100.0):
            buffer.push_gpu_data(60.0, 150.0, 70.0)

        # Advance to second 160 (also index = 40) — should reset the slot
        with patch("time.time", return_value=160.0):
            buffer.push_gpu_data(70.0, 250.0, 90.0)

        with patch("time.time", return_value=160.0):
            snap = buffer.get_ordered_snapshot()

        # Old second 100 data should be gone, replaced by second 160
        slot_160 = [s for s in snap if s["timestamp"] == 160]
        assert len(slot_160) == 1
        assert slot_160[0]["gpu_temp_c"] == 70.0

        # Second 100 should not appear (outside T-59..T window for current_sec=160)
        old_slots = [s for s in snap if s["timestamp"] == 100]
        assert len(old_slots) == 0


# ── Thread safety ───────────────────────────────────────────────────────


class TestThreadSafety:
    """Concurrent pushes should not corrupt state."""

    def test_concurrent_gpu_pushes(self, buffer):
        errors: list[Exception] = []

        def pusher():
            for i in range(100):
                try:
                    with patch("time.time", return_value=float(1000 + i)):
                        buffer.push_gpu_data(60.0 + i * 0.1, 200.0, 80.0)
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=pusher) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(errors) == 0, f"Threads raised errors: {errors}"

    def test_concurrent_request_pushes(self, buffer):
        errors: list[Exception] = []

        def pusher():
            for i in range(50):
                try:
                    buffer.push_ollama_request(
                        completion_time_ms=float(1000 * 1000 + i * 1000),
                        input_tokens=10,
                        output_tokens=20,
                    )
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=pusher) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(errors) == 0, f"Threads raised errors: {errors}"


# ── MetricSlot dataclass tests ───────────────────────────────────────────


class TestMetricSlot:
    """MetricSlot defaults should be correct."""

    def test_default_values(self):
        slot = MetricSlot()
        assert slot.timestamp == 0
        assert slot.gpu_temp_c is None
        assert slot.gpu_power_w is None
        assert slot.gpu_util_pct is None
        assert slot.input_tokens == 0
        assert slot.output_tokens == 0
        assert slot.request_count == 0

    def test_custom_values(self):
        slot = MetricSlot(
            timestamp=1234,
            gpu_temp_c=65.0,
            gpu_power_w=200.0,
            gpu_util_pct=80.0,
            input_tokens=50,
            output_tokens=200,
            request_count=1,
        )
        assert slot.timestamp == 1234
        assert slot.gpu_temp_c == 65.0
        assert slot.gpu_power_w == 200.0
        assert slot.gpu_util_pct == 80.0
        assert slot.input_tokens == 50
        assert slot.output_tokens == 200
        assert slot.request_count == 1