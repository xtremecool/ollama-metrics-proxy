"""Summary report generation (CSV and Markdown)."""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("report_usage")


def generate_summary_csv(df, prices, output_path):
    """Generate a CSV summary file with aggregated metrics and cost estimates."""
    out = Path(output_path)
    out.mkdir(parents=True, exist_ok=True)
    file_path = out / "summary.csv"

    total_input = int(df["input_tokens"].sum())
    total_output = int(df["output_tokens"].sum())
    total_all = int(df["total_tokens"].sum())

    rows = [
        {"metric": "total_requests", "value": len(df)},
        {"metric": "total_input_tokens", "value": total_input},
        {"metric": "total_output_tokens", "value": total_output},
        {"metric": "total_tokens", "value": total_all},
    ]

    total_prompt_sec = (df["prompt_eval_duration_ns"].sum() or 0) / 1e9
    total_eval_sec = (df["eval_duration_ns"].sum() or 0) / 1e9

    weighted_input_tps = total_input / total_prompt_sec if total_prompt_sec else 0
    weighted_output_tps = total_output / total_eval_sec if total_eval_sec else 0

    rows.append({"metric": "weighted_input_tokens_per_sec", "value": round(weighted_input_tps, 2)})
    rows.append({"metric": "weighted_output_tokens_per_sec", "value": round(weighted_output_tps, 2)})

    for price in prices:
        result = price.calculate_cost(total_input, total_output)
        rows.append({"metric": f"cost_{price.name}_input", "value": round(result.input_cost, 4), "currency": result.currency})
        rows.append({"metric": f"cost_{price.name}_output", "value": round(result.output_cost, 4), "currency": result.currency})
        rows.append({"metric": f"cost_{price.name}_total", "value": round(result.total_cost, 4), "currency": result.currency})

    with open(file_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["metric", "value", "currency"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    logger.info("Summary CSV saved to %s", file_path)
    return file_path


def generate_summary_markdown(df, prices, output_path, from_date=None, to_date=None):
    """Generate a human-readable Markdown summary report."""
    out = Path(output_path)
    out.mkdir(parents=True, exist_ok=True)
    file_path = out / "summary.md"

    total_input = int(df["input_tokens"].sum())
    total_output = int(df["output_tokens"].sum())
    total_all = int(df["total_tokens"].sum())

    total_prompt_sec = (df["prompt_eval_duration_ns"].sum() or 0) / 1e9
    total_eval_sec = (df["eval_duration_ns"].sum() or 0) / 1e9

    weighted_input_tps = total_input / total_prompt_sec if total_prompt_sec else 0
    weighted_output_tps = total_output / total_eval_sec if total_eval_sec else 0

    lines = [
        "# Ollama Usage Summary", "", "## Period", "",
        f"- From: {from_date or 'beginning'}", f"- To: {to_date or 'present'}",
        "", "## Requests", "", f"- Total requests: {len(df)}",
        "", "## Token Usage", "",
        f"- Input tokens: {total_input:,}", f"- Output tokens: {total_output:,}",
        f"- Total tokens: {total_all:,}",
        "", "## Token Rates (Weighted)", "",
        f"- Weighted input tokens/sec: {weighted_input_tps:.2f}",
        f"- Weighted output tokens/sec: {weighted_output_tps:.2f}",
        "", "## Paid-Model Equivalent Cost", "",
        "| Model | Input cost | Output cost | Total cost |",
        "|---|---:|---:|---:|",
    ]

    for price in prices:
        result = price.calculate_cost(total_input, total_output)
        symbol = "$" if result.currency == "USD" else result.currency + " "
        lines.append(f"| {price.name} | {symbol}{result.input_cost:.2f} | {symbol}{result.output_cost:.2f} | {symbol}{result.total_cost:.2f} |")

    lines.append("")
    lines.append(f"\n*Report generated at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}*")
    lines.append("")

    with open(file_path, "w") as f:
        f.write("\n".join(lines))

    logger.info("Summary Markdown saved to %s", file_path)
    return file_path