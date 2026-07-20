"""Paid-model cost graph generation."""

from __future__ import annotations

import itertools
import logging
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.colors as mcolors
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from ollama_usage_proxy.axis_format import format_xaxis, format_yaxis, smooth_line

logger = logging.getLogger("report_usage")


def _get_distinct_colors(n):
    """Return a list of n visually distinct colors.

    Uses tab20 colormap (20 distinct colors) for up to 20 items.
    For more than 20, generates colors from HSV colormap spread evenly.
    """
    if n <= 20:
        cmap = plt.get_cmap("tab20")
        return [cmap(i % 20) for i in range(n)]
    else:
        # Generate evenly spaced colors from HSV color wheel
        return [matplotlib.colors.hsv_to_hsv(h, 0.85, 0.9) for h in itertools.islice(
            (i / n for i in range(n)), None)]


def generate_cost_graphs(df, prices, output_path, group_by="day"):
    """Generate paid-model equivalent cost graphs (daily and cumulative).

    Lines are smoothed with cubic spline interpolation; markers show actual data points.
    Each model is assigned a distinct color from an extended palette to avoid reuse.
    """
    out = Path(output_path)
    out.mkdir(parents=True, exist_ok=True)
    files = []

    cost_data = {}
    for price in prices:
        input_cost = (df["input_tokens_sum"] / 1_000_000) * price.input_per_million
        output_cost = (df["output_tokens_sum"] / 1_000_000) * price.output_per_million
        total_cost = input_cost + output_cost
        cost_data[price.name] = {"input": input_cost, "output": output_cost, "total": total_cost}

    # Assign distinct colors — one per model, consistent across both graphs
    model_names = list(cost_data.keys())
    num_models = len(model_names)
    colors = _get_distinct_colors(num_models)
    color_map = dict(zip(model_names, colors))

    # Convert datetime index to numeric for spline interpolation
    x_num = mdates.date2num(df.index.to_pydatetime())

    # Period cost graph
    suffix = group_by if group_by != "day" else "daily"
    period_path = out / f"paid_model_cost_{suffix}.png"
    fig, ax = plt.subplots(figsize=(12, 6))
    for model_name, costs in cost_data.items():
        short_name = model_name[:30] + "..." if len(model_name) > 30 else model_name
        color = color_map[model_name]
        x_s, y_s = smooth_line(x_num, costs["total"].values)
        ax.plot(mdates.num2date(x_s), y_s, label=short_name, color=color)
        ax.scatter(df.index, costs["total"], s=20, zorder=5, color=color)
    ax.set_title(f"Paid-Model Equivalent Cost ({group_by})")
    ax.set_xlabel(f"Time ({group_by})")
    ax.set_ylabel("Cost (USD)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    format_yaxis(ax)
    format_xaxis(ax, group_by, data_index=df.index)
    fig.tight_layout()
    fig.savefig(period_path, dpi=150)
    plt.close(fig)
    logger.info("%s cost graph saved to %s", group_by.capitalize(), period_path)
    files.append(period_path)

    # Cumulative cost graph
    cum_path = out / f"paid_model_cost_cumulative_{suffix}.png"
    fig, ax = plt.subplots(figsize=(12, 6))
    for model_name, costs in cost_data.items():
        short_name = model_name[:30] + "..." if len(model_name) > 30 else model_name
        color = color_map[model_name]
        cumulative = costs["total"].cumsum()
        x_s, y_s = smooth_line(x_num, cumulative.values)
        ax.plot(mdates.num2date(x_s), y_s, label=short_name, color=color)
        ax.scatter(df.index, cumulative, s=20, zorder=5, color=color)
    ax.set_title(f"Paid-Model Equivalent Cost ({group_by}, Cumulative)")
    ax.set_xlabel(f"Time ({group_by})")
    ax.set_ylabel("Cumulative Cost (USD)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    format_yaxis(ax)
    format_xaxis(ax, group_by, data_index=df.index)
    fig.tight_layout()
    fig.savefig(cum_path, dpi=150)
    plt.close(fig)
    logger.info("Cumulative cost graph saved to %s", cum_path)
    files.append(cum_path)

    return files