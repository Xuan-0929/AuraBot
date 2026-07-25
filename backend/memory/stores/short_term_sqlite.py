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
        self._migrate()

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

    def _migrate(self) -> None:
        with self._lock:
            connection = self._get_connection()
            with connection:
                connection.executescript(
                    """
                    create table if not exists short_term_schema_info (
                      key text primary key,
                      value text not null,
                      updated_at text not null
                    );

                    create table if not exists short_term_messages (
                      sequence integer primary key autoincrement,
                      id text not null unique,
                      profile_id text not null,
                      source_type text not null,
                      role text not null,
                      content text not null,
                      source_id text,
                      created_at text not null
                    );

                    create table if not exists short_term_breakpoints (
                      id text primary key,
                      profile_id text not null,
                      source_type text not null,
                      message_sequence integer not null,
                      topic text,
                      reason text,
                      created_at text not null,
                      unique(profile_id, source_type, message_sequence)
                    );

                    create table if not exists short_term_segments (
                      id text primary key,
                      profile_id text not null,
                      source_type text not null,
                      breakpoint_id text not null unique,
                      state text not null check (
                        state in ('pending', 'processing', 'completed', 'failed')
                      ),
                      payload_json text not null,
                      result_json text not null default '{}',
                      attempt_count integer not null default 0,
                      last_error text,
                      lease_owner text,
                      lease_expires_at text,
                      created_at text not null,
                      updated_at text not null,
                      completed_at text
                    );

                    create index if not exists idx_short_term_messages_profile_source_sequence
                      on short_term_messages(profile_id, source_type, sequence);

                    create index if not exists idx_short_term_breakpoints_profile_source_sequence
                      on short_term_breakpoints(profile_id, source_type, message_sequence);

                    create index if not exists idx_short_term_segments_profile_source_state_created
                      on short_term_segments(profile_id, source_type, state, created_at);
                    """
                )
                connection.execute(
                    """
                    insert into short_term_schema_info(key, value, updated_at)
                    values ('schema_version', ?, ?)
                    on conflict(key) do update set
                      value = excluded.value,
                      updated_at = excluded.updated_at
                    """,
                    (str(SCHEMA_VERSION), _now_iso()),
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
