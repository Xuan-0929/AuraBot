"""用同步 sqlite3 持久化 Task、当前问题和可重放事件。

TaskStore 由单进程、单事件循环中的 TaskManager 独占；同步事务内没有 ``await``，状态、
CAS version、owner/idempotency 及事件直接在同一 ``BEGIN IMMEDIATE`` 中提交。初始化先以
PRAGMA 和 sqlite_master 验证完整的表、约束、外键和索引契约，才保留一个针对 unreleased
v1 数据的线性 v2 迁移；这让 Manager 只能读取可信的 Task 真相，无需数据库 worker
thread、async connection、write lock、repository 或 codec helper。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID, uuid4

from .models import (
    CreateTaskCommand,
    TERMINAL_TASK_STATUSES,
    TaskCommandError,
    TaskErrorCode,
    TaskEvent,
    TaskNotFound,
    TaskPage,
    TaskRecord,
    TaskStateConflict,
    TaskStatus,
    TaskVersionConflict,
    validate_status_transition,
)

SCHEMA_VERSION = 2
_Result = TypeVar("_Result")
_SqlToken = tuple[str, str]


@dataclass(frozen=True)
class _ColumnContract:
    """Expected PRAGMA table_info metadata for one Task database column."""

    affinity: str
    notnull: int
    default: str | None | tuple[str | None, ...]
    primary_key: int


@dataclass(frozen=True)
class _IndexContract:
    """Expected PRAGMA index metadata and ordered key columns for one index."""

    name: str | None
    columns: tuple[str, ...]
    unique: int
    partial: int
    origin: str
    predicate: tuple[_SqlToken, ...] | None = None


_TASK_COLUMNS = {
    "row_id": _ColumnContract("INTEGER", 0, None, 1),
    "id": _ColumnContract("TEXT", 1, None, 0),
    "title": _ColumnContract("TEXT", 1, None, 0),
    "prompt": _ColumnContract("TEXT", 1, None, 0),
    "working_directory": _ColumnContract("TEXT", 1, None, 0),
    "timeout_seconds": _ColumnContract("INTEGER", 1, None, 0),
    "rerun_of_task_id": _ColumnContract("TEXT", 0, None, 0),
    "status": _ColumnContract("TEXT", 1, None, 0),
    "active_runtime_ms": _ColumnContract("INTEGER", 1, "0", 0),
    "version": _ColumnContract("INTEGER", 1, None, 0),
    "question_id": _ColumnContract("TEXT", 0, None, 0),
    "question_text": _ColumnContract("TEXT", 0, None, 0),
    "question_options_json": _ColumnContract("TEXT", 0, None, 0),
    "question_answer": _ColumnContract("TEXT", 0, None, 0),
    "result_text": _ColumnContract("TEXT", 0, None, 0),
    "error_code": _ColumnContract("TEXT", 0, None, 0),
    "error_message": _ColumnContract("TEXT", 0, None, 0),
    "pause_reason": _ColumnContract("TEXT", 0, None, 0),
    "created_at": _ColumnContract("TEXT", 1, None, 0),
    "updated_at": _ColumnContract("TEXT", 1, None, 0),
    "started_at": _ColumnContract("TEXT", 0, None, 0),
    "finished_at": _ColumnContract("TEXT", 0, None, 0),
    "deleted_at": _ColumnContract("TEXT", 0, None, 0),
}
_EVENT_COLUMNS = {
    "event_id": _ColumnContract("INTEGER", 0, None, 1),
    "task_id": _ColumnContract("TEXT", 1, None, 0),
    "task_sequence": _ColumnContract("INTEGER", 1, None, 0),
    "event_type": _ColumnContract("TEXT", 1, None, 0),
    "occurred_at": _ColumnContract("TEXT", 1, None, 0),
    "payload_json": _ColumnContract("TEXT", 1, None, 0),
}
_V1_COLUMNS = {"tasks": _TASK_COLUMNS, "task_events": _EVENT_COLUMNS}
_V2_COLUMNS = {
    "tasks": {
        **_TASK_COLUMNS,
        "workspace_id": _ColumnContract("TEXT", 1, (None, "'legacy'"), 0),
        "owner_profile_id": _ColumnContract("TEXT", 1, (None, "'legacy'"), 0),
        "idempotency_key": _ColumnContract("TEXT", 1, (None, "''"), 0),
    },
    "task_events": _EVENT_COLUMNS,
}
_V1_INDEXES = {
    "tasks": (
        _IndexContract(None, ("id",), 1, 0, "u"),
        _IndexContract("tasks_queue_idx", ("status", "deleted_at", "row_id"), 0, 0, "c"),
    ),
    "task_events": (
        _IndexContract(None, ("task_id", "task_sequence"), 1, 0, "u"),
        _IndexContract("task_events_replay_idx", ("task_id", "event_id"), 0, 0, "c"),
    ),
}
_V2_INDEXES = {
    **_V1_INDEXES,
    "tasks": (
        *_V1_INDEXES["tasks"],
        _IndexContract(
            "tasks_owner_idempotency_idx",
            ("owner_profile_id", "idempotency_key"),
            1,
            1,
            "c",
            (("word", "idempotency_key"), ("symbol", "<>"), ("string", "")),
        ),
    ),
}
_FOREIGN_KEYS = {
    "tasks": frozenset({("rerun_of_task_id", "tasks", "id", "NO ACTION", "NO ACTION", "NONE")} ),
    "task_events": frozenset({("task_id", "tasks", "id", "NO ACTION", "NO ACTION", "NONE")} ),
}
_TABLE_SQL_REQUIREMENTS = {
    "tasks": (
        (("word", "row_id"), ("word", "integer"), ("word", "primary"),
         ("word", "key"), ("word", "autoincrement")),
        (("word", "check"), ("symbol", "("), ("word", "timeout_seconds"),
         ("symbol", ">"), ("word", "0"), ("symbol", ")")),
        (("word", "check"), ("symbol", "("), ("word", "status"),
         ("word", "in"), ("symbol", "("), ("string", "queued"), ("symbol", ","),
         ("string", "running"), ("symbol", ","), ("string", "waiting"),
         ("symbol", ","), ("string", "paused"), ("symbol", ","),
         ("string", "completed"), ("symbol", ","), ("string", "failed"),
         ("symbol", ","), ("string", "cancelled"), ("symbol", ")"), ("symbol", ")")),
        (("word", "check"), ("symbol", "("), ("word", "active_runtime_ms"),
         ("symbol", ">="), ("word", "0"), ("symbol", ")")),
        (("word", "check"), ("symbol", "("), ("word", "version"),
         ("symbol", ">="), ("word", "1"), ("symbol", ")")),
    ),
    "task_events": (
        (("word", "event_id"), ("word", "integer"), ("word", "primary"),
         ("word", "key"), ("word", "autoincrement")),
    ),
}


class TaskPersistenceError(RuntimeError):
    """SQLite 无法原子提交 Task 状态或事件。"""


class TaskStoreSchemaError(RuntimeError):
    """磁盘数据库不是当前两表模型，拒绝自动迁移。"""


def _now() -> datetime:
    return datetime.now(UTC)


def _dump(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def _load(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


class TaskStore:
    """单连接同步 SQLite Store；调用方必须从同一事件循环使用。"""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.resolve()
        self._connection: sqlite3.Connection | None = None

    def initialize(self) -> None:
        """创建新两表数据库，或验证现有数据库恰好是 schema v1。"""
        if self._connection is not None:
            return
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            for pragma in (
                "PRAGMA journal_mode = WAL",
                "PRAGMA foreign_keys = ON",
                "PRAGMA busy_timeout = 5000",
            ):
                connection.execute(pragma)
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
            }
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version == 0 and not tables:
                self._create_schema(connection)
            elif version == 1 and tables == {"tasks", "task_events"}:
                self._validate_schema(connection, _V1_COLUMNS, _V1_INDEXES, "legacy")
                self._migrate_v1_to_v2(connection)
            elif version == SCHEMA_VERSION and tables == {"tasks", "task_events"}:
                self._validate_schema(connection, _V2_COLUMNS, _V2_INDEXES, "current")
            else:
                raise TaskStoreSchemaError(
                    "Unsupported Task database schema "
                    f"(user_version={version}, tables={sorted(tables)}). Please back up "
                    "and delete the entire Task data directory: "
                    f"{self.database_path.parent}"
                )
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise TaskStoreSchemaError(
                    f"Task database has {len(violations)} foreign-key violation(s)."
                )
        except BaseException:
            connection.close()
            raise
        self._connection = connection

    def close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()

    def _create_schema(self, db: sqlite3.Connection) -> None:
        statuses = ",".join(f"'{status.value}'" for status in TaskStatus)
        statements = (
            f"""CREATE TABLE tasks (
                row_id INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL, prompt TEXT NOT NULL, working_directory TEXT NOT NULL,
                workspace_id TEXT NOT NULL, owner_profile_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                timeout_seconds INTEGER NOT NULL CHECK(timeout_seconds > 0),
                rerun_of_task_id TEXT REFERENCES tasks(id),
                status TEXT NOT NULL CHECK(status IN ({statuses})),
                active_runtime_ms INTEGER NOT NULL DEFAULT 0 CHECK(active_runtime_ms >= 0),
                version INTEGER NOT NULL CHECK(version >= 1), question_id TEXT,
                question_text TEXT, question_options_json TEXT, question_answer TEXT,
                result_text TEXT, error_code TEXT, error_message TEXT, pause_reason TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT,
                finished_at TEXT, deleted_at TEXT)""",
            "CREATE INDEX tasks_queue_idx ON tasks(status, deleted_at, row_id)",
            "CREATE UNIQUE INDEX tasks_owner_idempotency_idx ON tasks(owner_profile_id, idempotency_key) WHERE idempotency_key <> ''",
            """CREATE TABLE task_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL REFERENCES tasks(id), task_sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL, occurred_at TEXT NOT NULL, payload_json TEXT NOT NULL,
                UNIQUE(task_id, task_sequence))""",
            "CREATE INDEX task_events_replay_idx ON task_events(task_id, event_id)",
        )
        db.execute("BEGIN IMMEDIATE")
        try:
            for statement in statements:
                db.execute(statement)
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            db.commit()
        except BaseException:
            db.rollback()
            raise

    @staticmethod
    def _migrate_v1_to_v2(db: sqlite3.Connection) -> None:
        """Extend unreleased v1 Task rows without discarding active state or events."""

        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("ALTER TABLE tasks ADD COLUMN workspace_id TEXT NOT NULL DEFAULT 'legacy'")
            db.execute("ALTER TABLE tasks ADD COLUMN owner_profile_id TEXT NOT NULL DEFAULT 'legacy'")
            db.execute("ALTER TABLE tasks ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT ''")
            db.execute(
                "CREATE UNIQUE INDEX tasks_owner_idempotency_idx "
                "ON tasks(owner_profile_id, idempotency_key) WHERE idempotency_key <> ''"
            )
            TaskStore._validate_schema(db, _V2_COLUMNS, _V2_INDEXES, "migrated")
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            db.commit()
        except BaseException:
            db.rollback()
            raise

    @staticmethod
    def _validate_schema(
        db: sqlite3.Connection,
        expected: Mapping[str, Mapping[str, _ColumnContract]],
        expected_indexes: Mapping[str, tuple[_IndexContract, ...]],
        label: str,
    ) -> None:
        """Reject lookalike databases unless columns, constraints, keys, and indexes match."""

        for table, columns in expected.items():
            TaskStore._validate_columns(db, table, columns, label)
            TaskStore._validate_table_sql(db, table, label)
            TaskStore._validate_foreign_keys(db, table, label)
            TaskStore._validate_indexes(db, table, expected_indexes[table], label)

    @staticmethod
    def _validate_columns(
        db: sqlite3.Connection,
        table: str,
        expected: Mapping[str, _ColumnContract],
        label: str,
    ) -> None:
        """Compare every visible column's SQLite affinity, nullability, default, and PK slot."""

        actual = {
            row["name"]: _ColumnContract(
                TaskStore._sqlite_affinity(str(row["type"])),
                int(row["notnull"]),
                row["dflt_value"],
                int(row["pk"]),
            )
            for row in db.execute(f"PRAGMA table_info({TaskStore._quote_identifier(table)})")
        }
        if set(actual) != set(expected) or any(
            not TaskStore._column_matches(actual[name], contract)
            for name, contract in expected.items()
        ):
            raise TaskStoreSchemaError(
                f"Unsupported {label} Task database schema for {table}; "
                "column metadata (affinity, NOT NULL, default, primary key) does not match."
            )

    @staticmethod
    def _column_matches(actual: _ColumnContract, expected: _ColumnContract) -> bool:
        """Accept either fresh-v2 or migration-default metadata for newly introduced columns."""

        defaults = expected.default
        allowed_defaults = defaults if isinstance(defaults, tuple) else (defaults,)
        return (
            actual.affinity == expected.affinity
            and actual.notnull == expected.notnull
            and actual.default in allowed_defaults
            and actual.primary_key == expected.primary_key
        )

    @staticmethod
    def _validate_table_sql(db: sqlite3.Connection, table: str, label: str) -> None:
        """Require executable CHECK constraints and AUTOINCREMENT tokens unavailable from PRAGMA."""

        sql = TaskStore._master_sql(db, "table", table)
        required = _TABLE_SQL_REQUIREMENTS[table]
        tokens = TaskStore._structural_sql_tokens(sql) if sql is not None else ()
        if any(not TaskStore._contains_tokens(tokens, fragment) for fragment in required):
            raise TaskStoreSchemaError(
                f"Unsupported {label} Task database schema for {table}; "
                "required CHECK or primary-key constraint is missing."
            )

    @staticmethod
    def _validate_foreign_keys(db: sqlite3.Connection, table: str, label: str) -> None:
        """Require the Task self-reference and event-to-task reference with default actions."""

        actual = frozenset(
            (
                row["from"], row["table"], row["to"], row["on_update"],
                row["on_delete"], row["match"],
            )
            for row in db.execute(f"PRAGMA foreign_key_list({TaskStore._quote_identifier(table)})")
        )
        if actual != _FOREIGN_KEYS[table]:
            raise TaskStoreSchemaError(
                f"Unsupported {label} Task database schema for {table}; foreign keys do not match."
            )

    @staticmethod
    def _validate_indexes(
        db: sqlite3.Connection,
        table: str,
        expected: tuple[_IndexContract, ...],
        label: str,
    ) -> None:
        """Check index names, origin, uniqueness, partial status, ordered keys, and predicate."""

        actual = [
            _IndexContract(
                row["name"],
                tuple(
                    index_row["name"]
                    for index_row in sorted(
                        db.execute(
                            f"PRAGMA index_xinfo({TaskStore._quote_identifier(row['name'])})"
                        ),
                        key=lambda index_row: int(index_row["seqno"]),
                    )
                    if int(index_row["key"])
                ),
                int(row["unique"]),
                int(row["partial"]),
                str(row["origin"]),
                TaskStore._index_predicate(db, row["name"]),
            )
            for row in db.execute(f"PRAGMA index_list({TaskStore._quote_identifier(table)})")
        ]
        unmatched = list(actual)
        for contract in expected:
            if contract.name is None:
                matches = [
                    index
                    for index in unmatched
                    if index.name.startswith("sqlite_autoindex")
                    and index.columns == contract.columns
                    and index.unique == contract.unique
                    and index.partial == contract.partial
                    and index.origin == contract.origin
                ]
            else:
                matches = [
                    index
                    for index in unmatched
                    if index.name == contract.name
                    if index.columns == contract.columns
                    and index.unique == contract.unique
                    and index.partial == contract.partial
                    and index.origin == contract.origin
                    and (contract.predicate is None or index.predicate == contract.predicate)
                ]
            if len(matches) != 1:
                raise TaskStoreSchemaError(
                    f"Unsupported {label} Task database schema for {table}; "
                    "index name, uniqueness, partial predicate, origin, or key order does not match."
                )
            unmatched.remove(matches[0])
        if unmatched:
            raise TaskStoreSchemaError(
                f"Unsupported {label} Task database schema for {table}; unexpected index definitions."
            )

    @staticmethod
    def _master_sql(db: sqlite3.Connection, kind: str, name: str) -> str | None:
        """Return raw sqlite_master DDL so tokenization can distinguish SQL from quoted content."""

        row = db.execute(
            "SELECT sql FROM sqlite_master WHERE type = ? AND name = ?", (kind, name)
        ).fetchone()
        return str(row["sql"]) if row and row["sql"] else None

    @staticmethod
    def _structural_sql_tokens(sql: str) -> tuple[_SqlToken, ...]:
        """Tokenize SQLite DDL without comments, never treating quoted text as SQL syntax."""

        tokens: list[_SqlToken] = []
        index = 0
        while index < len(sql):
            character = sql[index]
            following = sql[index + 1] if index + 1 < len(sql) else ""
            if character.isspace():
                index += 1
            elif character == "-" and following == "-":
                newline = sql.find("\n", index + 2)
                index = len(sql) if newline < 0 else newline + 1
            elif character == "/" and following == "*":
                end = sql.find("*/", index + 2)
                index = len(sql) if end < 0 else end + 2
            elif character in ("'", '"', "`", "["):
                quote = "]" if character == "[" else character
                kind = "string" if character == "'" else "identifier"
                value, index = TaskStore._quoted_token(sql, index + 1, quote)
                tokens.append((kind, value))
            elif character.isalnum() or character == "_":
                end = index + 1
                while end < len(sql) and (sql[end].isalnum() or sql[end] == "_"):
                    end += 1
                tokens.append(("word", sql[index:end].lower()))
                index = end
            else:
                symbol = character + following
                if symbol in ("<=", ">=", "<>", "!=", "==", "||", "<<", ">>", "->"):
                    tokens.append(("symbol", symbol))
                    index += 2
                else:
                    tokens.append(("symbol", character))
                    index += 1
        return tuple(tokens)

    @staticmethod
    def _quoted_token(sql: str, index: int, quote: str) -> tuple[str, int]:
        """Read one SQLite quoted literal or identifier, decoding doubled quote escapes."""

        value: list[str] = []
        while index < len(sql):
            character = sql[index]
            following = sql[index + 1] if index + 1 < len(sql) else ""
            if character == quote:
                if quote != "]" and following == quote:
                    value.append(quote)
                    index += 2
                else:
                    return "".join(value), index + 1
            else:
                value.append(character)
                index += 1
        return "".join(value), index

    @staticmethod
    def _contains_tokens(tokens: tuple[_SqlToken, ...], expected: tuple[_SqlToken, ...]) -> bool:
        """Match complete token sequences so quoted literals cannot forge schema expressions."""

        length = len(expected)
        return any(tokens[index:index + length] == expected for index in range(len(tokens) - length + 1))

    @staticmethod
    def _comment_free_normalized_sql(sql: str) -> str:
        """Strip SQLite comments and outside whitespace without changing quoted DDL content."""

        normalized: list[str] = []
        quote: str | None = None
        index = 0
        while index < len(sql):
            character = sql[index]
            following = sql[index + 1] if index + 1 < len(sql) else ""
            if quote is None:
                if character == "-" and following == "-":
                    index = sql.find("\n", index + 2)
                    if index < 0:
                        break
                    continue
                if character == "/" and following == "*":
                    end = sql.find("*/", index + 2)
                    if end < 0:
                        break
                    index = end + 2
                    continue
                if character in ("'", '"', "`", "["):
                    quote = "]" if character == "[" else character
                    normalized.append(character)
                elif not character.isspace():
                    normalized.append(character.lower())
            else:
                normalized.append(character)
                if character == quote:
                    if quote != "]" and following == quote:
                        normalized.append(following)
                        index += 1
                    else:
                        quote = None
            index += 1
        return "".join(normalized)

    @staticmethod
    def _index_predicate(db: sqlite3.Connection, name: str) -> tuple[_SqlToken, ...] | None:
        """Extract a tokenized WHERE suffix so only an executable empty-string predicate matches."""

        sql = TaskStore._master_sql(db, "index", name)
        if sql is None:
            return None
        tokens = TaskStore._structural_sql_tokens(sql)
        try:
            where_index = tokens.index(("word", "where"))
        except ValueError:
            return None
        return tokens[where_index + 1:]

    @staticmethod
    def _sqlite_affinity(declared_type: str) -> str:
        """Map SQLite declared types to the five documented type affinities."""

        normalized = declared_type.upper()
        if "INT" in normalized:
            return "INTEGER"
        if any(token in normalized for token in ("CHAR", "CLOB", "TEXT")):
            return "TEXT"
        if "BLOB" in normalized or not normalized:
            return "BLOB"
        if any(token in normalized for token in ("REAL", "FLOA", "DOUB")):
            return "REAL"
        return "NUMERIC"

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        """Quote static schema names before using them in SQLite PRAGMA identifier syntax."""

        return '"' + identifier.replace('"', '""') + '"'

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("TaskStore is not initialized.")
        return self._connection

    def _write(self, operation: Callable[[sqlite3.Connection], _Result]) -> _Result:
        db = self._db()
        try:
            db.execute("BEGIN IMMEDIATE")
            result = operation(db)
            db.commit()
            return result
        except BaseException as error:
            db.rollback()
            if isinstance(error, sqlite3.Error):
                raise TaskPersistenceError(
                    "The Task database rejected a state or event write."
                ) from error
            raise

    def _row(self, task_id: UUID, include_deleted: bool = False) -> sqlite3.Row:
        clause = "" if include_deleted else " AND deleted_at IS NULL"
        row = self._db().execute(
            f"SELECT * FROM tasks WHERE id = ?{clause}", (str(task_id),)
        ).fetchone()
        if row is None:
            raise TaskNotFound(f"Task not found: {task_id}")
        return row

    def _cas(self, task_id: UUID, expected_version: int) -> sqlite3.Row:
        row = self._row(task_id)
        actual = int(row["version"])
        if actual != expected_version:
            raise TaskVersionConflict(expected_version, actual)
        return row

    @staticmethod
    def _task(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            id=UUID(row["id"]), row_id=int(row["row_id"]), title=row["title"],
            prompt=row["prompt"], working_directory=Path(row["working_directory"]),
            workspace_id=row["workspace_id"], owner_profile_id=row["owner_profile_id"],
            idempotency_key=row["idempotency_key"],
            status=TaskStatus(row["status"]), timeout_seconds=int(row["timeout_seconds"]),
            active_runtime_ms=int(row["active_runtime_ms"]),
            question_id=UUID(row["question_id"]) if row["question_id"] else None,
            question_text=row["question_text"],
            question_options=tuple(json.loads(row["question_options_json"] or "[]")),
            question_answer=row["question_answer"], result_text=row["result_text"],
            error_code=TaskErrorCode(row["error_code"]) if row["error_code"] else None,
            error_message=row["error_message"], pause_reason=row["pause_reason"],
            rerun_of_task_id=UUID(row["rerun_of_task_id"]) if row["rerun_of_task_id"] else None,
            version=int(row["version"]), created_at=_load(row["created_at"]),
            updated_at=_load(row["updated_at"]), started_at=_load(row["started_at"]),
            finished_at=_load(row["finished_at"]), deleted_at=_load(row["deleted_at"]),
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> TaskEvent:
        return TaskEvent(
            event_id=int(row["event_id"]), task_sequence=int(row["task_sequence"]),
            task_id=UUID(row["task_id"]), type=row["event_type"],
            occurred_at=_load(row["occurred_at"]), payload=json.loads(row["payload_json"]),
        )

    def _event(
        self, db: sqlite3.Connection, task_id: UUID, event_type: str,
        occurred_at: datetime, payload: Mapping[str, Any],
    ) -> TaskEvent:
        sequence = int(db.execute(
            "SELECT COALESCE(MAX(task_sequence), 0) + 1 FROM task_events WHERE task_id = ?",
            (str(task_id),),
        ).fetchone()[0])
        cursor = db.execute(
            "INSERT INTO task_events(task_id, task_sequence, event_type, occurred_at, "
            "payload_json) VALUES (?, ?, ?, ?, ?)",
            (str(task_id), sequence, event_type, _dump(occurred_at),
             json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
        )
        return TaskEvent(
            event_id=int(cursor.lastrowid), task_sequence=sequence, task_id=task_id,
            type=event_type, occurred_at=occurred_at, payload=dict(payload),
        )

    def create_task(self, command: CreateTaskCommand, working_directory: Path) -> TaskRecord:
        timeout = command.timeout_seconds
        if isinstance(timeout, bool) or timeout is None or not 1 <= timeout <= 86_400:
            raise TaskCommandError("timeout_seconds must be an integer from 1 to 86400.")

        def operation(db: sqlite3.Connection) -> TaskRecord:
            existing = db.execute(
                "SELECT * FROM tasks WHERE owner_profile_id=? AND idempotency_key=?",
                (command.owner_profile_id, command.idempotency_key),
            ).fetchone()
            if existing is not None:
                return self._task(existing)
            if command.rerun_of_task_id is not None:
                source = self._row(command.rerun_of_task_id)
                if TaskStatus(source["status"]) not in TERMINAL_TASK_STATUSES:
                    raise TaskStateConflict(TaskStatus(source["status"]), TaskStatus.QUEUED)
            task_id, occurred_at = uuid4(), _now()
            db.execute(
                """INSERT INTO tasks(
                    id,title,prompt,working_directory,workspace_id,owner_profile_id,idempotency_key,
                    timeout_seconds,rerun_of_task_id,
                    status,active_runtime_ms,version,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,0,1,?,?)""",
                (str(task_id), command.title, command.prompt, str(working_directory),
                 command.workspace_id, command.owner_profile_id, command.idempotency_key,
                 timeout, str(command.rerun_of_task_id) if command.rerun_of_task_id else None,
                 TaskStatus.QUEUED.value, _dump(occurred_at), _dump(occurred_at)),
            )
            self._event(db, task_id, "task.created", occurred_at,
                        {"status": TaskStatus.QUEUED.value})
            return self._task(self._row(task_id, True))
        return self._write(operation)

    def get_task(self, task_id: UUID, include_deleted: bool = False) -> TaskRecord:
        return self._task(self._row(task_id, include_deleted))

    def list_tasks(
        self, *, after_row_id: int | None = None, limit: int = 50,
        statuses: Iterable[TaskStatus] | None = None,
        owner_profile_id: str | None = None,
    ) -> TaskPage:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        where, parameters = ["deleted_at IS NULL", "row_id > ?"], [after_row_id or 0]
        if owner_profile_id is not None:
            where.append("owner_profile_id = ?")
            parameters.append(owner_profile_id)
        values = tuple(status.value for status in (statuses or ()))
        if values:
            where.append(f"status IN ({','.join('?' for _ in values)})")
            parameters.extend(values)
        rows = self._db().execute(
            f"SELECT * FROM tasks WHERE {' AND '.join(where)} ORDER BY row_id LIMIT ?",
            [*parameters, limit + 1],
        ).fetchall()
        visible = rows[:limit]
        return TaskPage(tuple(self._task(row) for row in visible),
                        int(visible[-1]["row_id"]) if len(rows) > limit else None)

    def claim_queued(self, limit: int) -> tuple[TaskRecord, ...]:
        """按 row_id 原子领取最老 queued Task。"""
        if limit <= 0:
            return ()

        def operation(db: sqlite3.Connection) -> tuple[TaskRecord, ...]:
            rows = db.execute(
                "SELECT * FROM tasks WHERE status=? AND deleted_at IS NULL "
                "ORDER BY row_id LIMIT ?", (TaskStatus.QUEUED.value, limit),
            ).fetchall()
            claimed = []
            for row in rows:
                occurred_at, task_id = _now(), UUID(row["id"])
                db.execute(
                    """UPDATE tasks SET status=?,version=version+1,updated_at=?,
                        started_at=COALESCE(started_at,?),error_code=NULL,
                        error_message=NULL,pause_reason=NULL
                        WHERE id=? AND version=? AND status=?""",
                    (TaskStatus.RUNNING.value, _dump(occurred_at), _dump(occurred_at),
                     row["id"], row["version"], TaskStatus.QUEUED.value),
                )
                self._event(db, task_id, "task.status_changed", occurred_at,
                            {"from": "queued", "to": "running"})
                claimed.append(self._task(self._row(task_id)))
            return tuple(claimed)
        return self._write(operation)

    def transition_task(
        self, task_id: UUID, target_status: TaskStatus, *, expected_version: int,
        active_runtime_ms: int | None = None, result_text: str | None = None,
        error_code: TaskErrorCode | None = None, error_message: str | None = None,
        pause_reason: str | None = None,
    ) -> tuple[TaskRecord, TaskEvent]:
        def operation(db: sqlite3.Connection) -> tuple[TaskRecord, TaskEvent]:
            row = self._cas(task_id, expected_version)
            current = TaskStatus(row["status"])
            validate_status_transition(current, target_status)
            runtime = int(row["active_runtime_ms"]) if active_runtime_ms is None else active_runtime_ms
            if runtime < 0:
                raise TaskCommandError("active_runtime_ms cannot be negative.")
            occurred_at = _now()
            started_at = row["started_at"] or (
                _dump(occurred_at) if target_status == TaskStatus.RUNNING else None)
            finished_at = (_dump(occurred_at) if target_status in TERMINAL_TASK_STATUSES
                           else row["finished_at"])
            db.execute(
                """UPDATE tasks SET status=?,active_runtime_ms=?,result_text=?,error_code=?,
                    error_message=?,pause_reason=?,version=version+1,updated_at=?,
                    started_at=?,finished_at=? WHERE id=? AND version=? AND deleted_at IS NULL""",
                (target_status.value, runtime, result_text,
                 error_code.value if error_code else None, error_message, pause_reason,
                 _dump(occurred_at), started_at, finished_at, str(task_id), expected_version),
            )
            event = self._event(db, task_id, "task.status_changed", occurred_at,
                                {"from": current.value, "to": target_status.value})
            return self._task(self._row(task_id)), event
        return self._write(operation)

    def append_output(self, task_id: UUID, text: str) -> TaskEvent:
        def operation(db: sqlite3.Connection) -> TaskEvent:
            self._row(task_id)
            return self._event(db, task_id, "task.output_appended", _now(), {"text": text})
        return self._write(operation)

    def raise_question(
        self, task_id: UUID, question: str, options: tuple[str, ...], *,
        expected_version: int, active_runtime_ms: int | None = None,
    ) -> tuple[TaskRecord, TaskEvent]:
        def operation(db: sqlite3.Connection) -> tuple[TaskRecord, TaskEvent]:
            row = self._cas(task_id, expected_version)
            current = TaskStatus(row["status"])
            validate_status_transition(current, TaskStatus.WAITING)
            question_id, occurred_at = uuid4(), _now()
            runtime = int(row["active_runtime_ms"]) if active_runtime_ms is None else active_runtime_ms
            db.execute(
                """UPDATE tasks SET status=?,active_runtime_ms=?,version=version+1,
                    question_id=?,question_text=?,question_options_json=?,question_answer=NULL,
                    updated_at=? WHERE id=? AND version=?""",
                (TaskStatus.WAITING.value, runtime, str(question_id), question,
                 json.dumps(options, ensure_ascii=False, separators=(",", ":")),
                 _dump(occurred_at), str(task_id), expected_version),
            )
            event = self._event(
                db, task_id, "task.question_raised", occurred_at,
                {"questionId": str(question_id), "question": question, "options": list(options)},
            )
            return self._task(self._row(task_id)), event
        return self._write(operation)

    def answer_question(
        self, task_id: UUID, question_id: UUID, answer: str, *, expected_version: int,
    ) -> tuple[TaskRecord, TaskEvent]:
        """持久化答案，并在同一事务把 waiting Task 放回 queued。"""
        def operation(db: sqlite3.Connection) -> tuple[TaskRecord, TaskEvent]:
            row = self._cas(task_id, expected_version)
            current = TaskStatus(row["status"])
            validate_status_transition(current, TaskStatus.QUEUED)
            if row["question_id"] != str(question_id) or row["question_answer"] is not None:
                raise TaskNotFound(f"Pending Task question not found: {question_id}")
            occurred_at = _now()
            db.execute(
                "UPDATE tasks SET status=?,question_answer=?,version=version+1,updated_at=? "
                "WHERE id=? AND version=?",
                (TaskStatus.QUEUED.value, answer, _dump(occurred_at),
                 str(task_id), expected_version),
            )
            event = self._event(db, task_id, "task.question_answered", occurred_at,
                                {"questionId": str(question_id), "answer": answer})
            return self._task(self._row(task_id)), event
        return self._write(operation)

    def clear_question(self, task_id: UUID, *, expected_version: int) -> TaskRecord:
        """continuation prompt 被接受后清除当前问题和答案。"""
        def operation(db: sqlite3.Connection) -> TaskRecord:
            row = self._cas(task_id, expected_version)
            if row["question_id"] is None:
                return self._task(row)
            if TaskStatus(row["status"]) != TaskStatus.RUNNING:
                raise TaskStateConflict(TaskStatus(row["status"]), TaskStatus.RUNNING)
            db.execute(
                """UPDATE tasks SET question_id=NULL,question_text=NULL,
                    question_options_json=NULL,question_answer=NULL,version=version+1,
                    updated_at=? WHERE id=? AND version=?""",
                (_dump(_now()), str(task_id), expected_version),
            )
            return self._task(self._row(task_id))
        return self._write(operation)

    def tombstone_task(self, task_id: UUID) -> tuple[TaskRecord, TaskEvent]:
        task = self.get_task(task_id, include_deleted=True)
        if task.deleted_at is not None:
            row = self._db().execute(
                "SELECT * FROM task_events WHERE task_id=? AND event_type='task.deleted' "
                "ORDER BY event_id DESC LIMIT 1", (str(task_id),),
            ).fetchone()
            if row is None:
                raise TaskPersistenceError("Deleted Task has no deletion event.")
            return task, self._event_from_row(row)
        if task.status not in TERMINAL_TASK_STATUSES:
            raise TaskStateConflict(task.status, TaskStatus.CANCELLED)

        def operation(db: sqlite3.Connection) -> tuple[TaskRecord, TaskEvent]:
            occurred_at = _now()
            db.execute(
                "UPDATE tasks SET deleted_at=?,updated_at=?,version=version+1 "
                "WHERE id=? AND deleted_at IS NULL",
                (_dump(occurred_at), _dump(occurred_at), str(task_id)),
            )
            event = self._event(db, task_id, "task.deleted", occurred_at,
                                {"status": task.status.value})
            return self.get_task(task_id, include_deleted=True), event
        return self._write(operation)

    def events_after(
        self, event_id: int, task_id: UUID | None, limit: int,
    ) -> list[TaskEvent]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        where, parameters = "event_id > ?", [event_id]
        if task_id is not None:
            where += " AND task_id = ?"
            parameters.append(str(task_id))
        rows = self._db().execute(
            f"SELECT * FROM task_events WHERE {where} ORDER BY event_id LIMIT ?",
            [*parameters, limit],
        ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def recoverable_tasks(self) -> list[TaskRecord]:
        terminal = tuple(status.value for status in TERMINAL_TASK_STATUSES)
        rows = self._db().execute(
            f"SELECT * FROM tasks WHERE deleted_at IS NULL AND status NOT IN "
            f"({','.join('?' for _ in terminal)}) ORDER BY row_id", terminal,
        ).fetchall()
        return [self._task(row) for row in rows]

    def deleted_task_ids(self) -> tuple[UUID, ...]:
        rows = self._db().execute(
            "SELECT id FROM tasks WHERE deleted_at IS NOT NULL ORDER BY row_id"
        ).fetchall()
        return tuple(UUID(row["id"]) for row in rows)


__all__ = ["SCHEMA_VERSION", "TaskPersistenceError", "TaskStore", "TaskStoreSchemaError"]
