"""Ollama Usage Proxy - Track local LLM token usage and compare with paid models."""

from importlib.metadata import PackageNotFoundError, version as _metadata_version

try:
    __version__ = _metadata_version("ollama-usage-proxy")
except PackageNotFoundError:
    __version__ = "0.3.4"
