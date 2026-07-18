# Ollama Usage Proxy

A transparent HTTP proxy that sits between **Cline** (or any Ollama client) and your local **Ollama** server to track token usage, processing rates, and estimate equivalent costs if the same tokens had been sent to paid LLM providers.

## Features

- **Transparent proxy** - Forwards all requests to Ollama with zero changes to client behaviour
- **Streaming support** - Passes through streaming responses in real-time without buffering
- **SQLite storage** - Durable, queryable local database for all request metrics
- **Token tracking** - Captures input/output token counts, durations, and per-request rates
- **Cost comparison** - Estimates equivalent cost against configurable paid-model pricing
- **Graph generation** - Produces PNG graphs for token usage, rates, and costs over time
- **Summary reports** - CSV and Markdown summaries with aggregated statistics
- **Privacy-first** - Stores only metadata (token counts, durations); no prompts or responses are captured

## Architecture

```
Cline / Ollama Client
       |
       | HTTP request
       v
Ollama Usage Proxy (localhost:11435)
       |
       | forwards request
       v
Ollama (localhost:11434)
       |
       | streaming or non-streaming response
       v
Ollama Usage Proxy
       |
       | records final usage metrics
       v
SQLite Database (~/.local/share/ollama-usage-proxy/usage.db)
```

## Requirements

- Python 3.10+
- Ollama running locally

## Installation

### Option A: Run standalone executables (PyInstaller builds)

The `scripts/run.sh` script auto-builds self-contained executables using PyInstaller on first run. No virtual environment or manual dependency management needed.

```bash
# First run builds the executables, then starts the proxy
./scripts/run.sh
```

The executables are placed in `dist/ollama-proxy/` and `dist/ollama-report/`. On subsequent runs they are reused directly without rebuilding.

### Option B: Development install

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"
```

Then start the proxy and generate reports from any directory:

```bash
ollama-proxy          # starts the proxy
ollama-report         # generates usage reports
```

Both commands are available once the venv is activated.

### Configuration files (optional)

Copy and edit example files if you want custom settings:

```bash
cp examples/config.example.toml config.toml
cp examples/prices.example.toml prices.toml
```

Edit `config.toml` to match your setup:

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

Edit `prices.toml` to add the paid-model pricing you want to compare against:

```toml
[[paid_models]]
name = "claude-sonnet-4-20250514"
currency = "USD"
input_per_million = 3.00
output_per_million = 15.00

[[paid_models]]
name = "gpt-4.1-mini"
currency = "USD"
input_per_million = 0.40
output_per_million = 1.60
```

Without user config files, bundled defaults are used automatically.

## Usage

### Starting the proxy

```bash
# Using the bootstrap script (port 20000 for testing)
./scripts/run.sh

# On a different port
./scripts/run.sh --port 11435

# With debug logging
./scripts/run.sh --log-level debug
```

**CLI options:**

| Option | Default | Description |
|---|---|---|
| `--config` | bundled default | Path to config.toml file |
| `--host` | 127.0.0.1 | Override listen host |
| `--port` | 11435 (default), 20000 (run.sh) | Override listen port |
| `--ollama-url` | http://127.0.0.1:11434 | Override Ollama base URL |
| `--log-level` | info | Logging level: debug, info, warning, error |

### Configuring Cline to use the proxy

In your Cline settings, configure Ollama with the proxy address:

```
Provider: Ollama
Base URL: http://localhost:11435
```

The default Ollama base URL is `http://localhost:11434`. Change it to point at the proxy (`:11435`). All your existing models and settings work as before.

### Generating reports

**Option A: CLI command (development install)**

After installing with `pip install -e .`, the `ollama-report` command is available from any directory:

```bash
ollama-report
```

**Option B: bundled executable (PyInstaller build)**

```bash
dist/ollama-report/ollama-report
```

**Option C: direct Python invocation**

```bash
python3 -m ollama_usage_proxy.report_main

With optional date filtering and aggregation:

```bash
ollama-report \
    --from 2025-01-01 \
    --to 2025-01-31 \
    --group-by day
```

**Report generator options:**

| Option | Default | Description |
|---|---|---|
| `--db` | `~/.local/share/ollama-usage-proxy/usage.db` | Path to the SQLite database |
| `--prices` | bundled default (falls back automatically) | Path to a custom pricing TOML file |
| `--output-dir` | `~/.local/share/ollama-usage-proxy/reports` | Directory for generated reports |
| `--from` | auto based on bucket | Start date (YYYY-MM-DD). If omitted, defaults to: 24h for hourly, today for `today`, 7 days for daily, 12 weeks for weekly, 12 months for monthly |
| `--to` | present | End date (YYYY-MM-DD) |
| `--group-by` | `day` | Time bucket: `hour`, `today`, `day`, `week`, `month`. The `today` option shows midnight-to-now with hourly buckets |

**Pricing file:** By default, the report generator looks for a `prices.toml` in the current working directory. If it is not found, it automatically falls back to the bundled default pricing included with the package. To use custom pricing, place a `prices.toml` in the current directory or pass its path explicitly with `--prices`.

**Default date ranges:** When you omit `--from`, the report uses a sensible window matching the aggregation bucket (e.g. daily buckets show the last 7 days). This prevents graphs from showing only a few minutes of data when the proxy was recently started. Pass explicit `--from`/`--to` dates to override.

### Generated report files

```text
~/.local/share/ollama-usage-proxy/reports/
  token_usage_daily.png        # Line chart of input/output/total tokens over time
  token_rates_daily.png        # Line chart of weighted token processing rates
  paid_model_cost_daily.png    # Daily equivalent cost by paid model
  paid_model_cost_cumulative.png  # Cumulative equivalent cost
  summary.csv                  # CSV with aggregated metrics and costs
  summary.md                   # Human-readable Markdown report
```

### Exporting raw data to CSV

```bash
python3 scripts/export_csv.py \
    --db ~/.local/share/ollama-usage-proxy/usage.db \
    --output requests.csv
```

## Running Tests

```bash
python3 -m pytest tests/ -v
```

## Inspecting the Database

You can query the database directly:

```bash
sqlite3 ~/.local/share/ollama-usage-proxy/usage.db \
  "SELECT created_at, model, input_tokens, output_tokens, total_tokens FROM requests ORDER BY id DESC LIMIT 10;"
```

Or use the CSV export script for spreadsheet analysis.

## Database Schema

### `requests` table

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER | Auto-incrementing primary key |
| `created_at` | TEXT | ISO 8601 timestamp (UTC) |
| `request_id` | TEXT | Unique hex identifier |
| `method` | TEXT | HTTP method (GET, POST, etc.) |
| `path` | TEXT | Request path (e.g. `/api/chat`) |
| `model` | TEXT | Ollama model name |
| `status_code` | INTEGER | HTTP status code from Ollama |
| `streaming` | INTEGER | 1 if streaming, 0 otherwise |
| `input_tokens` | INTEGER | Prompt/input token count |
| `output_tokens` | INTEGER | Generated/output token count |
| `total_tokens` | INTEGER | Sum of input + output |
| `total_duration_ns` | INTEGER | Total request duration (nanoseconds) |
| `load_duration_ns` | INTEGER | Model load time (nanoseconds) |
| `prompt_eval_duration_ns` | INTEGER | Prompt processing duration (nanoseconds) |
| `eval_duration_ns` | INTEGER | Output generation duration (nanoseconds) |
| `input_tokens_per_second` | REAL | Input token rate |
| `output_tokens_per_second` | REAL | Output token rate |
| `total_tokens_per_second` | REAL | Overall token rate |
| `done_reason` | TEXT | Stream completion reason |
| `error` | TEXT | Error message if request failed |

## Metrics Explained

### Token Counts

- **Input tokens** (`prompt_eval_count` from Ollama) - tokens in the prompt/context sent to the model
- **Output tokens** (`eval_count` from Ollama) - tokens generated by the model as response

### Token Rates

Rates are calculated per-request and also aggregated as weighted averages:

```
input_tokens_per_second = input_tokens / (prompt_eval_duration_ns / 1e9)
output_tokens_per_second = output_tokens / (eval_duration_ns / 1e9)
```

For aggregate reporting, the report script uses **weighted rates**:

```
weighted_input_rate = SUM(input_tokens) / SUM(prompt_eval_duration_s)
weighted_output_rate = SUM(output_tokens) / SUM(eval_duration_s)
```

Weighted rates are more meaningful than averaging per-request rates.

### Paid-Model Equivalent Cost

For each configured paid model:

```
input_cost  = input_tokens  / 1,000,000 * input_per_million_price
output_cost = output_tokens / 1,000,000 * output_per_million_price
total_cost  = input_cost + output_cost
```

Prices are loaded from your `prices.toml` file and can be updated without code changes.

## Privacy

By default, the proxy stores **only metadata**:

- Timestamps
- HTTP method and path
- Model name
- Status codes
- Token counts and durations
- Derived rates
- Error messages

It does **not** store:

- Prompt text or content
- Generated responses or code
- Repository file contents
- Cline task details
- Any secrets or API keys

## Troubleshooting

### Proxy starts but Cline cannot connect

Verify that:
1. Ollama is running on the expected port (default `11434`)
2. The proxy is listening on the port you configured in Cline
3. Check proxy logs for connection errors

```bash
curl http://localhost:11435/health  # Should return {"status": "ok", "proxy": true}
```

### No data in reports after running tasks

Make sure:
1. Cline is pointing at the proxy URL (port 11435), not directly at Ollama
2. The database path matches where the proxy actually stores data (default `~/.local/share/ollama-usage-proxy/usage.db`)
3. Check that the proxy log shows requests being processed

### Streaming responses appear slow

The proxy processes each chunk sequentially and feeds metrics from the final chunk. This should add negligible latency. If you notice delays, check:
1. SQLite disk I/O (consider SSD)
2. Database file permissions
3. Proxy logs for errors

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
Attribution to the original author must be provided in derivative works.