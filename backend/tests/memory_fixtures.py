"""Inspect persisted Memory rows for archive, migration, and read-only regression tests.

Tests open an independent SQLite connection instead of retaining production browse/debug APIs.
The helper returns stored columns unchanged and never writes or owns application handles.
"""
from contextlib import closing
import sqlite3

from backend.memory.stores.sqlite import SQLiteMemoryStore


def memory_snapshot(store: SQLiteMemoryStore, profile_id: str) -> dict[str, list[dict]]:
    tables = {
        "episodes": "memory_episodes",
        "memories": "memory_items",
        "entities": "memory_entities",
        "relations": "memory_relations",
        "audit_logs": "memory_audit_logs",
    }
    with closing(sqlite3.connect(store.path)) as connection:
        connection.row_factory = sqlite3.Row
        return {
            name: [dict(row) for row in connection.execute(
                f"select * from {table} where profile_id = ? order by rowid", (profile_id,),
            )]
            for name, table in tables.items()
        }
