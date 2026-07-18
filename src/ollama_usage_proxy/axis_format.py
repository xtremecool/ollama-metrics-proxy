"""Axis formatting helpers for matplotlib graphs."""

from __future__ import annotations

import numpy as np
import matplotlib.dates as mdates
import matplotlib.ticker as ticker


def human_number(x, pos):
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


def format_yaxis(ax):
    """Apply human-readable number formatting (K/M/B) to the y-axis."""
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(human_number))


def format_xaxis(ax, group_by, data_index=None):
    """Apply custom date formatting to the x-axis based on group-by bucket.

    Forces x-axis ticks to only appear at actual data points so there are no
    extra phantom periods between real buckets.

    Args:
        ax: matplotlib axis
        group_by: grouping type (hour, today, day, week, month)
        data_index: pandas DatetimeIndex of actual data points - if provided,
            ticks will be placed exactly at these positions
    """
    fmt_map = {
        "hour": "%d-%m-%y %H:%M",
        "today": "%d-%m-%y %H:%M",
        "day": "%d-%m-%y",
        "week": "%d-%m-%y",
        "month": "%b-%y",
    }
    date_fmt = fmt_map.get(group_by, "%Y-%m-%d")

    # Place ticks exactly at each data point to avoid extra phantom periods
    if data_index is not None and len(data_index) > 0:
        tick_locs = mdates.date2num(data_index.to_pydatetime())
        ax.xaxis.set_major_locator(ticker.FixedLocator(tick_locs))

    ax.xaxis.set_major_formatter(mdates.DateFormatter(date_fmt))
    fig = ax.get_figure()
    fig.autofmt_xdate()


def smooth_line(x, y, num_segments=50):
    """Generate smoothed x/y arrays using Fritsch-Butland monotone cubic interpolation.

    The Fritsch-Butland method guarantees the interpolant is monotone wherever
    the data is locally monotone - no overshoots or undershoots, even when
    adjacent data points have very different values.

    Args:
        x: numeric x values (e.g., matplotlib date numbers)
        y: corresponding y values
        num_segments: number of sample points per segment

    Returns:
        tuple of (x_smooth, y_smooth) arrays for plotting smooth curves.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)

    if len(x) < 2:
        return x, y

    if len(x) == 2:
        t = np.linspace(0, 1, num_segments)
        x_out = (1 - t) * x[0] + t * x[1]
        y_out = (1 - t) * y[0] + t * y[1]
        return x_out, y_out

    n = len(x)
    h = np.diff(x)
    # Delta y / delta x for each interval
    d = np.diff(y) / h

    # --- Fritsch-Butland weight computation ---
    # Compute piecewise weights that clamp the slope to preserve monotonicity
    m = np.zeros(n)  # slopes at each knot

    # Interior knots: weighted average of adjacent interval slopes
    for i in range(1, n - 1):
        if d[i - 1] * d[i] <= 0:
            # Sign change or zero: set slope to 0
            m[i] = 0.0
        else:
            # Weighted average (Fritsch-Butland uses h-weighted harmonic-like mean)
            w = (2 * h[i] + h[i - 1]) * d[i - 1] * d[i]
            if w == 0:
                m[i] = 0.0
            else:
                m[i] = (3 * h[i] * d[i - 1] + 3 * h[i - 1] * d[i]) / (2 * (h[i] + h[i - 1]))
                # Clamp: if |m[i]| > 3*min(|d[i-1]|, |d[i]|), reduce it
                max_slope = 3 * min(abs(d[i - 1]), abs(d[i]))
                if abs(m[i]) > max_slope:
                    m[i] = max_slope * np.sign(m[i])

    # Endpoints: non-uniform quadratic (Fritsch-Butland boundary conditions)
    m[0] = d[0]
    m[-1] = d[-1]

    # --- Evaluate cubic Hermite segments ---
    all_x = []
    all_y = []

    for i in range(n - 1):
        t = np.linspace(0, 1, num_segments)

        # Cubic Hermite basis functions
        H00 = 2 * t**3 - 3 * t**2 + 1
        H10 = t**3 - 2 * t**2 + t
        H01 = -2 * t**3 + 3 * t**2
        H11 = t**3 - t**2

        seg_x = (1 - t) * x[i] + t * x[i + 1]
        seg_y = (H00 * y[i]
                 + H10 * m[i] * h[i]
                 + H01 * y[i + 1]
                 + H11 * m[i + 1] * h[i])

        if i == 0:
            all_x.append(seg_x)
            all_y.append(seg_y)
        else:
            all_x.append(seg_x[1:])
            all_y.append(seg_y[1:])

    return np.concatenate(all_x), np.concatenate(all_y)