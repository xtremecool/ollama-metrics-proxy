# Developer Guide

> Internal reference for anyone working on the **ollama-usage-proxy** codebase.
> Covers tooling, naming conventions, required patterns, architecture overview,
> and common development workflows.

---

## Table of Contents

1. [Build Tools & Dependencies](#1-build-tools--dependencies)
2. [Project Structure](#2-project-structure)
3. [Naming Conventions](#3-naming-conventions)
4. [Required Code Patterns](#4-required-code-patterns)
5. [Async / Concurrency Model](#5-async--concurrency-model)
6. [Testing](#6-testing)
7. [Linting & Formatting](#7-linting--formatting)
8. [PyInstaller Builds](#8-pyinstaller-builds)
9. [Configuration System](#9-configuration-system)
10. [Database Schema](#10-database-schema)
11. [Frontend (Dashboard)](#11-frontend-dashboard)
12. [Git Workflow](#12-git-workflow)

---

## 1. Build Tools & Dependencies

### Required Tools

| Tool | Minimum Version | Purpose |
|---|---|---|
| Python | 3.10+ | Runtime — project uses modern typing (`X \| Y` union syntax, `type[X]`) |
| uv | any | Recommended package manager for fast installs and `uv run` one-shot launches |
| pip | 23.0+ | Fallback package manager |
| pytest | 8.0+ | Test runner |
| ruff | 0.4.0+ | Linter and formatter |
| Git | 2.0+ | Version control |

### Installing for Development

```bash
git clone git@github.com:xtremecool/ollama-metrics-proxy.git
cd ollama-metrics-proxy

python3 -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"
```

The `[dev]` extra installs `pytest`, `ruff`, and `rich`.

### One-Shot Launch (No Install)

```bash
uv run --from ollama-usage-proxy ollama-proxy
```

### CLI Entry Points

| Command | Entrypoint | Description |
|---|---|---|
| `ollama-proxy` | `ollama_usage_proxy.app:main` | Starts the proxy server |
| `ollama-report` | `ollama_usage_proxy.report_main:main` | Generates usage reports and graphs |

---

## 2. Project Structure

```
src/ollama_usage_proxy/
├── app.py              # FastAPI application, proxy handler, WebSocket, lifespan
├── config.py           # TOML configuration loading (AppConfig dataclasses)
├── db.py               # SQLite schema, insert/query helpers (sync + async)
├── models.py           # UsageMetrics dataclass, extract_metrics_from_response()
├── usage.py            # StreamCollector — parses ND-JSON Ollama streams
├── metrics_buffer.py   # TimeSeriesRingBuffer (60-slot circular buffer)
├── system_telemetry.py # NVIDIA GPU monitoring via nvidia-ml-py
├── pricing.py          # Paid-model price lookup from TOML
├── summary.py          # Text/Markdown summary generation
├── report_data.py      # Report data aggregation (pandas)
├── report_main.py      # ollama-report CLI entry point
├── token_graphs.py     # matplotlib token usage charts
├── cost_graphs.py      # matplotlib cost comparison charts
├── axis_format.py      # Shared axis formatting helpers for matplotlib
├── default_config.toml # Bundled default configuration
├── default_prices.toml # Bundled default paid-model pricing
└── static/
    └── index.html      # Self-contained dashboard (Alpine.js + Chart.js)
```

### Key Design Principle

**No prompt or response content is ever stored.** The proxy captures only metadata: timestamps, token counts, durations, rates, model names, and error messages.

---

## 3. Naming Conventions

### Modules

- **Snake case**, descriptive, singular nouns or verb-noun pairs:
  - `metrics_buffer.py`, `system_telemetry.py`, `report_data.py`

### Classes

- **PascalCase**, noun phrases describing the entity:
  - `TimeSeriesRingBuffer`, `RequestLifecycle`, `SessionCounters`, `StreamCollector`, `UsageMetrics`
- Data transfer objects use the `dataclass` decorator with type-annotated fields.

### Functions

- **Snake case**, verb-first describing the action:
  - `push_gpu_data()`, `get_ordered_snapshot()`, `extract_metrics_from_response()`
- Async functions that start background loops use the `start_*_loop` pattern:
  - `start_telemetry_loop()`, `_metrics_broadcast_loop()`

### Private Functions

- Prefixed with single underscore `_`:
  - `_broadcast_live_metrics()`, `_send_to_all_clients()`, `_record_request_complete()`
- Private functions are module-level helpers not intended for external import.

### Variables

- **Snake case**, lowercase:
  - `metrics_buffer`, `session_counters`, `request_lifecycle`
- Module-level singletons (shared state) use plain names without prefix:
  - `metrics_buffer = TimeSeriesRingBuffer()`
- Internal mutable state within classes uses underscore-prefixed attributes:
  - `self._lock`, `self._buffer`, `self._in_flight_count`

### Private Import Aliases

- Internal imports in `app.py` use underscore-prefixed aliases to avoid name collisions with local definitions:
  ```python
  import ollama_usage_proxy.db as _db
  import ollama_usage_proxy.usage as _usage
  ```

---

## 4. Required Code Patterns

### 4.1 Always Use `from __future__ import annotations`

Every module starts with:
```python
from __future__ import annotations
```
This enables PEP 563 postponed evaluation of annotations, which is required for the `X | Y` union syntax in Python 3.10.

### 4.2 Modern Type Hints Only

**DO use:**
```python
def foo(x: int | None) -> str | None: ...
list[str], dict[str, Any]
```

**DO NOT use:**
```python
from typing import Optional, List, Dict  # legacy — not used in this project
def foo(x: Optional[int]) -> Optional[str]: ...
```

The project targets Python 3.10+ which supports PEP 604 union syntax (`X | Y`) and built-in generic types (`list[X]`).

### 4.3 Dataclasses for Data Containers

Use `@dataclass` from the `dataclass` module for all data-holding structures:
```python
from dataclasses import dataclass, field

@dataclass
class MyData:
    name: str = ""
    count: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
```

### 4.4 `__slots__` for State Tracker Classes

Classes that manage shared mutable state (counters, lifecycle trackers) use `__slots__` to prevent accidental attribute creation and reduce memory overhead:
```python
class RequestLifecycle:
    __slots__ = ("_in_flight_count", "_is_generating", "_lock")
```

### 4.5 Explicit Return Type Annotations

Every function and method must have a return type annotation:
```python
async def health() -> dict: ...
def calculate(x: int) -> float | None: ...
async def _internal_helper() -> None: ...
```

### 4.6 Docstrings on All Public Functions

Public functions and methods include a concise docstring describing what the function does, its parameters (if non-obvious), and return value:
```python
def push_gpu_data(self, temp_c: float, power_w: float, util_pct: float) -> None:
    """Write GPU telemetry to the current-second slot.

    Called by the 1Hz GPU polling loop.
    """
```

### 4.7 No Bare `except:`

Always catch specific exceptions. The project uses `except Exception:` as the widest net (which still allows `KeyboardInterrupt` and `SystemExit` to propagate).

### 4.8 Logging Over Printing

Use the `logging` module — never `print()`:
```python
logger = logging.getLogger(__name__)
logger.info("Starting GPU telemetry poller")
logger.error("Streaming error for %s: %s", path, exc)
logger.debug("Metrics broadcast loop cancelled")
```

---

## 5. Async / Concurrency Model

### 5.1 asyncio for I/O, threading for CPU

- **`asyncio`** is used for all network I/O (HTTP proxying, WebSocket broadcasting, SQLite async inserts).
- **`threading`** is used for the ring buffer's internal lock (`threading.RLock`) because the buffer is accessed from both async tasks and background threads.
- **`asyncio.Lock`** is used for async-only shared state (`RequestLifecycle`, `SessionCounters`, WebSocket client registry).

### 5.2 Task Creation

**DO use `asyncio.create_task()`** — it provides proper parent-child task tracking and integration with the asyncio debugger:
```python
_task = asyncio.create_task(asyncio.to_thread(_record_request_complete, ...))
_task.add_done_callback(_silent_task_callback)
```

**DO NOT use `asyncio.ensure_future()`** — it is a legacy wrapper that hides the created task from debuggers and profilers.

### 5.3 Fire-and-Forget with Callbacks

Background tasks that should not block the request path use the fire-and-forget pattern with a silent callback to suppress unhandled exception logs:
```python
_task = asyncio.create_task(asyncio.to_thread(some_func, ...))
_task.add_done_callback(_silent_task_callback)
```

The `_silent_task_callback` pattern:
```python
def _silent_task_callback(task: asyncio.Task) -> None:
    try:
        if task.done() and not task.cancelled():
            task.exception()
    except Exception:
        pass
```

### 5.4 Blocking Calls via `asyncio.to_thread()`

Synchronous/database calls are offloaded to a thread pool:
```python
await asyncio.to_thread(initialize_schema, str(db_path))
await asyncio.to_thread(insert_request, db_path, metrics)
```

**Never** call blocking I/O (file operations, database writes, CPU-heavy work) directly in an async handler.

### 5.5 Background Task Lifecycle

Background tasks are created during the FastAPI lifespan and properly cancelled on shutdown:
```python
# In lifespan:
app.state.telemetry_task = asyncio.create_task(start_telemetry_loop(...))

# On shutdown:
app.state.telemetry_task.cancel()
try:
    await app.state.telemetry_task
except asyncio.CancelledError:
    pass
```

---

## 6. Testing

### Running Tests

```bash
python -m pytest tests/ -v
```

### Test Organization

| Test File | Coverage |
|---|---|
| `test_metrics_buffer.py` | TimeSeriesRingBuffer — pushes, snapshots, wrap-around, thread safety |
| `test_usage_metrics.py` | UsageMetrics extraction, token rate calculation |
| `test_pricing.py` | Paid-model price lookup |
| `test_gpu_telemetry.py` | GPU monitor detection and telemetry snapshots |
| `test_dashboard_endpoints.py` | FastAPI route responses, health, history |
| `test_dashboard_revamp.py` | Dashboard payload structure, KPI fields |
| `test_chart_rendering.py` | Chart.js data rendering in browser via Playwright |
| `test_dashboard_e2e.py` | Full E2E: server subprocess + Playwright browser |

### Test Patterns

- **Unit tests** mock time using `unittest.mock.patch("time.time", ...)`.
- **E2E tests** spawn the real server in a subprocess with a temporary config and database.
- Fixtures are defined in `tests/conftest.py` with session scope for the server and temp database.

---

## 7. Linting & Formatting

The project uses **ruff** configured in `pyproject.toml`:

```toml
[tool.ruff]
line-length = 120
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "W", "I"]
```

| Rule Set | Description |
|---|---|
| `E` | pycodestyle errors |
| `F` | Pyflakes |
| `W` | pycodestyle warnings |
| `I` | isort (import ordering) |

Run before committing:
```bash
ruff check src/ tests/
ruff format --check src/ tests/
```

---

## 8. PyInstaller Builds

The project supports single-file binary distribution via PyInstaller. Spec files are in `scripts/`:

| Spec File | Purpose |
|---|---|
| `ollama-proxy.spec` | Builds the proxy server binary |
| `ollama-report.spec` | Builds the report generator binary |

Build commands:
```bash
pip install -e ".[build]"
pyinstaller scripts/ollama-proxy.spec
pyinstaller scripts/ollama-report.spec
```

**Important:** The `--onefile` mode packages everything into a single executable. The hook file `scripts/hook-ollama_usage_proxy.py` ensures bundled data files (`*.toml`, `static/**/*`) are included.

---

## 9. Configuration System

Configuration is loaded from TOML files using the `config.py` module. The system follows this resolution order:

1. **Bundled defaults** (`default_config.toml`) — always loaded first
2. **User config file** — located at:
   - Path specified by `--config` CLI flag
   - Path specified by `OLLAMA_PROXY_CONFIG` environment variable
   - Default location: `~/.local/share/ollama-usage-proxy/config.toml`

Configuration sections:
```toml
[proxy]
listen_host = "127.0.0.1"
listen_port = 11435
ollama_base_url = "http://127.0.0.1:11434"

[database]
path = "~/.local/share/ollama-usage-proxy/usage.db"

[reporting]
output_dir = "~/.local/share/ollama-usage-proxy/reports"
```

---

## 10. Database Schema

The SQLite database stores two tables:

### `usage_metrics`
| Column | Type | Description |
|---|---|---|
| `created_at` | TEXT (ISO 8601) | Request timestamp |
| `request_id` | TEXT (UUID hex) | Unique request identifier |
| `method` | TEXT | HTTP method |
| `path` | TEXT | Request path |
| `model` | TEXT \| NULL | Ollama model name |
| `status_code` | INT \| NULL | HTTP status from Ollama |
| `streaming` | BOOLEAN | Whether response was streamed |
| `input_tokens` | INT | Prompt/input token count |
| `output_tokens` | INT | Generation/output token count |
| `total_tokens` | INT | Sum of input + output |
| `total_duration_ns` | INT \| NULL | Total processing time (nanoseconds) |
| `load_duration_ns` | INT \| NULL | Model load time |
| `prompt_eval_duration_ns` | INT \| NULL | Prompt evaluation time |
| `eval_duration_ns` | INT \| NULL | Token generation time |
| `input_tokens_per_second` | REAL \| NULL | Derived: input token rate |
| `output_tokens_per_second` | REAL \| NULL | Derived: output token rate |
| `total_tokens_per_second` | REAL \| NULL | Derived: total token rate |
| `done_reason` | TEXT \| NULL | Ollama stop reason |
| `error` | TEXT \| NULL | Error message if request failed |

### `system_metrics`
| Column | Type | Description |
|---|---|---|
| `timestamp` | TEXT (ISO 8601) | Sample timestamp |
| `gpu_temp_c` | REAL \| NULL | GPU temperature |
| `gpu_power_w` | REAL \| NULL | GPU power draw |
| `gpu_util_pct` | REAL \| NULL | GPU core utilization |

Database initialization is handled by `initialize_schema()` in `db.py`. Async inserts use `ainsert_system_metrics()`; synchronous inserts use `insert_request()` wrapped in `asyncio.to_thread()`.

---

## 11. Frontend (Dashboard)

The dashboard is a **self-contained HTML file** (`src/ollama_usage_proxy/static/index.html`) with zero build steps.

### Technologies
- **Alpine.js** — Reactive state management (CDN)
- **Chart.js** — Chart rendering (CDN)

### WebSocket Protocol

The dashboard connects to `/api/ws/telemetry` and receives two message types:

**`metrics` message** (sent every second):
```json
{
  "type": "metrics",
  "status": "idle | thinking | generating",
  "in_flight_requests": 0,
  "total_session_input_tokens": 12345,
  "total_session_output_tokens": 67890,
  "gpu_online": true,
  "gpu_temp_c": 65.2,
  "gpu_power_w": 250.0,
  "gpu_util_pct": 82.0,
  "snapshot": [ /* 60 MetricSlot dicts */ ]
}
```

**`request` message** (sent on each completed proxy request):
```json
{
  "type": "request",
  "timestamp": "2025-01-15T10:30:00.000000+00:00",
  "model": "llama3",
  "input_tokens": 256,
  "output_tokens": 512,
  "total_tokens": 768,
  "output_tps": 50.0
}
```

### REST Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | Health check (status, version, gpu_online, lifecycle state, session counters) |
| `GET /api/history?limit=100` | Historical GPU metrics for chart seeding |
| `GET /dashboard` | Dashboard HTML |

---

## 12. Git Workflow

### Branches

| Branch | Purpose |
|---|---|
| `main` | Stable releases |
| `develop` | Integration branch |
| `feature/*` | Feature branches (e.g., `feature/realtime-dashboard`) |

### Commit Messages

Follow a conventional-commit style prefix:
- `feat:` — New feature
- `fix:` — Bug fix
- `test:` — Test additions or changes
- `refactor:` — Code restructuring without behavior change
- `docs:` — Documentation updates
- `chore:` — Maintenance (dependencies, config)

Example:
```
feat: add in-flight request status tracking
fix: decrement in-flight count on HTTP error path
test: add Playwright E2E tests for chart rendering
```

---

## Quick Reference

### Common Commands

```bash
# Development server
ollama-proxy --log-level debug

# Run all tests
python -m pytest tests/ -v

# Lint check
ruff check src/ tests/

# Format check
ruff format --check src/ tests/

# Single-file build
pyinstaller scripts/ollama-proxy.spec

# Generate report
ollama-report --from 2025-01-01 --to 2025-01-31 --group-by day
```

### Environment Variables

| Variable | Purpose |
|---|---|
| `OLLAMA_PROXY_CONFIG` | Path to custom `config.toml` |