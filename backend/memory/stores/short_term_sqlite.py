from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ..short_term.models import (
    BufferStats,
    ShortTermBreakpoint,
    ShortTermMessage,
)


SCHEMA_VERSION = 1

_SCHEMA_STATEMENTS = (
    """
    create table if not exists short_term_schema_info (
      key text primary key,
      value text not null,
      updated_at text not null
    )
    """,
    """
    create table if not exists short_term_messages (
      sequence integer primary key autoincrement,
      id text not null unique,
      profile_id text not null,
      source_type text not null,
      role text not null,
      content text not null,
      source_id text,
      created_at text not null
    )
    """,
    """
    create table if not exists short_term_breakpoints (
      id text primary key,
      profile_id text not null,
      source_type text not null,
      message_sequence integer not null,
      topic text,
      reason text,
      created_at text not null,
      unique(profile_id, source_type, message_sequence)
    )
    """,
    """
    create table if not exists short_term_segments (
      id text primary key,
      profile_id text not null,
      source_type text not null,
      breakpoint_id text not null unique,
      state text not null check (
        state in ('pending', 'processing', 'completed', 'failed')
      ),
      payload_json text not null check (json_valid(payload_json)),
      result_json text not null default '{}' check (json_valid(result_json)),
      attempt_count integer not null default 0 check (attempt_count >= 0),
      last_error text,
      lease_owner text,
      lease_expires_at text,
      created_at text not null,
      updated_at text not null,
      completed_at text
    )
    """,
    """
    create index if not exists idx_short_term_messages_profile_source_sequence
      on short_term_messages(profile_id, source_type, sequence)
    """,
    """
    create index if not exists idx_short_term_breakpoints_profile_source_sequence
      on short_term_breakpoints(profile_id, source_type, message_sequence)
    """,
    """
    create index if not exists idx_short_term_segments_profile_source_state_created
      on short_term_segments(profile_id, source_type, state, created_at)
    """,
    """
    create index if not exists idx_short_term_segments_profile_source_state_lease
      on short_term_segments(profile_id, source_type, state, lease_expires_at)
    """,
)

@dataclass(frozen=True)
class _ColumnSpec:
    declared_type: str
    required_not_null: bool
    primary_key_position: int = 0
    default: str | None = None


@dataclass(frozen=True)
class _IndexSpec:
    table_name: str
    columns: tuple[str, ...]


@dataclass(frozen=True)
class _IndexMetadata:
    columns: tuple[str, ...]
    unique: bool
    origin: str
    partial: bool


_TEXT_REQUIRED = _ColumnSpec("TEXT", True)
_TEXT_OPTIONAL = _ColumnSpec("TEXT", False)
_INTEGER_REQUIRED = _ColumnSpec("INTEGER", True)
_TEXT_PRIMARY_KEY = _ColumnSpec("TEXT", True, primary_key_position=1)
_INTEGER_PRIMARY_KEY = _ColumnSpec("INTEGER", True, primary_key_position=1)

_V1_TABLE_COLUMNS = {
    "short_term_schema_info": {
        "key": _TEXT_PRIMARY_KEY,
        "value": _TEXT_REQUIRED,
        "updated_at": _TEXT_REQUIRED,
    },
    "short_term_messages": {
        "sequence": _INTEGER_PRIMARY_KEY,
        "id": _TEXT_REQUIRED,
        "profile_id": _TEXT_REQUIRED,
        "source_type": _TEXT_REQUIRED,
        "role": _TEXT_REQUIRED,
        "content": _TEXT_REQUIRED,
        "source_id": _TEXT_OPTIONAL,
        "created_at": _TEXT_REQUIRED,
    },
    "short_term_breakpoints": {
        "id": _TEXT_PRIMARY_KEY,
        "profile_id": _TEXT_REQUIRED,
        "source_type": _TEXT_REQUIRED,
        "message_sequence": _INTEGER_REQUIRED,
        "topic": _TEXT_OPTIONAL,
        "reason": _TEXT_OPTIONAL,
        "created_at": _TEXT_REQUIRED,
    },
    "short_term_segments": {
        "id": _TEXT_PRIMARY_KEY,
        "profile_id": _TEXT_REQUIRED,
        "source_type": _TEXT_REQUIRED,
        "breakpoint_id": _TEXT_REQUIRED,
        "state": _TEXT_REQUIRED,
        "payload_json": _TEXT_REQUIRED,
        "result_json": _ColumnSpec("TEXT", True, default="'{}'"),
        "attempt_count": _ColumnSpec("INTEGER", True, default="0"),
        "last_error": _TEXT_OPTIONAL,
        "lease_owner": _TEXT_OPTIONAL,
        "lease_expires_at": _TEXT_OPTIONAL,
        "created_at": _TEXT_REQUIRED,
        "updated_at": _TEXT_REQUIRED,
        "completed_at": _TEXT_OPTIONAL,
    },
}

_V1_REQUIRED_UNIQUE_COLUMNS = {
    "short_term_messages": (("id",),),
    "short_term_breakpoints": (
        ("profile_id", "source_type", "message_sequence"),
    ),
    "short_term_segments": (("breakpoint_id",),),
}

_V1_EXPLICIT_INDEXES = {
    "idx_short_term_messages_profile_source_sequence": _IndexSpec(
        "short_term_messages",
        ("profile_id", "source_type", "sequence"),
    ),
    "idx_short_term_breakpoints_profile_source_sequence": _IndexSpec(
        "short_term_breakpoints",
        ("profile_id", "source_type", "message_sequence"),
    ),
    "idx_short_term_segments_profile_source_state_created": _IndexSpec(
        "short_term_segments",
        ("profile_id", "source_type", "state", "created_at"),
    ),
    "idx_short_term_segments_profile_source_state_lease": _IndexSpec(
        "short_term_segments",
        ("profile_id", "source_type", "state", "lease_expires_at"),
    ),
}

_V1_REQUIRED_CHECKS = {
    "short_term_segments": (
        "check(statein('pending','processing','completed','failed'))",
        "check(json_valid(payload_json))",
        "check(json_valid(result_json))",
        "check(attempt_count>=0)",
    ),
}


class SQLiteShortTermStore:
    def __init__(self, path: str | Path):
        if str(path) == ":memory:":
            self.path: str | Path = ":memory:"
        else:
            self.path = Path(path)
            self.path.parent.mkdir(parents=True, exist_ok=True)

        self._connection: sqlite3.Connection | None = sqlite3.connect(
            self.path,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        try:
            self._initialize_schema()
        except BaseException:
            connection = self._connection
            self._connection = None
            if connection is not None:
                connection.close()
            raise

    def close(self) -> None:
        with self._lock:
            if self._connection is None:
                return

            connection = self._connection
            self._connection = None
            connection.close()

    def append_message(
        self,
        *,
        profile_id: str,
        source_type: str,
        role: str,
        content: str,
        source_id: str | None,
    ) -> ShortTermMessage:
        message_id = f"message_{uuid4().hex}"
        created_at = _now_iso()

        with self._lock:
            connection = self._get_connection()
            with connection:
                cursor = connection.execute(
                    """
                    insert into short_term_messages (
                      id, profile_id, source_type, role, content, source_id, created_at
                    )
                    values (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        profile_id,
                        source_type,
                        role,
                        content,
                        source_id,
                        created_at,
                    ),
                )
                sequence = cursor.lastrowid

        if sequence is None:
            raise RuntimeError("SQLite did not assign a message sequence")

        return ShortTermMessage(
            id=message_id,
            sequence=sequence,
            role=role,
            content=content,
            source_id=source_id,
            created_at=created_at,
        )

    def add_breakpoint(
        self,
        *,
        profile_id: str,
        source_type: str,
        topic: str | None,
        reason: str | None,
    ) -> ShortTermBreakpoint | None:
        with self._lock:
            connection = self._get_connection()
            with connection:
                latest = connection.execute(
                    """
                    select max(sequence) as message_sequence
                    from short_term_messages
                    where profile_id = ? and source_type = ?
                    """,
                    (profile_id, source_type),
                ).fetchone()
                message_sequence = latest["message_sequence"]
                if message_sequence is None:
                    return None

                breakpoint_id = f"breakpoint_{uuid4().hex}"
                created_at = _now_iso()
                cursor = connection.execute(
                    """
                    insert or ignore into short_term_breakpoints (
                      id, profile_id, source_type, message_sequence, topic, reason, created_at
                    )
                    values (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        breakpoint_id,
                        profile_id,
                        source_type,
                        message_sequence,
                        topic,
                        reason,
                        created_at,
                    ),
                )
                if cursor.rowcount == 0:
                    return None

                message_index = connection.execute(
                    """
                    select count(*) as message_index
                    from short_term_messages
                    where profile_id = ?
                      and source_type = ?
                      and sequence <= ?
                    """,
                    (profile_id, source_type, message_sequence),
                ).fetchone()["message_index"]

        return ShortTermBreakpoint(
            id=breakpoint_id,
            message_sequence=message_sequence,
            message_index=message_index,
            topic=topic,
            reason=reason,
            created_at=created_at,
        )

    def list_messages(
        self,
        profile_id: str,
        source_type: str,
    ) -> list[ShortTermMessage]:
        with self._lock:
            rows = self._get_connection().execute(
                """
                select id, sequence, role, content, source_id, created_at
                from short_term_messages
                where profile_id = ? and source_type = ?
                order by sequence asc
                """,
                (profile_id, source_type),
            ).fetchall()

        return [_message_from_row(row) for row in rows]

    def list_breakpoints(
        self,
        profile_id: str,
        source_type: str,
    ) -> list[ShortTermBreakpoint]:
        with self._lock:
            rows = self._get_connection().execute(
                """
                select
                  breakpoint.id,
                  breakpoint.message_sequence,
                  breakpoint.topic,
                  breakpoint.reason,
                  breakpoint.created_at,
                  (
                    select count(*)
                    from short_term_messages as message
                    where message.profile_id = breakpoint.profile_id
                      and message.source_type = breakpoint.source_type
                      and message.sequence <= breakpoint.message_sequence
                  ) as message_index
                from short_term_breakpoints as breakpoint
                where breakpoint.profile_id = ? and breakpoint.source_type = ?
                order by breakpoint.message_sequence asc
                """,
                (profile_id, source_type),
            ).fetchall()

        return [_breakpoint_from_row(row) for row in rows]

    def get_stats(self, profile_id: str, source_type: str) -> BufferStats:
        with self._lock:
            connection = self._get_connection()
            message_stats = connection.execute(
                """
                select
                  count(*) as message_count,
                  coalesce(sum(length(content)), 0) as char_count
                from short_term_messages
                where profile_id = ? and source_type = ?
                """,
                (profile_id, source_type),
            ).fetchone()
            breakpoint_count = connection.execute(
                """
                select count(*) as breakpoint_count
                from short_term_breakpoints
                where profile_id = ? and source_type = ?
                """,
                (profile_id, source_type),
            ).fetchone()["breakpoint_count"]

        return BufferStats(
            message_count=message_stats["message_count"],
            breakpoint_count=breakpoint_count,
            char_count=message_stats["char_count"],
        )

    def _get_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("SQLiteShortTermStore is closed")
        return self._connection

    def _initialize_schema(self) -> None:
        raw_version = self._read_schema_version()
        if raw_version is None:
            self._create_schema()
            return

        try:
            version = int(raw_version)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"Invalid short-term schema version: {raw_version!r}"
            ) from error

        if version != SCHEMA_VERSION:
            raise RuntimeError(
                "Unsupported short-term schema version "
                f"{version}; supported version is {SCHEMA_VERSION}"
            )

        self._validate_schema()

    def _read_schema_version(self) -> str | None:
        with self._lock:
            connection = self._get_connection()
            schema_info = connection.execute(
                """
                select 1
                from sqlite_master
                where type = 'table' and name = ?
                """,
                ("short_term_schema_info",),
            ).fetchone()
            if schema_info is None:
                return None

            try:
                row = connection.execute(
                    """
                    select value
                    from short_term_schema_info
                    where key = ?
                    """,
                    ("schema_version",),
                ).fetchone()
            except sqlite3.Error as error:
                raise RuntimeError(
                    f"Invalid short-term schema metadata: {error}"
                ) from error

        return None if row is None else row["value"]

    def _create_schema(self) -> None:
        with self._lock:
            connection = self._get_connection()
            with connection:
                connection.execute("begin immediate")
                for statement in _SCHEMA_STATEMENTS:
                    try:
                        connection.execute(statement)
                    except sqlite3.Error as error:
                        compact_statement = " ".join(statement.split())
                        raise RuntimeError(
                            "Failed to initialize short-term schema while executing "
                            f"{compact_statement!r}: {error}"
                        ) from error

                self._validate_schema()
                connection.execute(
                    """
                    insert into short_term_schema_info(key, value, updated_at)
                    values (?, ?, ?)
                    """,
                    ("schema_version", str(SCHEMA_VERSION), _now_iso()),
                )

    def _validate_schema(self) -> None:
        connection = self._get_connection()
        indexes_by_table: dict[str, dict[str, _IndexMetadata]] = {}

        for table_name, expected_columns in _V1_TABLE_COLUMNS.items():
            row = connection.execute(
                """
                select sql
                from sqlite_master
                where type = 'table' and name = ?
                """,
                (table_name,),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    f"Incompatible short-term schema: missing table {table_name}"
                )

            actual_columns = {
                column["name"]: column
                for column in connection.execute(
                    """
                    select *
                    from pragma_table_xinfo(?)
                    """,
                    (table_name,),
                )
            }
            expected_names = set(expected_columns)
            actual_names = set(actual_columns)
            if actual_names != expected_names:
                missing = ", ".join(sorted(expected_names - actual_names)) or "none"
                unexpected = ", ".join(sorted(actual_names - expected_names)) or "none"
                raise RuntimeError(
                    "Incompatible short-term schema: "
                    f"{table_name} columns differ; missing: {missing}; "
                    f"unexpected: {unexpected}"
                )

            for column_name, expected in expected_columns.items():
                actual = actual_columns[column_name]
                actual_type = _normalize_declared_type(actual["type"])
                actual_not_null = bool(actual["notnull"]) or int(actual["pk"]) > 0
                actual_primary_key = int(actual["pk"])
                actual_default = _normalize_default(actual["dflt_value"])
                actual_hidden = int(actual["hidden"])
                actual_structure = (
                    actual_type,
                    actual_not_null,
                    actual_primary_key,
                    actual_default,
                    actual_hidden,
                )
                expected_structure = (
                    expected.declared_type,
                    expected.required_not_null,
                    expected.primary_key_position,
                    expected.default,
                    0,
                )
                if actual_structure != expected_structure:
                    raise RuntimeError(
                        "Incompatible short-term schema: "
                        f"{table_name}.{column_name} has structure "
                        f"{actual_structure}, expected {expected_structure}"
                    )

            normalized_sql = _normalize_schema_sql(row["sql"] or "")
            for check in _V1_REQUIRED_CHECKS.get(table_name, ()):
                if check not in normalized_sql:
                    raise RuntimeError(
                        "Incompatible short-term schema: "
                        f"{table_name} is missing CHECK constraint {check}"
                    )

            indexes = _read_table_indexes(connection, table_name)
            indexes_by_table[table_name] = indexes
            for unique_columns in _V1_REQUIRED_UNIQUE_COLUMNS.get(table_name, ()):
                has_unique_index = any(
                    metadata.unique
                    and not metadata.partial
                    and metadata.columns == unique_columns
                    for metadata in indexes.values()
                )
                if not has_unique_index:
                    raise RuntimeError(
                        "Incompatible short-term schema: "
                        f"{table_name} is missing unique index on {unique_columns}"
                    )

        for index_name, expected in _V1_EXPLICIT_INDEXES.items():
            metadata = indexes_by_table[expected.table_name].get(index_name)
            if (
                metadata is None
                or metadata.columns != expected.columns
                or metadata.unique
                or metadata.origin != "c"
                or metadata.partial
            ):
                raise RuntimeError(
                    "Incompatible short-term schema: "
                    f"{index_name} has structure {metadata}, expected a non-unique "
                    f"explicit index on {expected.columns}"
                )


def _message_from_row(row: sqlite3.Row) -> ShortTermMessage:
    return ShortTermMessage(
        id=row["id"],
        sequence=row["sequence"],
        role=row["role"],
        content=row["content"],
        source_id=row["source_id"],
        created_at=row["created_at"],
    )


def _breakpoint_from_row(row: sqlite3.Row) -> ShortTermBreakpoint:
    return ShortTermBreakpoint(
        id=row["id"],
        message_sequence=row["message_sequence"],
        message_index=row["message_index"],
        topic=row["topic"],
        reason=row["reason"],
        created_at=row["created_at"],
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_declared_type(value: object) -> str:
    return " ".join(str(value).strip().upper().split())


def _normalize_default(value: object | None) -> str | None:
    if value is None:
        return None

    normalized = "".join(str(value).strip().lower().split())
    while len(normalized) >= 2 and normalized[0] == "(" and normalized[-1] == ")":
        normalized = normalized[1:-1]
    return normalized


def _normalize_schema_sql(sql: str) -> str:
    return "".join(sql.lower().split())


def _read_table_indexes(
    connection: sqlite3.Connection,
    table_name: str,
) -> dict[str, _IndexMetadata]:
    indexes: dict[str, _IndexMetadata] = {}
    for row in connection.execute(
        """
        select *
        from pragma_index_list(?)
        """,
        (table_name,),
    ):
        index_name = row["name"]
        columns = tuple(
            column["name"]
            for column in connection.execute(
                """
                select name
                from pragma_index_info(?)
                order by seqno
                """,
                (index_name,),
            )
        )
        indexes[index_name] = _IndexMetadata(
            columns=columns,
            unique=bool(row["unique"]),
            origin=row["origin"],
            partial=bool(row["partial"]),
        )
    return indexes
