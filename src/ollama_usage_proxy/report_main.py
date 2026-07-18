"""Entry point for the usage report generator.

This module re-implements report_usage.py as a proper package entry point
so PyInstaller can bundle it into a standalone executable.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import ollama_usage_proxy.pricing as pricing

from ollama_usage_proxy.report_data import fetch_requests, resample_dataframe
from ollama_usage_proxy.token_graphs import generate_token_usage_graph, generate_token_rates_graph
from ollama_usage_proxy.cost_graphs import generate_cost_graphs
from ollama_usage_proxy.summary import generate_summary_csv, generate_summary_markdown

load_prices = pricing.load_prices

logger = logging.getLogger("report_usage")


# Default paths using XDG-style data directory
_DEFAULT_DB = str(Path.home() / ".local/share/ollama-usage-proxy/usage.db")
_DEFAULT_PRICES = "prices.toml"
_BUNDLED_PRICES = str(Path(__file__).with_name("default_prices.toml").resolve())
_DEFAULT_OUTPUT = str(Path.home() / ".local/share/ollama-usage-proxy/reports")


def _default_from_date(group_by: str) -> str | None:
    """Return a sensible default --from date based on the group-by bucket.

    When the user does not specify --from/--to, restrict the report to the
    current period for the chosen bucket so graphs are never empty-looking.
    """
    now = datetime.now(timezone.utc)

    if group_by == "today":
        # Midnight today until now
        return now.strftime("%Y-%m-%d")
    elif group_by == "hour":
        # Last 24 hours worth of hourly buckets
        start = now - pd.Timedelta(hours=24)
        return start.strftime("%Y-%m-%d")
    elif group_by == "day":
        # Last 7 days (gives a nice weekly view with daily buckets)
        start = now - pd.Timedelta(days=7)
        return start.strftime("%Y-%m-%d")
    elif group_by == "week":
        # Last 12 weeks
        start = now - pd.Timedelta(weeks=12)
        return start.strftime("%Y-%m-%d")
    elif group_by == "month":
        # Last 12 months
        start = now.replace(day=1) - pd.DateOffset(months=12)
        return start.strftime("%Y-%m-%d")
    return None


def main() -> None:
    """Entry point for the report generator."""
    parser = argparse.ArgumentParser(description="Generate usage reports from Ollama proxy database.")
    parser.add_argument("--db", type=str, default=_DEFAULT_DB, help=f"Path to SQLite database file (default: {_DEFAULT_DB}).")
    parser.add_argument("--prices", type=str, default=_DEFAULT_PRICES, help=f"Path to pricing TOML file (default: {_DEFAULT_PRICES}, falls back to bundled default if not found).")
    parser.add_argument("--output-dir", type=str, default=_DEFAULT_OUTPUT, help=f"Directory for generated reports (default: {_DEFAULT_OUTPUT}).")
    parser.add_argument("--from", dest="from_date", type=str, default=None, help="Start date filter (YYYY-MM-DD).")
    parser.add_argument("--to", dest="to_date", type=str, default=None, help="End date filter (YYYY-MM-DD).")
    parser.add_argument("--group-by", choices=["hour", "today", "day", "week", "month"], default="day",
                        help="Time bucket for aggregation. 'today' = midnight-to-now with hourly buckets (default: day).")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    # Apply sensible default date range when user did not specify --from
    effective_from = args.from_date
    if effective_from is None:
        effective_from = _default_from_date(args.group_by)
        if effective_from:
            logger.info("No --from specified; defaulting to %s for '%s' buckets", effective_from, args.group_by)

    logger.info("Reading requests from %s", args.db)
    df = fetch_requests(args.db, from_date=effective_from, to_date=args.to_date)

    if df.empty:
        logger.warning("No data to report. Exiting.")
        sys.exit(0)

    # Resolve prices file: use specified path, fall back to bundled default if not found
    prices_path = Path(args.prices)
    if not prices_path.exists():
        if prices_path.name == _DEFAULT_PRICES and Path(_BUNDLED_PRICES).exists():
            logger.info("Prices file '%s' not found; using bundled default: %s", args.prices, _BUNDLED_PRICES)
            prices_path = Path(_BUNDLED_PRICES)
        else:
            raise FileNotFoundError(f"Pricing file not found: {prices_path}")

    logger.info("Loading pricing from %s", prices_path)
    prices = load_prices(prices_path)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    resampled = resample_dataframe(df, group_by=args.group_by)

    if not resampled.empty:
        generate_token_usage_graph(resampled, output_dir, args.group_by)
        generate_token_rates_graph(resampled, output_dir, args.group_by)
        generate_cost_graphs(resampled, prices, output_dir, args.group_by)

    generate_summary_csv(df, prices, output_dir)
    generate_summary_markdown(df, prices, output_dir, from_date=effective_from, to_date=args.to_date)

    logger.info("Report generation complete. Output in %s", output_dir)


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()
