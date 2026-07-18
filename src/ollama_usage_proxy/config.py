"""Configuration loading for the Ollama usage proxy."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


# Directory where bundled defaults live
_DATA_DIR = Path(__file__).parent


class ProxyConfig:
    """Configuration for the proxy server."""

    def __init__(self, listen_host: str = "127.0.0.1", listen_port: int = 11435, ollama_base_url: str = "http://127.0.0.1:11434"):
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.ollama_base_url = ollama_base_url.rstrip("/")


class DatabaseConfig:
    """Configuration for the SQLite database."""

    def __init__(self, path: str = "~/.local/share/ollama-usage-proxy/usage.db"):
        self.path = path


class ReportingConfig:
    """Configuration for reporting."""

    def __init__(self, output_dir: str = "~/.local/share/ollama-usage-proxy/reports"):
        self.output_dir = output_dir


class AppConfig:
    """Top-level application configuration."""

    def __init__(
        self,
        proxy: ProxyConfig,
        database: DatabaseConfig,
        reporting: ReportingConfig,
    ):
        self.proxy = proxy
        self.database = database
        self.reporting = reporting

    @property
    def data_dir(self) -> Path:
        """Return the expanded parent directory for database/report paths."""
        return Path(self.database.path).expanduser().parent


def _load_toml(path: Path | None) -> dict[str, Any]:
    """Load a TOML file, returning an empty dict if not found."""
    if path is None or not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """Load configuration from a TOML file.

    Lookup order:
    1. Explicit config_path argument
    2. config.toml in the current working directory
    3. Bundled defaults shipped with the package
    """
    if config_path is None:
        config_path = "config.toml"

    path = Path(config_path)
    data: dict[str, Any] = {}

    # Try user-provided config first
    if path.exists():
        data = _load_toml(path)

    # Fall back to bundled defaults for any missing sections
    if not data:
        bundled = _DATA_DIR / "default_config.toml"
        bundled_data = _load_toml(bundled)
        data.update(bundled_data)

    # Parse proxy section
    proxy_data = data.get("proxy", {})
    proxy = ProxyConfig(
        listen_host=proxy_data.get("listen_host", "127.0.0.1"),
        listen_port=proxy_data.get("listen_port", 11435),
        ollama_base_url=proxy_data.get("ollama_base_url", "http://127.0.0.1:11434"),
    )

    # Parse database section
    db_data = data.get("database", {})
    database = DatabaseConfig(
        path=db_data.get("path", "~/.local/share/ollama-usage-proxy/usage.db"),
    )

    # Parse reporting section
    report_data = data.get("reporting", {})
    reporting = ReportingConfig(
        output_dir=report_data.get("output_dir", "~/.local/share/ollama-usage-proxy/reports"),
    )

    return AppConfig(proxy=proxy, database=database, reporting=reporting)


def get_default_prices_path() -> Path:
    """Return the path to the bundled default pricing file."""
    return _DATA_DIR / "default_prices.toml"