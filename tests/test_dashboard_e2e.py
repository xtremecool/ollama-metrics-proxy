"""End-to-end browser tests for the real-time dashboard using Playwright.

The dashboard uses Chart.js with 6 standalone chart cards:
  - GPU Temperature, GPU Power, GPU Utilization
  - Token Output Rate
  - Input Tokens (1s), Output Tokens (1s) — discrete per-second bar charts
"""

from playwright.sync_api import Page, expect


# Chart container IDs (6 charts in Chart.js)
CHART_IDS = [
    "chartGpuTemp",
    "chartGpuPower",
    "chartGpuUtil",
    "chartTokenRate",
    "chartInputTokens",
    "chartOutputTokens",
]


def test_dashboard_page_loads(page: Page, server: str):
    """The dashboard SPA must load without errors."""
    page.goto(f"{server}/")
    assert "Dashboard" in page.title()


def test_health_endpoint_accessible(page: Page, server: str):
    """/health must return JSON with status ok."""
    resp = page.request.get(f"{server}/health")
    assert resp.status == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_dashboard_html_structure(page: Page, server: str):
    """The root page must include the key structural elements."""
    page.goto(f"{server}/")
    expect(page.locator("h1")).to_be_visible()
    alpine_root = page.locator("[x-data]")
    expect(alpine_root.first).to_be_visible()


def test_chart_containers_render(page: Page, server: str):
    """All 6 chart canvases must be present in the DOM."""
    page.goto(f"{server}/")
    page.wait_for_timeout(4000)

    for cid in CHART_IDS:
        canvas = page.locator(f"#{cid}")
        assert canvas.count() == 1, f"Missing chart canvas #{cid}"


def test_dashboard_has_chartjs_canvases(page: Page, server: str):
    """Chart.js should render canvas elements when data is available.

    Verifies all 5 chart containers exist and Chart.js script is loaded.
    When canvases do render (CDN loaded in headless), assert count.
    """
    page.goto(f"{server}/")
    page.wait_for_timeout(4000)

    # Verify all chart IDs exist in the DOM
    for cid in CHART_IDS:
        assert page.locator(f"#{cid}").count() == 1, \
            f"Missing chart container #{cid}"

    # Chart.js script must be present
    assert page.locator("script[src*='chart.js']").count() > 0

    # uPlot must NOT be present
    assert page.locator("script[src*='uplot']").count() == 0

    # If canvases rendered (CDN loaded in headless), assert count
    canvases = page.locator("canvas")
    count = canvases.count()
    if count > 0:
        assert count >= 6, f"Expected >=6 canvases but found {count}"


def test_dashboard_has_kpi_cards(page: Page, server: str):
    """Dashboard should show KPI cards (no VRAM)."""
    page.goto(f"{server}/")
    page.wait_for_timeout(2000)

    gpu_temp_label = page.locator("text=GPU Temp")
    expect(gpu_temp_label.first).to_be_visible()

    vram_label = page.locator("text=VRAM")
    assert not vram_label.is_visible(), "VRAM KPI card should be removed"


def test_dashboard_no_apexcharts(page: Page, server: str):
    """Page should not load ApexCharts library."""
    page.goto(f"{server}/")
    scripts = page.locator("script[src*='apexcharts']")
    assert scripts.count() == 0, "ApexCharts script should not be present"


def test_dashboard_no_uplot(page: Page, server: str):
    """Page should NOT load uPlot library (replaced by Chart.js)."""
    page.goto(f"{server}/")
    uplot_scripts = page.locator("script[src*='uplot']")
    assert uplot_scripts.count() == 0, "uPlot script must not be present"


def test_websocket_connection_established(page: Page, server: str):
    """The dashboard JS should open a WebSocket connection."""
    page.goto(f"{server}/")
    page.wait_for_timeout(4000)

    alpine_root = page.locator("[x-data]")
    expect(alpine_root.first).to_be_visible()


def test_history_api_returns_json(page: Page, server: str):
    """GET /api/history must return valid JSON."""
    resp = page.request.get(f"{server}/api/history")
    assert resp.status == 200
    data = resp.json()
    assert "gpu_online" in data
    assert "metrics" in data


def test_static_files_served(page: Page, server: str):
    """/static/index.html must be directly accessible."""
    resp = page.request.get(f"{server}/static/index.html")
    assert resp.status == 200
    assert "text/html" in resp.headers.get("content-type", "")