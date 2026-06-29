import sqlite3
from pathlib import Path

SQLITE_PATH = Path(__file__).with_name("agent_runtime_sql.sqlite")


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(SQLITE_PATH)
    connection.row_factory = sqlite3.Row
    return connection
