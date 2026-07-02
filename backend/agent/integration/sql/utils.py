import sqlite3
from pathlib import Path

SQLITE_PATH = Path(__file__).with_name("agent_runtime_sql.sqlite")


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(SQLITE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    """
    幂等地初始化Skill系统需要的SQLite表格。
    """
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS skill_group (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            is_builtin INTEGER NOT NULL DEFAULT 0 CHECK (is_builtin IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS skill (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            is_builtin INTEGER NOT NULL DEFAULT 0 CHECK (is_builtin IN (0, 1)),
            is_global_url INTEGER NOT NULL DEFAULT 0 CHECK (is_global_url IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (group_id)
                REFERENCES skill_group(id)
                ON UPDATE CASCADE
                ON DELETE CASCADE,

            UNIQUE (group_id, name)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_skill_group_id
        ON skill(group_id)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_skill_name
        ON skill(name)
        """
    )
    connection.commit()
