"""Database initialisation script.

Creates the SQLite database and required tables if they do not exist.
Safe to run multiple times - schema creation is idempotent.

Usage:
    python scripts/init_db.py [--db usage.db]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Allow importing from the parent directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ollama_usage_proxy.db import initialize_schema

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("init_db")


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialise the Ollama usage database.")
    parser.add_argument(
        "--db",
        type=str,
        default="usage.db",
        help="Path to SQLite database file (default: usage.db)",
    )

    args = parser.parse_args()
    db_path = Path(args.db)

    # Ensure parent directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Creating/verifying database at %s", db_path)
    initialize_schema(db_path)
    logger.info("Database ready.")


if __name__ == "__main__":
    main()