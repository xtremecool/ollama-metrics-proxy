"""CSV export script.

Exports raw request records from the SQLite database to a CSV file.

Usage:
    python scripts/export_csv.py --db usage.db [--output requests.csv]
"""

from __future__ import annotations

import argparse
import csv
import logging
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("export_csv")


def export(
    db_path: str | Path,
    output_path: str | Path,
) -> None:
    """Export all request records to CSV.

    Args:
        db_path: Path to the SQLite database.
        output_path: Path for the output CSV file.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    cursor = conn.execute("""
        SELECT *
        FROM requests
        ORDER BY created_at ASC
    """)

    rows = cursor.fetchall()
    if not rows:
        logger.warning("No records to export.")
        conn.close()
        return

    columns = list(rows[0].keys())

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))

    conn.close()
    logger.info("Exported %d records to %s", len(rows), output_file)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Ollama proxy request data to CSV.")
    parser.add_argument(
        "--db",
        type=str,
        required=True,
        help="Path to SQLite database file.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="requests.csv",
        help="Output CSV file path (default: requests.csv).",
    )

    args = parser.parse_args()
    export(args.db, args.output)


if __name__ == "__main__":
    main()