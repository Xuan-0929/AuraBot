from __future__ import annotations

import sqlite3
import threading
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

_REQUIRED_COLUMNS = {
    "short_term_schema_info": {"key", "value", "updated_at"},
    "short_term_messages": {
        "sequence",
        "id",
        "profile_id",
        "source_type",
        "role",
        "content",
        "source_id",
        "created_at",
    },
    "short_term_breakpoints": {
        "id",
        "profile_id",
        "source_type",
        "message_sequence",
        "topic",
        "reason",
        "created_at",
    },
    "short_term_segments": {
        "id",
        "profile_id",
        "source_type",
        "breakpoint_id",
        "state",
        "payload_json",
        "result_json",
        "attempt_count",
        "last_error",
        "lease_owner",
        "lease_expires_at",
        "created_at",
        "updated_at",
        "completed_at",
    },
}

_REQUIRED_CONSTRAINTS = {
    "short_term_schema_info": (
        "keytextprimarykey",
    ),
    "short_term_messages": (
        "sequenceintegerprimarykeyautoincrement",
        "idtextnotnullunique",
    ),
    "short_term_breakpoints": (
        "idtextprimarykey",
        "unique(profile_id,source_type,message_sequence)",
    ),
    "short_term_segments": (
        "idtextprimarykey",
        "breakpoint_idtextnotnullunique",
        "check(statein('pending','processing','completed','failed'))",
        "check(json_valid(payload_json))",
        "check(json_valid(result_json))",
        "check(attempt_count>=0)",
    ),
}

_REQUIRED_INDEXES = {
    "idx_short_term_messages_profile_source_sequence": (
        "profile_id",
        "source_type",
        "sequence",
    ),
    "idx_short_term_breakpoints_profile_source_sequence": (
        "profile_id",
        "source_type",
        "message_sequence",
    ),
    "idx_short_term_segments_profile_source_state_created": (
        "profile_id",
        "source_type",
        "state",
        "created_at",
    ),
    "idx_short_term_segments_profile_source_state_lease": (
        "profile_id",
        "source_type",
        "state",
        "lease_expires_at",
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

        for table_name, required_columns in _REQUIRED_COLUMNS.items():
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

            columns = {
                column["name"]
                for column in connection.execute(
                    """
                    select name
                    from pragma_table_info(?)
                    """,
                    (table_name,),
                )
            }
            missing_columns = required_columns - columns
            if missing_columns:
                missing = ", ".join(sorted(missing_columns))
                raise RuntimeError(
                    "Incompatible short-term schema: "
                    f"{table_name} is missing columns: {missing}"
                )

            normalized_sql = "".join((row["sql"] or "").lower().split())
            for constraint in _REQUIRED_CONSTRAINTS.get(table_name, ()):
                if constraint not in normalized_sql:
                    raise RuntimeError(
                        "Incompatible short-term schema: "
                        f"{table_name} is missing constraint {constraint}"
                    )

        for index_name, required_columns in _REQUIRED_INDEXES.items():
            columns = tuple(
                row["name"]
                for row in connection.execute(
                    """
                    select name
                    from pragma_index_info(?)
                    order by seqno
                    """,
                    (index_name,),
                )
            )
            if columns != required_columns:
                raise RuntimeError(
                    "Incompatible short-term schema: "
                    f"{index_name} has columns {columns}, expected {required_columns}"
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
