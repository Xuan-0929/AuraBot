import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
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


def create_connection(
    database_path: str | Path | None = None,
) -> sqlite3.Connection:
    """Create one configured, caller-owned Agent SQLite connection."""
    path = Path(database_path) if database_path is not None else SQLITE_PATH
    connection = sqlite3.connect(path, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    foreign_keys_enabled = connection.execute(
        "PRAGMA foreign_keys"
    ).fetchone()
    if foreign_keys_enabled is None or foreign_keys_enabled[0] != 1:
        connection.close()
        raise SchemaInitializationError(
            "SQLite foreign key enforcement could not be enabled"
        )

    return connection


@contextmanager
def read_connection(
    database_path: str | Path | None = None,
) -> Iterator[sqlite3.Connection]:
    """Open one configured connection for a complete logical read."""
    connection = create_connection(database_path)
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def transaction(
    database_path: str | Path | None = None,
) -> Iterator[sqlite3.Connection]:
    """Open, commit or roll back, then close one write transaction."""
    connection = create_connection(database_path)
    try:
        connection.execute("BEGIN")
        yield connection
    except BaseException:
        connection.rollback()
        raise
    else:
        try:
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    finally:
        connection.close()


def initialize_schema(
    database_path: str | Path | None = None,
) -> None:
    """Initialize the idempotent Agent schema once for a database lifecycle."""
    with closing(create_connection(database_path)) as connection:
        ensure_schema(connection)


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Idempotently create all Agent tables and indexes during initialization."""

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
