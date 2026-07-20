import sqlite3
from pathlib import Path

SQLITE_PATH = Path(__file__).with_name("agent_runtime_sql.sqlite")


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS skill_group (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        description TEXT,
        is_builtin INTEGER NOT NULL DEFAULT 0
            CHECK (is_builtin IN (0, 1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS skill (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        url TEXT NOT NULL,
        is_builtin INTEGER NOT NULL DEFAULT 0
            CHECK (is_builtin IN (0, 1)),
        is_global_url INTEGER NOT NULL DEFAULT 0
            CHECK (is_global_url IN (0, 1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (group_id)
            REFERENCES skill_group(id)
            ON UPDATE CASCADE
            ON DELETE CASCADE,

        UNIQUE (group_id, name)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_skill_group_id
    ON skill(group_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_skill_name
    ON skill(name)
    """,
    """
    CREATE TABLE IF NOT EXISTS mcp_server (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        url TEXT NOT NULL UNIQUE,
        description TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tool_group (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id INTEGER NOT NULL,
        name TEXT NOT NULL UNIQUE,
        description TEXT,
        is_native INTEGER NOT NULL DEFAULT 0
            CHECK (is_native IN (0, 1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (server_id)
            REFERENCES mcp_server(id)
            ON UPDATE CASCADE
            ON DELETE RESTRICT,

        UNIQUE (server_id, id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_tool_group_server_id
    ON tool_group(server_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS tool (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id INTEGER NOT NULL,
        group_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT NOT NULL,
        input_schema_json TEXT NOT NULL
            CHECK (
                json_valid(input_schema_json)
                AND json_type(input_schema_json) = 'object'
            ),
        output_schema_json TEXT
            CHECK (
                output_schema_json IS NULL
                OR (
                    json_valid(output_schema_json)
                    AND json_type(output_schema_json) = 'object'
                )
            ),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (server_id)
            REFERENCES mcp_server(id)
            ON UPDATE CASCADE
            ON DELETE RESTRICT,

        FOREIGN KEY (server_id, group_id)
            REFERENCES tool_group(server_id, id)
            ON UPDATE CASCADE
            ON DELETE RESTRICT,

        UNIQUE (server_id, name)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_tool_server_id
    ON tool(server_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_tool_group_id
    ON tool(group_id)
    """,
)


class SchemaInitializationError(RuntimeError):
    """Raised when the Agent database cannot be initialized safely."""


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(SQLITE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Idempotently create all Agent tables and indexes."""

    connection.execute("PRAGMA foreign_keys = ON")
    foreign_keys_enabled = connection.execute(
        "PRAGMA foreign_keys"
    ).fetchone()
    if foreign_keys_enabled is None or foreign_keys_enabled[0] != 1:
        raise SchemaInitializationError(
            "SQLite foreign key enforcement could not be enabled"
        )

    try:
        connection.execute("BEGIN IMMEDIATE")
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)

        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise SchemaInitializationError(
                "Existing Agent database contains foreign key violations: "
                f"{violations!r}"
            )

        connection.commit()
    except Exception:
        connection.rollback()
        raise
