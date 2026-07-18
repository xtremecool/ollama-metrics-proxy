"""SQLite database layer for storing usage metrics."""

from __future__ import annotations

import sqlite3
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import ollama_usage_proxy.models as _models

UsageMetrics = _models.UsageMetrics

logger = logging.getLogger(__name__)

# Schema version for potential future migrations
SCHEMA_VERSION = 1


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """Create a new SQLite connection with sensible defaults.

    Uses WAL journal mode and NORMAL synchronous for better concurrency.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def transaction(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """Context manager for a database transaction.

    Commits on success, rolls back on exception.
    The connection is always closed.
    """
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_schema(db_path: str | Path) -> None:
    """Create tables if they do not exist and apply initial schema.

    This is idempotent and safe to call multiple times.
    """
    with transaction(db_path) as conn:
        # Main requests table
        conn.execute("""
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
            )
        """)

        # Paid model prices reference table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paid_model_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                model_name TEXT NOT NULL,
                currency TEXT NOT NULL,
                input_per_million REAL NOT NULL,
                output_per_million REAL NOT NULL
            )
        """)

        # Report audit trail table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS report_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                report_type TEXT NOT NULL,
                output_path TEXT NOT NULL,
                notes TEXT
            )
        """)

        # Performance indexes
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_requests_created_at ON requests(created_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_requests_model ON requests(model)
        """)

    logger.info("Database schema initialised at %s", db_path)


def insert_request(db_path: str | Path, metrics: UsageMetrics) -> None:
    """Insert a completed request's usage metrics into the database.

    This operation is wrapped in its own transaction so it does not block
    the proxy response. If it fails, the error is logged but not raised
    to avoid breaking Cline's connection.
    """
    try:
        with transaction(db_path) as conn:
            conn.execute(
                """
                INSERT INTO requests (
                    created_at, request_id, method, path, model, status_code, streaming,
                    input_tokens, output_tokens, total_tokens,
                    total_duration_ns, load_duration_ns, prompt_eval_duration_ns, eval_duration_ns,
                    input_tokens_per_second, output_tokens_per_second, total_tokens_per_second,
                    done_reason, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    metrics.created_at,
                    metrics.request_id,
                    metrics.method,
                    metrics.path,
                    metrics.model,
                    metrics.status_code,
                    1 if metrics.streaming else 0,
                    metrics.input_tokens,
                    metrics.output_tokens,
                    metrics.total_tokens,
                    metrics.total_duration_ns,
                    metrics.load_duration_ns,
                    metrics.prompt_eval_duration_ns,
                    metrics.eval_duration_ns,
                    metrics.input_tokens_per_second,
                    metrics.output_tokens_per_second,
                    metrics.total_tokens_per_second,
                    metrics.done_reason,
                    metrics.error,
                ),
            )
        logger.debug(
            "Inserted request %s: model=%s, input_tokens=%d, output_tokens=%d",
            metrics.request_id,
            metrics.model,
            metrics.input_tokens,
            metrics.output_tokens,
        )
    except Exception as e:
        # Must not break the proxy response
        logger.error("Failed to insert request metrics: %s", e)


def get_total_requests_count(db_path: str | Path) -> int:
    """Return the total number of requests in the database."""
    with get_connection(db_path) as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM requests")
        row = cursor.fetchone()
        return row[0] if row else 0