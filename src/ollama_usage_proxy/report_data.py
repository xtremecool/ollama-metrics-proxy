"""Data fetching and resampling for usage reports."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

logger = logging.getLogger("report_usage")


def fetch_requests(
    db_path: str | Path,
    from_date: str | None = None,
    to_date: str | None = None,
) -> pd.DataFrame:
    """Read all request records from the database into a DataFrame."""
    conn = sqlite3.connect(str(db_path))

    query = """
        SELECT
            created_at, request_id, method, path, model, status_code,
            streaming, input_tokens, output_tokens, total_tokens,
            total_duration_ns, load_duration_ns, prompt_eval_duration_ns,
            eval_duration_ns, input_tokens_per_second, output_tokens_per_second,
            total_tokens_per_second, done_reason, error
        FROM requests WHERE 1=1
    """
    params: list[str] = []

    if from_date:
        query += " AND created_at >= ?"
        params.append(from_date)

    if to_date:
        next_day = datetime.strptime(to_date, "%Y-%m-%d") + pd.Timedelta(days=1)
        query += " AND created_at < ?"
        params.append(next_day.strftime("%Y-%m-%dT%H:%M:%S"))

    query += " ORDER BY created_at ASC"

    df = pd.read_sql_query(query, conn, params=params)
    conn.close()

    if df.empty:
        logger.warning("No request records found matching criteria.")
        return df

    df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
    return df


def resample_dataframe(df: pd.DataFrame, group_by: str = "day") -> pd.DataFrame:
    """Resample the DataFrame by time bucket and aggregate metrics."""
    freq_map = {"hour": "h", "today": "h", "day": "D", "week": "W-MON", "month": "MS"}
    freq = freq_map.get(group_by, "D")

    df = df.set_index("created_at").sort_index()

    agg_dict = {
        "input_tokens": ["sum"],
        "output_tokens": ["sum"],
        "total_tokens": ["sum"],
        "prompt_eval_duration_ns": ["sum"],
        "eval_duration_ns": ["sum"],
        "method": "count",
    }

    result = df.resample(freq).agg(agg_dict)
    result.columns = [f"{col[0]}_{col[1]}" if col[1] else col[0] for col in result.columns]

    total_prompt_sec = (result["prompt_eval_duration_ns_sum"] / 1e9).replace(0, float("nan"))
    total_eval_sec = (result["eval_duration_ns_sum"] / 1e9).replace(0, float("nan"))

    result["weighted_input_tps"] = result["input_tokens_sum"] / total_prompt_sec
    result["weighted_output_tps"] = result["output_tokens_sum"] / total_eval_sec
    result = result[result["method_count"] > 0]

    return result