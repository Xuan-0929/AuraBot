import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.agent.integration.sql import utils


class AgentSQLUtilsTests(unittest.TestCase):
    def test_runtime_sqlite_path_lives_next_to_utils_module(self):
        self.assertEqual(utils.SQLITE_PATH.name, "agent_runtime_sql.sqlite")
        self.assertEqual(utils.SQLITE_PATH.parent, Path(utils.__file__).parent)

    def test_get_connection_opens_runtime_sqlite_with_row_factory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sqlite_path = Path(tmpdir) / "agent_runtime_sql.sqlite"

            with patch.object(utils, "SQLITE_PATH", sqlite_path):
                connection = utils.get_connection()

                try:
                    self.assertIsInstance(connection, sqlite3.Connection)
                    self.assertTrue(sqlite_path.exists())
                    self.assertIs(connection.row_factory, sqlite3.Row)

                    connection.execute("create table runtime_context (value text)")
                    connection.execute(
                        "insert into runtime_context (value) values (?)",
                        ("hello",),
                    )
                    row = connection.execute(
                        "select value from runtime_context"
                    ).fetchone()

                    self.assertEqual(row["value"], "hello")
                finally:
                    connection.close()


if __name__ == "__main__":
    unittest.main()
