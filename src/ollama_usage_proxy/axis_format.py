"""Axis formatting helpers for matplotlib graphs."""

from __future__ import annotations

import numpy as np
import matplotlib.dates as mdates
import matplotlib.ticker as ticker
from scipy.interpolate import PchipInterpolator


def human_number(x: float, pos: int | None = None) -> str:
    """Format number with K/M/B suffixes for axis tick labels."""
    if x < 0:
        prefix = "-"
        x = -x
    else:
        prefix = ""
    if x >= 1e9:
        return f"{prefix}{x / 1e9:.1f}B"
    elif x >= 1e6:
        return f"{prefix}{x / 1e6:.1f}M"
    elif x >= 1e3:
        return f"{prefix}{x / 1e3:.1f}K"
    else:
        return f"{prefix}{x:.0f}"


def format_yaxis(ax) -> None:
    """Apply human-readable number formatting (K/M/B) to the y-axis."""
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(human_number))


def format_xaxis(ax, group_by: str, data_index=None) -> None:
    """Apply custom date formatting to the x-axis based on group-by bucket."""
    fmt_map = {
        "hour": "%d-%m-%y %H:%M",
        "today": "%d-%m-%y %H:%M",
        "day": "%d-%m-%y",
        "week": "%d-%m-%y",
        "month": "%b-%y",
    }
    date_fmt = fmt_map.get(group_by, "%Y-%m-%d")

    if data_index is not None and len(data_index) > 0 and len(data_index) <= 15:
        tick_locs = mdates.date2num(data_index.to_pydatetime())
        ax.xaxis.set_major_locator(ticker.FixedLocator(tick_locs))

    ax.xaxis.set_major_formatter(mdates.DateFormatter(date_fmt))
    fig = ax.get_figure()
    fig.autofmt_xdate()


def smooth_line(x, y, num_segments: int = 50) -> tuple[np.ndarray, np.ndarray]:
    """Generate smoothed x/y arrays using Monotone Cubic Interpolation (PCHIP).

    Guarantees the interpolant is monotone wherever data is locally monotone—no
    overshoots or undershoots, keeping rendered graph trends accurate.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)

    if len(x) < 2:
        return x, y

    if len(x) == 2:
        t = np.linspace(0, 1, num_segments)
        return (1 - t) * x[0] + t * x[1], (1 - t) * y[0] + t * y[1]

    # Deduplicate x coordinates if needed
    x_unique, unique_indices = np.unique(x, return_index=True)
    y_unique = y[unique_indices]

    if len(x_unique) < 2:
        return x, y

    # Generate fine grid
    x_smooth = np.linspace(x_unique.min(), x_unique.max(), len(x_unique) * num_segments)
    
    # Monotone Piecewise Cubic Hermite Interpolating Polynomial (PCHIP)
    pchip = PchipInterpolator(x_unique, y_unique)
    y_smooth = pchip(x_smooth)

    return x_smooth, y_smooth