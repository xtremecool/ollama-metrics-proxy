"""Data models for usage metrics."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class UsageMetrics:
    """Captures all usage and timing metrics from an Ollama request.

    This is extracted from the final Ollama response payload and stored in SQLite.
    Only metadata is stored; no prompt or response content is captured.
    """

    # Request metadata
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    method: str = ""
    path: str = ""
    model: str | None = None
    status_code: int | None = None
    streaming: bool = False

    # Token counts
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    # Durations (nanoseconds from Ollama)
    total_duration_ns: int | None = None
    load_duration_ns: int | None = None
    prompt_eval_duration_ns: int | None = None
    eval_duration_ns: int | None = None

    # Derived rates (tokens/sec)
    input_tokens_per_second: float | None = None
    output_tokens_per_second: float | None = None
    total_tokens_per_second: float | None = None

    # Error information
    done_reason: str | None = None
    error: str | None = None

    def calculate_derived_metrics(self) -> None:
        """Calculate derived metrics from raw Ollama fields.

        Sets total_tokens, token rates, and handles divide-by-zero gracefully.
        """
        # Total tokens
        self.total_tokens = self.input_tokens + self.output_tokens

        # Input token rate
        if self.prompt_eval_duration_ns and self.prompt_eval_duration_ns > 0:
            prompt_seconds = self.prompt_eval_duration_ns / 1_000_000_000
            self.input_tokens_per_second = self.input_tokens / prompt_seconds
        else:
            self.input_tokens_per_second = 0.0

        # Output token rate
        if self.eval_duration_ns and self.eval_duration_ns > 0:
            gen_seconds = self.eval_duration_ns / 1_000_000_000
            self.output_tokens_per_second = self.output_tokens / gen_seconds
        else:
            self.output_tokens_per_second = 0.0

        # Total token rate
        if self.total_duration_ns and self.total_duration_ns > 0:
            total_seconds = self.total_duration_ns / 1_000_000_000
            self.total_tokens_per_second = self.total_tokens / total_seconds
        else:
            self.total_tokens_per_second = 0.0


def extract_metrics_from_response(
    payload: dict,
    *,
    method: str = "",
    path: str = "",
    status_code: int | None = None,
    streaming: bool = False,
) -> UsageMetrics:
    """Extract usage metrics from an Ollama response JSON payload.

    Ollama returns usage fields in the final chunk of a streaming response or
    in the body of a non-streaming response.

    Args:
        payload: The parsed JSON dict from Ollama.
        method: HTTP method of the original request.
        path: URL path of the original request.
        status_code: HTTP status code from Ollama.
        streaming: Whether this was a streaming request.

    Returns:
        A UsageMetrics instance populated with extracted values.
    """
    metrics = UsageMetrics(
        method=method,
        path=path,
        status_code=status_code,
        streaming=streaming,
    )

    # Model name
    if "model" in payload:
        metrics.model = payload["model"]

    # Token counts from Ollama usage fields.
    # Ollama exposes prompt_eval_count (input) and eval_count (output).
    metrics.input_tokens = int(payload.get("prompt_eval_count") or 0)
    metrics.output_tokens = int(payload.get("eval_count") or 0)

    # Durations (nanoseconds)
    metrics.total_duration_ns = payload.get("total_duration")
    metrics.load_duration_ns = payload.get("load_duration")
    metrics.prompt_eval_duration_ns = payload.get("prompt_eval_duration")
    metrics.eval_duration_ns = payload.get("eval_duration")

    # Done reason
    if done_reason := payload.get("done_reason"):
        metrics.done_reason = done_reason

    # Calculate derived fields
    metrics.calculate_derived_metrics()

    return metrics