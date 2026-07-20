"""Token usage and token rates graph generation with dual y-axes."""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from ollama_usage_proxy.axis_format import format_xaxis, format_yaxis, smooth_line

logger = logging.getLogger("report_usage")


def generate_token_usage_graph(df, output_path, group_by="day"):
    """Generate a line chart of token usage over time with dual y-axes.

    Input tokens plotted on the left axis, output tokens on the right axis.
    Total tokens plotted on the left axis alongside input.
    Lines are smoothed with cubic spline interpolation; markers show actual data points.
    """
    out = Path(output_path)
    out.mkdir(parents=True, exist_ok=True)
    suffix = group_by if group_by != "day" else "daily"
    file_path = out / f"token_usage_{suffix}.png"

    fig, ax_left = plt.subplots(figsize=(12, 6))
    idx = df.index

    # Convert datetime index to numeric for spline interpolation
    x_num = mdates.date2num(idx.to_pydatetime())

    # Left axis: input tokens and total tokens (smoothed)
    x_s, y_s = smooth_line(x_num, df["input_tokens_sum"].values)
    ax_left.plot(mdates.num2date(x_s), y_s, label="Input tokens", color="blue")
    ax_left.scatter(idx, df["input_tokens_sum"], marker="o", s=20, color="blue", zorder=5)

    x_s2, y_s2 = smooth_line(x_num, df["total_tokens_sum"].values)
    ax_left.plot(mdates.num2date(x_s2), y_s2, label="Total tokens", color="purple")
    ax_left.scatter(idx, df["total_tokens_sum"], marker="^", s=20, color="purple", zorder=5)

    ax_left.set_ylabel("Input / Total Tokens", color="blue")
    ax_left.tick_params(axis='y', labelcolor="blue")
    format_yaxis(ax_left)

    # Right axis: output tokens (smoothed)
    ax_right = ax_left.twinx()
    x_s3, y_s3 = smooth_line(x_num, df["output_tokens_sum"].values)
    ax_right.plot(mdates.num2date(x_s3), y_s3, label="Output tokens", color="red")
    ax_right.scatter(idx, df["output_tokens_sum"], marker="s", s=20, color="red", zorder=5)
    ax_right.set_ylabel("Output Tokens", color="red")
    ax_right.tick_params(axis='y', labelcolor="red")
    format_yaxis(ax_right)

    ax_left.set_title(f"Token Usage Over Time ({group_by})")
    ax_left.set_xlabel(f"Time ({group_by})")

    # Combine legends from both axes
    left_legend = ax_left.get_legend_handles_labels()
    right_legend = ax_right.get_legend_handles_labels()
    ax_left.legend(left_legend[0] + right_legend[0], left_legend[1] + right_legend[1], loc="upper left")

    ax_left.grid(True, alpha=0.3)
    format_xaxis(ax_left, group_by, data_index=idx)
    fig.tight_layout()
    fig.savefig(file_path, dpi=150)
    plt.close(fig)

    logger.info("Token usage graph saved to %s", file_path)
    return file_path


def generate_token_rates_graph(df, output_path, group_by="day"):
    """Generate a line chart of weighted token rates over time with dual y-axes.

    Input rate on the left axis, output rate on the right axis.
    Lines are smoothed with cubic spline interpolation; markers show actual data points.
    """
    out = Path(output_path)
    out.mkdir(parents=True, exist_ok=True)
    suffix = group_by if group_by != "day" else "daily"
    file_path = out / f"token_rates_{suffix}.png"

    fig, ax_left = plt.subplots(figsize=(12, 6))
    idx = df.index

    # Convert datetime index to numeric for spline interpolation
    x_num = mdates.date2num(idx.to_pydatetime())

    # Left axis: input tokens/sec (smoothed) — rounded to nearest integer for straighter lines
    input_tps_rounded = df["weighted_input_tps"].round(0)
    x_s, y_s = smooth_line(x_num, input_tps_rounded.values)
    ax_left.plot(mdates.num2date(x_s), y_s, label="Weighted input tokens/sec", color="blue")
    ax_left.scatter(idx, input_tps_rounded, marker="o", s=20, color="blue", zorder=5)
    ax_left.set_ylabel("Input Tokens/sec", color="blue")
    ax_left.tick_params(axis='y', labelcolor="blue")
    format_yaxis(ax_left)

    # Right axis: output tokens/sec (smoothed) — rounded to nearest integer for straighter lines
    ax_right = ax_left.twinx()
    output_tps_rounded = df["weighted_output_tps"].round(0)
    x_s2, y_s2 = smooth_line(x_num, output_tps_rounded.values)
    ax_right.plot(mdates.num2date(x_s2), y_s2, label="Weighted output tokens/sec", color="red")
    ax_right.scatter(idx, output_tps_rounded, marker="s", s=20, color="red", zorder=5)
    ax_right.set_ylabel("Output Tokens/sec", color="red")
    ax_right.tick_params(axis='y', labelcolor="red")
    format_yaxis(ax_right)

    ax_left.set_title(f"Token Processing Rates Over Time ({group_by}, Weighted)")
    ax_left.set_xlabel(f"Time ({group_by})")

    # Combine legends from both axes
    left_legend = ax_left.get_legend_handles_labels()
    right_legend = ax_right.get_legend_handles_labels()
    ax_left.legend(left_legend[0] + right_legend[0], left_legend[1] + right_legend[1], loc="upper left")

    ax_left.grid(True, alpha=0.3)
    format_xaxis(ax_left, group_by, data_index=idx)
    fig.tight_layout()
    fig.savefig(file_path, dpi=150)
    plt.close(fig)

    logger.info("Token rates graph saved to %s", file_path)
    return file_path