"""Usage reporting script (legacy entry point).

Reads usage metrics from the SQLite database and generates graphs and summary reports.

This script delegates to ollama_usage_proxy.report_main which contains the actual
implementation. This file is kept for backward compatibility when running directly
from the project directory.

Usage:
    python3 scripts/report_usage.py \
        --db ~/.local/share/ollama-usage-proxy/usage.db \
        --prices prices.toml \
        --output-dir reports \
        [--from YYYY-MM-DD] \
        [--to YYYY-MM-DD] \
        [--group-by hour|day|week|month]
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the package importable when running as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ollama_usage_proxy.report_main import main  # noqa: E402


if __name__ == "__main__":
    main()