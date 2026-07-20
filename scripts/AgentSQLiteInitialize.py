#!/usr/bin/env python3
"""Initialize the SQLite schema used by AuraBot's Agent subsystem.

The initializer is intentionally idempotent:

* the database file and its parent directory are created only when absent;
* every table and index uses ``IF NOT EXISTS``;
* existing rows are never deleted or replaced;
* all schema changes run in one transaction and are rolled back on failure.

Schema reference: ``backend/agent/SQLite表格.md``.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agent.integration.sql.utils import (  # noqa: E402
    SQLITE_PATH,
    SchemaInitializationError,
    initialize_schema,
)

DEFAULT_DATABASE_PATH = SQLITE_PATH


def _resolve_database_path(database_path: str | Path) -> Path:
    path = Path(database_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def initialize_database(
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> Path:
    """Create any missing Agent tables and indexes, then return the DB path.

    Calling this function repeatedly against the same database is safe. Existing
    tables, indexes, and rows are preserved. The function does not seed native
    records because no canonical native Server, ToolGroup, Tool, or Skill rows
    are currently defined by the schema document.
    """

    resolved_path = _resolve_database_path(database_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    initialize_schema(resolved_path)

    return resolved_path


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create missing tables and indexes in the AuraBot Agent database."
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=(
            "SQLite database path. Defaults to "
            f"{DEFAULT_DATABASE_PATH.relative_to(PROJECT_ROOT)}"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_argument_parser().parse_args(argv)

    try:
        database_path = initialize_database(args.db_path)
    except (OSError, sqlite3.Error, SchemaInitializationError) as exc:
        print(f"Failed to initialize Agent SQLite database: {exc}", file=sys.stderr)
        return 1

    print(f"Agent SQLite database initialized: {database_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
