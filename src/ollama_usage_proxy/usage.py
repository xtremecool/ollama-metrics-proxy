"""Usage tracking utilities for processing Ollama streaming responses."""

from __future__ import annotations

import json
import logging
from typing import Callable, Awaitable

import ollama_usage_proxy.models as _models

UsageMetrics = _models.UsageMetrics
extract_metrics_from_response = _models.extract_metrics_from_response

logger = logging.getLogger(__name__)


class StreamCollector:
    """Collects newline-delimited JSON chunks from an Ollama streaming response.

    This class is used during the proxy forward to track each chunk and
    retain the final payload where `done` is `true`, which contains the
    usage metrics we need.

    Attributes:
        final_payload: The last JSON object in the stream (where done=true).
        all_chunks: List of all raw chunk bytes received.
    """

    def __init__(self) -> None:
        self.final_payload: dict | None = None
        self.all_chunks: list[bytes] = []
        self._complete = False

    async def feed_line(self, line: bytes) -> None:
        """Process a single newline-delimited line from the stream.

        Args:
            line: A raw byte line (without trailing newline).
        """
        if self._complete:
            return

        # Store for passthrough
        self.all_chunks.append(line + b"\n")

        try:
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                return

            data = json.loads(text)

            if isinstance(data, dict):
                # Update final payload with every valid JSON object.
                # The last one should have done=true with usage metrics.
                self.final_payload = data

                if data.get("done") is True:
                    self._complete = True

        except json.JSONDecodeError:
            # Non-JSON chunk; just pass it through
            pass

    @property
    def is_complete(self) -> bool:
        """Return True if we have received the final done=true chunk."""
        return self._complete

    def get_metrics(
        self,
        *,
        method: str = "",
        path: str = "",
        status_code: int | None = None,
        streaming: bool = False,
    ) -> UsageMetrics | None:
        """Extract UsageMetrics from the final payload.

        Returns None if no valid final payload was collected.
        """
        if self.final_payload is None:
            return None

        return extract_metrics_from_response(
            self.final_payload,
            method=method,
            path=path,
            status_code=status_code,
            streaming=streaming,
        )


async def collect_stream_body(
    read_func: Callable[[], Awaitable[bytes]],
    collector: StreamCollector,
) -> bytes:
    """Read all remaining data from a stream and feed it to the collector.

    This is used after forwarding a streaming response to consume any
    leftover data and extract metrics.

    Args:
        read_func: An async callable that returns the next chunk of data.
                   Returns empty bytes when the stream is exhausted.
        collector: The StreamCollector to feed data into.

    Returns:
        The complete raw body as bytes.
    """
    while True:
        chunk = await read_func()
        if not chunk:
            break

        # Split by newlines and feed each line
        for line in chunk.split(b"\n"):
            if line:
                await collector.feed_line(line)

    return b"".join(collector.all_chunks)