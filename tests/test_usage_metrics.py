"""Tests for usage metric extraction."""

import pytest
from src.ollama_usage_proxy.models import UsageMetrics, extract_metrics_from_response


class TestExtractMetricsFromResponse:
    """Test metric extraction from Ollama response payloads."""

    def test_basic_chat_response(self):
        """Test extraction from a typical streaming chat final chunk."""
        payload = {
            "model": "llama3.1:8b",
            "prompt_eval_count": 500,
            "eval_count": 300,
            "total_duration": 5_000_000_000,
            "load_duration": 200_000_000,
            "prompt_eval_duration": 1_000_000_000,
            "eval_duration": 3_500_000_000,
            "done": True,
            "done_reason": "stop",
        }

        metrics = extract_metrics_from_response(
            payload,
            method="POST",
            path="/api/chat",
            status_code=200,
            streaming=True,
        )

        assert metrics.model == "llama3.1:8b"
        assert metrics.input_tokens == 500
        assert metrics.output_tokens == 300
        assert metrics.total_tokens == 800
        assert metrics.total_duration_ns == 5_000_000_000
        assert metrics.prompt_eval_duration_ns == 1_000_000_000
        assert metrics.eval_duration_ns == 3_500_000_000
        assert metrics.done_reason == "stop"

        # Check rates
        assert metrics.input_tokens_per_second == pytest.approx(500.0, rel=0.01)
        assert metrics.output_tokens_per_second == pytest.approx(85.71, rel=0.01)

    def test_non_streaming_response(self):
        """Test extraction from a non-streaming generate response."""
        payload = {
            "model": "codellama:7b",
            "prompt_eval_count": 200,
            "eval_count": 500,
            "total_duration": 8_000_000_000,
            "load_duration": 50_000_000,
            "prompt_eval_duration": 2_000_000_000,
            "eval_duration": 5_800_000_000,
        }

        metrics = extract_metrics_from_response(
            payload,
            method="POST",
            path="/api/generate",
            status_code=200,
            streaming=False,
        )

        assert metrics.input_tokens == 200
        assert metrics.output_tokens == 500
        assert metrics.total_tokens == 700
        assert not metrics.streaming

    def test_missing_duration_fields(self):
        """Test that missing duration fields default to zero rate."""
        payload = {
            "model": "tinyllama",
            "prompt_eval_count": 10,
            "eval_count": 20,
        }

        metrics = extract_metrics_from_response(payload)

        assert metrics.input_tokens == 10
        assert metrics.output_tokens == 20
        assert metrics.total_tokens == 30
        # Should not divide by zero
        assert metrics.input_tokens_per_second == 0.0
        assert metrics.output_tokens_per_second == 0.0

    def test_zero_duration_guard(self):
        """Test that zero durations produce zero rates instead of infinity."""
        payload = {
            "model": "test",
            "prompt_eval_count": 100,
            "eval_count": 50,
            "prompt_eval_duration": 0,
            "eval_duration": 0,
            "total_duration": 0,
        }

        metrics = extract_metrics_from_response(payload)

        assert metrics.input_tokens_per_second == 0.0
        assert metrics.output_tokens_per_second == 0.0
        assert metrics.total_tokens_per_second == 0.0

    def test_empty_response(self):
        """Test handling of an empty response body."""
        metrics = extract_metrics_from_response({})

        assert metrics.input_tokens == 0
        assert metrics.output_tokens == 0
        assert metrics.total_tokens == 0
        assert metrics.model is None


class TestUsageMetricsDerived:
    """Test the calculate_derived_metrics method."""

    def test_total_tokens(self):
        m = UsageMetrics(input_tokens=100, output_tokens=200)
        m.calculate_derived_metrics()
        assert m.total_tokens == 300

    def test_rates_with_valid_durations(self):
        m = UsageMetrics(
            input_tokens=1000,
            output_tokens=500,
            prompt_eval_duration_ns=2_000_000_000,
            eval_duration_ns=1_000_000_000,
            total_duration_ns=3_500_000_000,
        )
        m.calculate_derived_metrics()

        assert m.input_tokens_per_second == pytest.approx(500.0)
        assert m.output_tokens_per_second == pytest.approx(500.0)
        assert m.total_tokens_per_second == pytest.approx(428.57, rel=0.01)