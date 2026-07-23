import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from backend.agent.integration.sql import utils
from scripts.AgentSQLiteInitialize import (
    DEFAULT_DATABASE_PATH,
    initialize_database,
)


class AgentSQLiteInitializeTests(unittest.TestCase):
    def test_default_path_matches_agent_sql_runtime(self):
        self.assertEqual(DEFAULT_DATABASE_PATH, utils.SQLITE_PATH)

    def test_creates_parent_directory_tables_and_indexes(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "nested" / "agent.sqlite"

            result = initialize_database(database_path)

            self.assertEqual(result, database_path.resolve())
            self.assertTrue(database_path.is_file())

            with closing(sqlite3.connect(database_path)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE type = 'table'
                        """
                    )
                }
                indexes = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE type = 'index'
                        """
                    )
                }

            self.assertTrue(
                {
                    "skill_group",
                    "skill",
                    "mcp_server",
                    "adaptor",
                    "tool_group",
                    "tool",
                }
                <= tables
            )
            self.assertTrue(
                {
                    "idx_skill_group_id",
                    "idx_skill_name",
                    "idx_tool_group_server_id",
                    "idx_tool_server_id",
                    "idx_tool_group_id",
                    "idx_tool_adaptor_id",
                }
                <= indexes
            )

    def test_repeated_initialization_preserves_existing_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "agent.sqlite"
            initialize_database(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute(
                    """
                    INSERT INTO skill_group (name, description, is_builtin)
                    VALUES ('core', 'Core skills', 1)
                    """
                )
                connection.execute(
                    """
                    INSERT INTO mcp_server (name, url, description)
                    VALUES ('native', 'http://127.0.0.1:6402/mcp', 'Native MCP')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO tool_group (
                        server_id,
                        name,
                        description,
                        is_native
                    )
                    VALUES (1, 'filesystem', 'Filesystem tools', 1)
                    """
                )
                connection.execute(
                    """
                    INSERT INTO tool (
                        server_id,
                        group_id,
                        name,
                        description,
                        input_schema_json
                    )
                    VALUES (
                        1,
                        1,
                        'read_file',
                        'Read a file',
                        '{"type":"object"}'
                    )
                    """
                )
                connection.commit()

            initialize_database(database_path)
            initialize_database(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                counts = {
                    table: connection.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0]
                    for table in (
                        "skill_group",
                        "mcp_server",
                        "tool_group",
                        "tool",
                    )
                }

            self.assertEqual(
                counts,
                {
                    "skill_group": 1,
                    "mcp_server": 1,
                    "tool_group": 1,
                    "tool": 1,
                },
            )

    def test_existing_skill_database_gets_missing_tool_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "agent.sqlite"

            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    """
                    CREATE TABLE skill_group (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL UNIQUE,
                        description TEXT,
                        is_builtin INTEGER NOT NULL DEFAULT 0
                            CHECK (is_builtin IN (0, 1)),
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE skill (
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
                    """
                )
                connection.execute(
                    """
                    INSERT INTO skill_group (name, description, is_builtin)
                    VALUES ('existing', 'Existing skills', 1)
                    """
                )
                connection.commit()

            initialize_database(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                existing_group = connection.execute(
                    """
                    SELECT name, description, is_builtin
                    FROM skill_group
                    WHERE name = 'existing'
                    """
                ).fetchone()
                tool_tables = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE type = 'table'
                          AND name IN (
                              'mcp_server',
                              'adaptor',
                              'tool_group',
                              'tool'
                          )
                        """
                    )
                }

            self.assertEqual(
                existing_group,
                ("existing", "Existing skills", 1),
            )
            self.assertEqual(
                tool_tables,
                {"mcp_server", "adaptor", "tool_group", "tool"},
            )

    def test_legacy_tool_table_gets_adaptor_column_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "legacy.sqlite"

            with closing(sqlite3.connect(database_path)) as connection:
                connection.executescript(
                    """
                    PRAGMA foreign_keys = ON;

                    CREATE TABLE mcp_server (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL UNIQUE,
                        url TEXT NOT NULL UNIQUE,
                        description TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );

                    CREATE TABLE tool_group (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        server_id INTEGER NOT NULL,
                        name TEXT NOT NULL UNIQUE,
                        description TEXT,
                        is_native INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (server_id)
                            REFERENCES mcp_server(id)
                            ON UPDATE CASCADE
                            ON DELETE RESTRICT,
                        UNIQUE (server_id, id)
                    );

                    CREATE TABLE tool (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        server_id INTEGER NOT NULL,
                        group_id INTEGER NOT NULL,
                        name TEXT NOT NULL,
                        description TEXT NOT NULL,
                        input_schema_json TEXT NOT NULL,
                        output_schema_json TEXT,
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
                    );

                    INSERT INTO mcp_server (name, url)
                    VALUES ('native', 'localhost:6410');

                    INSERT INTO tool_group (
                        server_id,
                        name,
                        description,
                        is_native
                    )
                    VALUES (1, 'filesystem', 'Filesystem tools', 1);

                    INSERT INTO tool (
                        server_id,
                        group_id,
                        name,
                        description,
                        input_schema_json
                    )
                    VALUES (
                        1,
                        1,
                        'read_file',
                        'Read a file',
                        '{"type":"object"}'
                    );
                    """
                )

            initialize_database(database_path)
            initialize_database(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(tool)"
                    )
                }
                foreign_keys = {
                    (row[2], row[3], row[4])
                    for row in connection.execute(
                        "PRAGMA foreign_key_list(tool)"
                    )
                }
                indexes = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA index_list(tool)"
                    )
                }
                tool_row = connection.execute(
                    "SELECT name, adaptor_id FROM tool"
                ).fetchone()

            self.assertIn("adaptor_id", columns)
            self.assertIn(
                ("adaptor", "adaptor_id", "id"),
                foreign_keys,
            )
            self.assertIn("idx_tool_adaptor_id", indexes)
            self.assertEqual(tool_row, ("read_file", None))

    def test_schema_failure_rolls_back_all_new_adaptor_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "failed.sqlite"

            with patch.object(
                utils,
                "TOOL_ADAPTOR_INDEX_STATEMENT",
                "CREATE INDEX broken_adaptor_index ON tool(missing_column)",
            ):
                with self.assertRaises(sqlite3.OperationalError):
                    initialize_database(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE type = 'table'
                          AND name IN ('adaptor', 'tool')
                        """
                    )
                }

            self.assertEqual(tables, set())

    def test_tool_and_group_must_reference_the_same_server(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "agent.sqlite"
            initialize_database(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute(
                    "INSERT INTO mcp_server (name, url) VALUES (?, ?)",
                    ("first", "https://first.example/mcp"),
                )
                connection.execute(
                    "INSERT INTO mcp_server (name, url) VALUES (?, ?)",
                    ("second", "https://second.example/mcp"),
                )
                connection.execute(
                    """
                    INSERT INTO tool_group (server_id, name, is_native)
                    VALUES (2, 'second-tools', 0)
                    """
                )

                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO tool (
                            server_id,
                            group_id,
                            name,
                            description,
                            input_schema_json
                        )
                        VALUES (1, 1, 'search', 'Search', '{"type":"object"}')
                        """
                    )


if __name__ == "__main__":
    unittest.main()
