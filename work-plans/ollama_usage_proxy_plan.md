# Plan: Ollama Usage Proxy for Cline Token Tracking, Rates and Paid-Model Cost Comparison

## 1. Purpose

Build a local HTTP proxy that sits between **Cline** and **Ollama** so that every model request can be observed and recorded.

The proxy will capture:

- input token usage
- output token usage
- total token usage
- prompt processing duration
- generation duration
- total request duration
- input token rate
- output token rate
- estimated equivalent cost if the same tokens had been sent to a paid LLM provider

The reporting script will generate graphs for:

- token usage over time
- token rates over time
- paid-model equivalent cost over time
- cumulative paid-model equivalent cost

The initial implementation should use **SQLite** rather than CSV so that results are durable, queryable and less awkward to extend later.

---

## 2. Target Architecture

```text
Cline
  |
  | HTTP request
  v
Ollama usage proxy
  |
  | forwards request
  v
Ollama
  |
  | streaming or non-streaming response
  v
Ollama usage proxy
  |
  | records final usage metrics
  v
SQLite database
  |
  | read by reporting script
  v
Graphs and cost reports
```

Cline should use the proxy as its Ollama base URL.

Example:

```text
Cline provider: Ollama
Base URL: http://localhost:11435
Actual Ollama: http://localhost:11434
```

---

## 3. Key Ollama Metrics to Capture

Ollama responses expose usage and timing fields that can be used for measurement.

The proxy should capture these fields from the final response payload:

| Ollama field | Meaning in this project |
|---|---|
| `model` | Model used for the request |
| `prompt_eval_count` | Input tokens processed |
| `eval_count` | Output tokens generated |
| `total_duration` | Total request duration in nanoseconds |
| `load_duration` | Model load duration in nanoseconds |
| `prompt_eval_duration` | Prompt evaluation duration in nanoseconds |
| `eval_duration` | Output generation duration in nanoseconds |
| `done` | Indicates final streaming chunk when `true` |

Derived values:

```text
input_tokens       = prompt_eval_count
output_tokens      = eval_count
total_tokens       = input_tokens + output_tokens
input_tokens_sec   = input_tokens / (prompt_eval_duration / 1_000_000_000)
output_tokens_sec  = output_tokens / (eval_duration / 1_000_000_000)
total_seconds      = total_duration / 1_000_000_000
```

Guard against divide-by-zero when durations are missing or zero.

---

## 4. Project Structure

Recommended initial layout:

```text
ollama-usage-proxy/
  README.md
  pyproject.toml
  .gitignore
  config.example.toml
  data/
    .gitkeep
  reports/
    .gitkeep
  src/
    ollama_usage_proxy/
      __init__.py
      app.py
      config.py
      db.py
      models.py
      pricing.py
      usage.py
  scripts/
    init_db.py
    report_usage.py
    export_csv.py
  tests/
    test_usage_metrics.py
    test_pricing.py
```

For a quick proof of concept, this can be simplified to:

```text
ollama_usage_proxy.py
init_db.py
report_usage.py
prices.toml
usage.db
```

---

## 5. Configuration

Use a small TOML configuration file.

Example `config.toml`:

```toml
[proxy]
listen_host = "127.0.0.1"
listen_port = 11435
ollama_base_url = "http://127.0.0.1:11434"

[database]
path = "usage.db"

[reporting]
output_dir = "reports"

```

Pricing should be kept separately so it can be updated without touching code.

Example `prices.toml`:

```toml
[[paid_models]]
name = "claude-sonnet-example"
currency = "USD"
input_per_million = 3.00
output_per_million = 15.00

[[paid_models]]
name = "gpt-coding-example"
currency = "USD"
input_per_million = 1.75
output_per_million = 14.00

[[paid_models]]
name = "cheap-hosted-example"
currency = "USD"
input_per_million = 0.50
output_per_million = 1.50
```

Do not hard-code provider prices in the reporting logic. Treat them as user-maintained assumptions.

---

## 6. SQLite Schema

### 6.1 `requests` table

Stores one row per completed Ollama model request.

```sql
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    request_id TEXT,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    model TEXT,
    status_code INTEGER,
    streaming INTEGER NOT NULL DEFAULT 0,

    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,

    total_duration_ns INTEGER,
    load_duration_ns INTEGER,
    prompt_eval_duration_ns INTEGER,
    eval_duration_ns INTEGER,

    input_tokens_per_second REAL,
    output_tokens_per_second REAL,
    total_tokens_per_second REAL,

    done_reason TEXT,
    error TEXT
);
```

### 6.2 `paid_model_prices` table

Optional if using a TOML/CSV pricing file, but useful if you want repeatable historical reports.

```sql
CREATE TABLE IF NOT EXISTS paid_model_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    model_name TEXT NOT NULL,
    currency TEXT NOT NULL,
    input_per_million REAL NOT NULL,
    output_per_million REAL NOT NULL
);
```

### 6.3 `report_runs` table

Optional audit trail of generated reports.

```sql
CREATE TABLE IF NOT EXISTS report_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    report_type TEXT NOT NULL,
    output_path TEXT NOT NULL,
    notes TEXT
);
```

---

## 7. Proxy Behaviour

### 7.1 Request handling

The proxy should:

1. Accept any HTTP method and path used by Ollama.
2. Forward the request to the real Ollama base URL.
3. Stream the response back to Cline without buffering the full response if the upstream response is streaming.
4. Inspect the final Ollama JSON object for usage metrics.
5. Write one completed row to SQLite.

Important: logging must not delay the stream of tokens back to Cline.

### 7.2 Streaming responses

For streaming endpoints such as chat/generate style requests:

1. Forward each newline-delimited JSON chunk to Cline as soon as it arrives.
2. Keep track of the latest payload where `done` is `true`.
3. When the stream completes, extract usage fields from that final payload.
4. Insert the usage row into SQLite.

### 7.3 Non-streaming responses

For non-streaming JSON responses:

1. Forward the response body to Cline.
2. Parse the JSON body.
3. If usage fields exist, insert them into SQLite.

### 7.4 Error handling

The proxy should not break Cline if logging fails.

Recommended behaviour:

- If Ollama returns an error, forward that error to Cline.
- Record status code and error text if possible.
- If SQLite insert fails, log to stderr but still return the Ollama response.
- If the response cannot be parsed, record a minimal row only if useful.

---

## 8. SQLite Write Strategy

For a single-user Cline setup, direct SQLite inserts are likely sufficient.

Recommended initial approach:

```text
one completed request -> one SQLite insert
```

If contention or latency becomes visible, move to:

```text
request handler -> in-memory queue -> background writer -> SQLite
```

Suggested default SQLite settings:

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
```

This improves write/read concurrency for the reporting script while the proxy is running.

---

## 9. Reporting Script Requirements

Create a script called:

```text
scripts/report_usage.py
```

It should read from SQLite and generate graphs into the report output directory.

### 9.1 Inputs

The script should accept:

```bash
python scripts/report_usage.py \
  --db usage.db \
  --prices prices.toml \
  --output-dir reports
```

Optional flags:

```text
--from YYYY-MM-DD
--to YYYY-MM-DD
--group-by hour|day|week|month
--currency USD
```

### 9.2 Outputs

Generate:

```text
reports/token_usage_daily.png
reports/token_rates_daily.png
reports/paid_model_cost_daily.png
reports/paid_model_cost_cumulative.png
reports/summary.csv
reports/summary.md
```

---

## 10. Graphs to Generate

### 10.1 Token usage graph

Graph daily/hourly token usage:

- input tokens
- output tokens
- total tokens

Suggested chart:

```text
Line chart or stacked bar chart
X axis: date/time bucket
Y axis: tokens
```

### 10.2 Token rate graph

Graph average token rates:

- input tokens/sec
- output tokens/sec

Suggested chart:

```text
Line chart
X axis: date/time bucket
Y axis: tokens/sec
```

For each time bucket:

```sql
AVG(input_tokens_per_second)
AVG(output_tokens_per_second)
```

Also consider weighted rates:

```text
weighted_input_tokens_sec = SUM(input_tokens) / SUM(prompt_eval_duration_ns / 1e9)
weighted_output_tokens_sec = SUM(output_tokens) / SUM(eval_duration_ns / 1e9)
```

Weighted rates are usually more meaningful for aggregate reporting.

### 10.3 Paid-model equivalent cost graph

For each paid model:

```text
input_cost  = input_tokens  / 1_000_000 * input_per_million
output_cost = output_tokens / 1_000_000 * output_per_million
total_cost  = input_cost + output_cost
```

Generate:

- daily cost by model
- cumulative cost by model
- summary table by model

---

## 11. Summary Report

Generate a Markdown summary file:

```text
reports/summary.md
```

It should include:

```markdown
# Ollama Usage Summary

## Period

- From: YYYY-MM-DD
- To: YYYY-MM-DD

## Token Usage

- Input tokens: n
- Output tokens: n
- Total tokens: n

## Token Rates

- Weighted input tokens/sec: n
- Weighted output tokens/sec: n

## Paid-Model Equivalent Cost

| Model | Input cost | Output cost | Total cost |
|---|---:|---:|---:|
| claude-sonnet-example | $x.xx | $x.xx | $x.xx |

```

---

## 12. Implementation Phases

### Phase 1: Minimal proxy and database

Deliverables:

- FastAPI or equivalent Python proxy
- SQLite database initialisation script
- request forwarding to Ollama
- streaming response passthrough
- capture final usage payload
- insert usage row into SQLite

Acceptance criteria:

- Cline can use the proxy as its Ollama base URL.
- Cline receives responses normally.
- SQLite receives one row per completed model request.
- Input and output tokens are populated when Ollama returns usage metrics.

---

### Phase 2: Token usage reporting

Deliverables:

- reporting script
- daily token usage graph
- daily token rate graph
- summary CSV
- summary Markdown

Acceptance criteria:

- Running the report script against `usage.db` creates PNG graphs.
- Report includes input, output and total token counts.
- Report includes weighted input/output token rates.

---

### Phase 3: Paid-model cost comparison

Deliverables:

- pricing config file
- cost calculation module
- cost graphs
- cost summary table

Acceptance criteria:

- User can add/edit paid model prices without code changes.
- Report shows equivalent cost for each configured paid model.
- Cost calculations are split into input, output and total cost.

---

### Phase 4: Hardening and convenience

Deliverables:

- service file or container setup
- configurable log level
- graceful shutdown
- database backup/export script
- tests for usage extraction and pricing maths

Acceptance criteria:

- Proxy can be started consistently from a shell or service manager.
- Reporting can run while proxy is still writing to SQLite.
- Unit tests cover pricing and usage metric extraction.

---

## 13. Suggested Dependencies

Python packages:

```text
fastapi
uvicorn
httpx
pandas
matplotlib
tomli; python_version < "3.11"
```

Optional:

```text
rich
pytest
ruff
```

Standard library modules to use:

```text
sqlite3
asyncio
datetime
uuid
json
logging
pathlib
argparse
```

---

## 14. Security and Privacy Considerations

Avoid storing full prompts and responses by default.

The first version should store only metadata:

- timestamp
- path
- model
- status
- token counts
- durations
- rates
- errors

Do not store:

- prompt text
- generated code
- repository file contents
- secrets
- Cline task contents

If later adding prompt capture, make it explicit and disabled by default.

---

## 15. `.gitignore`

Suggested `.gitignore`:

```gitignore
.venv/
__pycache__/
*.pyc
usage.db
usage.db-shm
usage.db-wal
data/*.db
reports/*.png
reports/*.csv
.env
config.toml
```

Keep examples in source control:

```text
config.example.toml
prices.example.toml
```

---

## 16. Initial Manual Test Plan

1. Start Ollama normally.
2. Start the proxy on port `11435`.
3. Configure Cline to use `http://localhost:11435` as the Ollama base URL.
4. Run a small Cline task.
5. Confirm the task completes successfully.
6. Inspect SQLite:

```bash
sqlite3 usage.db 'select created_at, model, input_tokens, output_tokens from requests order by id desc limit 5;'
```

7. Run the report script:

```bash
python scripts/report_usage.py --db usage.db --prices prices.toml --output-dir reports
```

8. Confirm these files are created:

```text
reports/token_usage_daily.png
reports/token_rates_daily.png
reports/paid_model_cost_daily.png
reports/paid_model_cost_cumulative.png
reports/summary.csv
reports/summary.md
```

---

## 17. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Proxy breaks streaming behaviour | Pass through chunks immediately and only process metadata at stream end |
| SQLite write failure affects Cline | Catch database exceptions and never block the Ollama response |
| Paid model prices change | Keep prices in config and record assumptions in generated reports |
| Token counters missing from response | Insert row with zero/null metrics and flag the row for investigation |
| Reports read while proxy writes | Use SQLite WAL mode |
| Sensitive data captured accidentally | Store metadata only by default |

---

## 18. Definition of Done

The project is complete when:

- Cline can use the proxy transparently.
- Ollama responses continue to stream normally.
- SQLite captures usage metrics for completed requests.
- Token usage graphs can be generated.
- Token rate graphs can be generated.
- Paid-model equivalent cost graphs can be generated.
- A Markdown summary report is produced.
- Paid-model pricing assumptions are editable without code changes.
- No prompt or response content is stored by default.

---

## 19. Future Enhancements

Potential follow-on improvements:

- Small local dashboard using Streamlit or FastAPI templates.
- Per-project or per-repository tagging.
- Cline task/session ID capture if available.
- Export to Prometheus/Grafana.
- Budget alerts.
- Compare local model throughput across different quantisations.
- Add model quality notes alongside cost data.
- Add support for multiple local Ollama hosts.

