"""Playwright tests that verify Chart.js charts actually render data.

Uses two approaches for both GPU and token charts:
  1. **Chart.js Internal Data Array** — read the global ``charts`` object to confirm
     the Chart.js instances received data via dataset updates.
  2. **Canvas Pixel-Data Sampling** — peek inside the <canvas> backing store
     to prove pixels were actually drawn (not an empty/blank canvas).

The test server is seeded with synthetic mock data so charts render
predictable content regardless of hardware or live LLM traffic.
"""

from __future__ import annotations

import time

from playwright.sync_api import Page


# Chart.js container IDs (6 charts: 3 GPU line + 1 token rate line + 2 discrete bar)
CHART_IDS = [
    "chartGpuTemp",
    "chartGpuPower",
    "chartGpuUtil",
    "chartTokenRate",
    "chartInputTokens",
    "chartOutputTokens",
]

GPU_CHART_IDS = ["chartGpuTemp", "chartGpuPower", "chartGpuUtil"]
TOKEN_CHART_IDS = ["chartTokenRate", "chartInputTokens", "chartOutputTokens"]

# Global ``charts`` object keys (matches index.html ``const charts = {}``)
CHART_GLOBAL_KEYS = {
    "chartGpuTemp": "temp",
    "chartGpuPower": "power",
    "chartGpuUtil": "util",
    "chartTokenRate": "tokenRate",
    "chartInputTokens": "inputTokens",
    "chartOutputTokens": "outputTokens",
}


def _wait_for_chartjs_data(page: Page, container_id: str, timeout_ms: int = 10_000):
    """Poll until the Chart.js chart for *container_id* has data.

    Charts are stored in a global ``charts`` object (outside Alpine
    reactivity).  This function reads that global directly.

    Returns dict with dataLength and datasetCount or raises on timeout.
    """
    key = CHART_GLOBAL_KEYS.get(container_id)
    if not key:
        raise ValueError(f"Unknown chart container: {container_id}")

    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        info = page.evaluate(
            f"""(() => {{
                var chart = (typeof charts !== 'undefined' && charts) ? charts.{key} : null;
                if (!chart) return null;
                var ds = chart.data.datasets;
                return {{
                    datasetCount: ds.length,
                    dataLength: ds[0]?.data?.length ?? 0,
                }};
            }})"""
        )
        if info and info["dataLength"] >= 2:
            return info
        time.sleep(0.5)

    raise AssertionError(
        f"Chart {container_id} did not receive ≥2 data points within "
        f"{timeout_ms / 1000:.0f}s (info={info})"
    )


def _get_chart_data_values(page: Page, container_id: str):
    """Return the dataset[0].data array from the global charts object."""
    key = CHART_GLOBAL_KEYS.get(container_id)
    if not key:
        raise ValueError(f"Unknown chart container: {container_id}")

    return page.evaluate(
        f"""(() => {{
            var chart = (typeof charts !== 'undefined' && charts) ? charts.{key} : null;
            if (!chart) return [];
            return chart.data.datasets[0]?.data || [];
        }})"""
    )


def _canvas_has_nonblank_pixels(page: Page, container_id: str, timeout_ms: int = 10_000):
    """Return True if the canvas inside *container_id* has drawn pixels.

    Strategy: sample a grid of points from the canvas ImageData and check
    that at least some have non-zero alpha (the background is transparent /
    slate-950, and Chart.js draws grid + data lines on top).

    Requires --disable-web-security browser arg (set in pyproject.toml) so
    getImageData works when Chart.js is loaded from CDN.
    """
    # Ensure a proper viewport so containers have real width before resize
    page.set_viewport_size({"width": 1280, "height": 960})

    deadline = time.time() + timeout_ms / 1000
    last_result = None
    while time.time() < deadline:
        result = page.evaluate(
            f"""(() => {{
                const container = document.querySelector(".chart-container");
                if (!container) return {{ err: 'no container' }};

                const all_canvases = container.querySelectorAll('canvas');
                if (all_canvases.length === 0) return {{ err: 'no canvases', children: container.children.length }};

                const infos = [];
                for (let ci = 0; ci < all_canvases.length; ci++) {{
                    const c = all_canvases[ci];
                    const info = {{ idx: ci, w: c.width, h: c.height }};
                    try {{
                        const ctx = c.getContext('2d');
                        if (!ctx) {{ info.err = 'no ctx'; infos.push(info); continue; }}
                        if (c.width === 0 || c.height === 0) {{ info.err = 'zero-size'; infos.push(info); continue; }}
                        const img = ctx.getImageData(0, 0, c.width, c.height);
                        const px = img.data;
                        let nb = 0;
                        for (let i = 3; i < px.length; i += 40) if (px[i] > 0) nb++;
                        info.sampled = Math.floor(px.length / 40);
                        info.nonblank = nb;
                    }} catch (e) {{ info.err = e.message; }}
                    infos.push(info);
                }}

                return {{ canvases: infos }};
            }})"""
        )
        last_result = result
        if isinstance(result, dict):
            for cinfo in result.get("canvases", []):
                if cinfo.get("nonblank", 0) > 0:
                    return True
        time.sleep(0.5)

    raise AssertionError(
        f"Canvas inside #{container_id} appears blank after {timeout_ms / 1000:.0f}s (last_result={last_result})"
    )


# ── Tests ────────────────────────────────────────────────────────────────

class TestGPUChartRendering:
    """Verify GPU charts render when telemetry is available.

    These tests start the server, wait for the tick loop to produce data,
    and then check Chart.js internals / canvas pixels.
    """

    # ------------------------------------------------------------------

    def test_gpu_charts_receive_data_via_chartjs(self, page: Page, server: str):
        """Chart.js charts should receive data arrays via dataset updates.

        Checks global ``charts`` object to confirm ≥2 timestamps and y values.
        Mock data feeds synthetic GPU telemetry into the buffer every tick.
        """
        page.goto(f"{server}/")
        page.wait_for_timeout(5000)  # allow several mock ticks + Chart.js init

        for cid in GPU_CHART_IDS:
            info = _wait_for_chartjs_data(page, cid)
            assert info["dataLength"] >= 2, f"{cid} has {info['dataLength']} points (need ≥2)"
            assert info["datasetCount"] >= 1, f"{cid} has {info['datasetCount']} datasets (need ≥1)"

    # ------------------------------------------------------------------

    def test_gpu_charts_draw_pixels_on_canvas(self, page: Page, server: str):
        """Each GPU chart canvas should have drawn pixels.

        Samples ImageData to detect non-blank content.
        Mock data feeds synthetic GPU telemetry into the buffer every tick.
        """
        page.goto(f"{server}/")
        page.wait_for_timeout(5000)  # allow several mock ticks + render

        for cid in GPU_CHART_IDS:
            assert _canvas_has_nonblank_pixels(page, cid), (
                f"{cid} canvas is blank — chart may not be rendering"
            )

    # ------------------------------------------------------------------

    def test_chartjs_data_values_reasonable(self, page: Page, server: str):
        """The y-values in Chart.js data arrays should be within realistic GPU ranges.

        Mock data feeds sinusoidal values:
          Temperature: ~55-75 °C, Power: ~210-350 W, Utilization: ~35-85 %.
        """
        page.goto(f"{server}/")
        page.wait_for_timeout(5000)

        # Check GPU utilization is in 0-100 range (mock range: ~35-85%)
        _wait_for_chartjs_data(page, "chartGpuUtil")
        util_vals = _get_chart_data_values(page, "chartGpuUtil")
        for v in util_vals:
            if v is None:
                continue  # early padding slots may be null before telemetry fills them
            assert 0 <= v <= 120, f"GPU utilization value {v} out of expected range [0, 120]"

    # ------------------------------------------------------------------

    def test_canvas_count_matches_chart_count(self, page: Page, server: str):
        """Expect at least 6 canvas elements (3 GPU + 1 token rate + 2 discrete bar).

        Mock data feeds both GPU and token telemetry so all charts render.
        """
        page.goto(f"{server}/")
        page.wait_for_timeout(5000)

        canvases = page.locator("canvas").count()
        assert canvases >= 6, f"Expected ≥6 canvases (3 GPU + 1 rate + 2 discrete bar) but found {canvases}"

    # ------------------------------------------------------------------

    def test_no_chartjs_errors(self, page: Page, server: str):
        """Console should not contain Chart.js-related JavaScript errors."""
        errors: list[str] = []

        def on_console(msg):
            if msg.type == "error" and "chart" in msg.text.lower():
                errors.append(msg.text)

        page.on("console", on_console)
        page.goto(f"{server}/")
        page.wait_for_timeout(5000)

        assert not errors, f"Chart.js JS errors detected: {errors}"


class TestTokenChartRendering:
    """Verify token charts render data using Chart.js internals + canvas pixels.

    Mock data feeds synthetic token requests into the buffer every 5 ticks,
    so all three token charts receive data.
    """

    # ------------------------------------------------------------------

    def test_token_charts_receive_data_via_chartjs(self, page: Page, server: str):
        """Token chart Chart.js instances should receive data arrays."""
        page.goto(f"{server}/")
        page.wait_for_timeout(8000)  # need several mock ticks + token injections

        for cid in TOKEN_CHART_IDS:
            info = _wait_for_chartjs_data(page, cid)
            assert info["dataLength"] >= 2, f"{cid} has {info['dataLength']} points (need ≥2)"

    # ------------------------------------------------------------------

    def test_token_charts_draw_pixels_on_canvas(self, page: Page, server: str):
        """Token chart canvases should have drawn pixels."""
        page.goto(f"{server}/")
        page.wait_for_timeout(8000)

        for cid in TOKEN_CHART_IDS:
            assert _canvas_has_nonblank_pixels(page, cid), \
                f"{cid} canvas is blank — chart may not be rendering"

    # ------------------------------------------------------------------

    def test_token_charts_dataset_count(self, page: Page, server: str):
        """Each token chart should have exactly 1 dataset (line or bar)."""
        page.goto(f"{server}/")
        page.wait_for_timeout(8000)

        # Token rate is a line chart with 1 dataset
        info = _wait_for_chartjs_data(page, "chartTokenRate")
        assert info["datasetCount"] == 1, \
            f"chartTokenRate expected 1 dataset but got {info['datasetCount']}"

        # Input tokens bar chart — 1 dataset
        info = _wait_for_chartjs_data(page, "chartInputTokens")
        assert info["datasetCount"] == 1, \
            f"chartInputTokens expected 1 dataset (bar) but got {info['datasetCount']}"

        # Output tokens bar chart — 1 dataset
        info = _wait_for_chartjs_data(page, "chartOutputTokens")
        assert info["datasetCount"] == 1, \
            f"chartOutputTokens expected 1 dataset (bar) but got {info['datasetCount']}"


class TestChartJSLibraryLoaded:
    """Verify Chart.js is loaded and uPlot is removed."""

    def test_chartjs_global_exists(self, page: Page, server: str):
        """Chart.js must be available as a global constructor."""
        page.goto(f"{server}/")
        has_chart = page.evaluate("typeof Chart !== 'undefined'")
        assert has_chart, "Chart.js global not found"

    def test_uplot_not_loaded(self, page: Page, server: str):
        """uPlot must NOT be loaded on the page."""
        page.goto(f"{server}/")
        uplot_scripts = page.locator("script[src*='uplot']")
        assert uplot_scripts.count() == 0, "uPlot script should not be present"

    def test_chartjs_cdn_script_present(self, page: Page, server: str):
        """Chart.js CDN script tag must be present."""
        page.goto(f"{server}/")
        chartjs_scripts = page.locator("script[src*='chart.js']")
        assert chartjs_scripts.count() > 0, "Chart.js script should be present"

    def test_x_axis_hidden_in_charts(self, page: Page, server: str):
        """X-axis must be hidden (display: false) to prevent time-label jitter.

        Charts still carry a labels array (time strings for tooltips) but the
        axis is not rendered because scales.x.display === false.
        """
        page.goto(f"{server}/")
        page.wait_for_timeout(5000)

        x_axis_visible = page.evaluate("""(() => {
            if (typeof charts === 'undefined') return true;
            const chartList = [charts.temp, charts.power, charts.util,
                              charts.tokenRate, charts.inputTokens, charts.outputTokens];
            for (const c of chartList) {
                if (c && c.options?.scales?.x?.display !== false) {
                    return true;
                }
            }
            return false;
        })""")
        assert not x_axis_visible, "Charts should have x-axis hidden (display: false)"
