from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from ..short_term.errors import ShortTermStateError
from ..short_term.models import (
    BufferStats,
    SegmentState,
    ShortTermBreakpoint,
    ShortTermMessage,
    SummaryRequest,
)


SCHEMA_VERSION = 1

_SCHEMA_STATEMENTS = (
    """
    create table if not exists short_term_schema_info (
      key text not null primary key,
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
      id text not null primary key,
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
      id text not null primary key,
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

    def prepare_oldest_segment(
        self,
        *,
        profile_id: str,
        source_type: str,
    ) -> SummaryRequest | None:
        with self._lock:
            connection = self._get_connection()
            with connection:
                connection.execute("begin immediate")
                breakpoint_row = connection.execute(
                    """
                    select
                      id, message_sequence, topic, reason, created_at
                    from short_term_breakpoints
                    where profile_id = ? and source_type = ?
                    order by message_sequence asc, created_at asc, id asc
                    limit 1
                    """,
                    (profile_id, source_type),
                ).fetchone()
                if breakpoint_row is None:
                    return None

                message_rows = connection.execute(
                    """
                    select id, sequence, role, content, source_id, created_at
                    from short_term_messages
                    where profile_id = ?
                      and source_type = ?
                      and sequence <= ?
                    order by sequence asc
                    """,
                    (
                        profile_id,
                        source_type,
                        breakpoint_row["message_sequence"],
                    ),
                ).fetchall()
                if not message_rows:
                    raise RuntimeError(
                        "Persisted short-term breakpoint "
                        f"{breakpoint_row['id']!r} has no matching messages"
                    )
                final_message_sequence = message_rows[-1]["sequence"]
                if final_message_sequence != breakpoint_row["message_sequence"]:
                    raise RuntimeError(
                        "Persisted short-term breakpoint "
                        f"{breakpoint_row['id']!r} has an inconsistent breakpoint "
                        "boundary: expected final message sequence "
                        f"{breakpoint_row['message_sequence']}, found "
                        f"{final_message_sequence}"
                    )

                segment_id = f"segment_{uuid4().hex}"
                created_at = _now_iso()
                request = SummaryRequest(
                    segment_id=segment_id,
                    profile_id=profile_id,
                    source_type=source_type,
                    breakpoint_id=breakpoint_row["id"],
                    topic=breakpoint_row["topic"],
                    reason=breakpoint_row["reason"],
                    messages=tuple(
                        _message_from_row(row) for row in message_rows
                    ),
                    state=SegmentState.PENDING,
                    attempt_count=0,
                    created_at=created_at,
                    lease_expires_at=None,
                )
                payload_json = _summary_request_to_json(request)

                connection.execute(
                    """
                    insert into short_term_segments (
                      id, profile_id, source_type, breakpoint_id, state,
                      payload_json, result_json, attempt_count,
                      last_error, lease_owner, lease_expires_at,
                      created_at, updated_at, completed_at
                    )
                    values (?, ?, ?, ?, ?, ?, '{}', 0, null, null, null, ?, ?, null)
                    """,
                    (
                        segment_id,
                        profile_id,
                        source_type,
                        breakpoint_row["id"],
                        SegmentState.PENDING.value,
                        payload_json,
                        created_at,
                        created_at,
                    ),
                )
                connection.execute(
                    """
                    delete from short_term_messages
                    where profile_id = ?
                      and source_type = ?
                      and sequence <= ?
                    """,
                    (
                        profile_id,
                        source_type,
                        breakpoint_row["message_sequence"],
                    ),
                )
                connection.execute(
                    """
                    delete from short_term_breakpoints
                    where profile_id = ?
                      and source_type = ?
                      and message_sequence <= ?
                    """,
                    (
                        profile_id,
                        source_type,
                        breakpoint_row["message_sequence"],
                    ),
                )

        return request

    def claim_next_segment(
        self,
        *,
        profile_id: str,
        source_type: str,
        owner: str,
        lease_seconds: int,
        now: str | None = None,
    ) -> SummaryRequest | None:
        _require_nonblank("profile_id", profile_id)
        _require_nonblank("source_type", source_type)
        _require_nonblank("owner", owner)
        _require_positive_int("lease_seconds", lease_seconds)
        claimed_at = _parse_aware_iso_timestamp(now, field_name="now")
        claimed_at_iso = claimed_at.isoformat()
        lease_expires_at = (claimed_at + timedelta(seconds=lease_seconds)).isoformat()

        with self._lock:
            connection = self._get_connection()
            with connection:
                connection.execute("begin immediate")
                connection.execute(
                    """
                    update short_term_segments
                    set state = ?,
                        lease_owner = null,
                        lease_expires_at = null,
                        updated_at = ?
                    where profile_id = ?
                      and source_type = ?
                      and state = ?
                      and lease_expires_at is not null
                      and julianday(lease_expires_at) <= julianday(?)
                    """,
                    (
                        SegmentState.PENDING.value,
                        claimed_at_iso,
                        profile_id,
                        source_type,
                        SegmentState.PROCESSING.value,
                        claimed_at_iso,
                    ),
                )
                row = connection.execute(
                    """
                    select *
                    from short_term_segments
                    where profile_id = ?
                      and source_type = ?
                      and state = ?
                    order by created_at asc, id asc
                    limit 1
                    """,
                    (
                        profile_id,
                        source_type,
                        SegmentState.PENDING.value,
                    ),
                ).fetchone()
                if row is None:
                    return None

                _summary_request_from_segment_row(row)
                cursor = connection.execute(
                    """
                    update short_term_segments
                    set state = ?,
                        attempt_count = attempt_count + 1,
                        lease_owner = ?,
                        lease_expires_at = ?,
                        updated_at = ?
                    where id = ? and state = ?
                    """,
                    (
                        SegmentState.PROCESSING.value,
                        owner,
                        lease_expires_at,
                        claimed_at_iso,
                        row["id"],
                        SegmentState.PENDING.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(
                        f"Failed to claim short-term segment {row['id']!r}"
                    )
                updated_row = connection.execute(
                    """
                    select *
                    from short_term_segments
                    where id = ?
                    """,
                    (row["id"],),
                ).fetchone()
                request = _summary_request_from_segment_row(updated_row)

        return request

    def acknowledge_segment(
        self,
        *,
        profile_id: str,
        segment_id: str,
        owner: str,
        outcome: str,
        episode_id: str | None,
        memory_ids: list[str],
        reason: str | None,
    ) -> SummaryRequest:
        _require_nonblank("profile_id", profile_id)
        _require_nonblank("segment_id", segment_id)
        _require_nonblank("owner", owner)
        _require_nonblank("outcome", outcome)
        _require_optional_string("episode_id", episode_id)
        _require_optional_string("reason", reason)
        if type(memory_ids) is not list:
            raise TypeError("memory_ids must be a list of strings")
        if not all(isinstance(memory_id, str) for memory_id in memory_ids):
            raise TypeError("memory_ids must be a list of strings")

        result_json = json.dumps(
            {
                "outcome": outcome,
                "episode_id": episode_id,
                "memory_ids": memory_ids,
                "reason": reason,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        with self._lock:
            connection = self._get_connection()
            with connection:
                connection.execute("begin immediate")
                row = _get_exact_segment_row(
                    connection,
                    profile_id=profile_id,
                    segment_id=segment_id,
                )
                if row["state"] == SegmentState.COMPLETED.value:
                    request = _summary_request_from_segment_row(row)
                else:
                    _require_processing_owner(row, owner)
                    completed_at = _now_iso()
                    connection.execute(
                        """
                        update short_term_segments
                        set state = ?,
                            result_json = ?,
                            last_error = null,
                            lease_owner = null,
                            lease_expires_at = null,
                            updated_at = ?,
                            completed_at = ?
                        where id = ?
                        """,
                        (
                            SegmentState.COMPLETED.value,
                            result_json,
                            completed_at,
                            completed_at,
                            segment_id,
                        ),
                    )
                    updated_row = connection.execute(
                        """
                        select *
                        from short_term_segments
                        where id = ?
                        """,
                        (segment_id,),
                    ).fetchone()
                    request = _summary_request_from_segment_row(updated_row)

        return request

    def release_segment(
        self,
        *,
        profile_id: str,
        segment_id: str,
        owner: str,
        error: str,
        max_attempts: int,
    ) -> SummaryRequest:
        _require_nonblank("profile_id", profile_id)
        _require_nonblank("segment_id", segment_id)
        _require_nonblank("owner", owner)
        _require_nonblank("error", error)
        _require_positive_int("max_attempts", max_attempts)

        with self._lock:
            connection = self._get_connection()
            with connection:
                connection.execute("begin immediate")
                row = _get_exact_segment_row(
                    connection,
                    profile_id=profile_id,
                    segment_id=segment_id,
                )
                _require_processing_owner(row, owner)
                next_state = (
                    SegmentState.FAILED
                    if row["attempt_count"] >= max_attempts
                    else SegmentState.PENDING
                )
                connection.execute(
                    """
                    update short_term_segments
                    set state = ?,
                        last_error = ?,
                        lease_owner = null,
                        lease_expires_at = null,
                        updated_at = ?
                    where id = ?
                    """,
                    (
                        next_state.value,
                        error,
                        _now_iso(),
                        segment_id,
                    ),
                )
                updated_row = connection.execute(
                    """
                    select *
                    from short_term_segments
                    where id = ?
                    """,
                    (segment_id,),
                ).fetchone()
                request = _summary_request_from_segment_row(updated_row)

        return request

    def recover_expired_leases(
        self,
        *,
        profile_id: str,
        source_type: str,
        now: str | None = None,
    ) -> int:
        _require_nonblank("profile_id", profile_id)
        _require_nonblank("source_type", source_type)
        recovered_at = _parse_aware_iso_timestamp(now, field_name="now").isoformat()

        with self._lock:
            connection = self._get_connection()
            with connection:
                connection.execute("begin immediate")
                cursor = connection.execute(
                    """
                    update short_term_segments
                    set state = ?,
                        lease_owner = null,
                        lease_expires_at = null,
                        updated_at = ?
                    where profile_id = ?
                      and source_type = ?
                      and state = ?
                      and lease_expires_at is not null
                      and julianday(lease_expires_at) <= julianday(?)
                    """,
                    (
                        SegmentState.PENDING.value,
                        recovered_at,
                        profile_id,
                        source_type,
                        SegmentState.PROCESSING.value,
                        recovered_at,
                    ),
                )
                recovered_count = cursor.rowcount

        return recovered_count

    def requeue_failed_segment(
        self,
        *,
        profile_id: str,
        segment_id: str,
    ) -> SummaryRequest:
        _require_nonblank("profile_id", profile_id)
        _require_nonblank("segment_id", segment_id)

        with self._lock:
            connection = self._get_connection()
            with connection:
                connection.execute("begin immediate")
                row = _get_exact_segment_row(
                    connection,
                    profile_id=profile_id,
                    segment_id=segment_id,
                )
                if row["state"] != SegmentState.FAILED.value:
                    raise ShortTermStateError(
                        f"Short-term segment {segment_id!r} is not failed"
                    )
                connection.execute(
                    """
                    update short_term_segments
                    set state = ?,
                        result_json = '{}',
                        attempt_count = 0,
                        last_error = null,
                        lease_owner = null,
                        lease_expires_at = null,
                        updated_at = ?,
                        completed_at = null
                    where id = ?
                    """,
                    (
                        SegmentState.PENDING.value,
                        _now_iso(),
                        segment_id,
                    ),
                )
                updated_row = connection.execute(
                    """
                    select *
                    from short_term_segments
                    where id = ?
                    """,
                    (segment_id,),
                ).fetchone()
                request = _summary_request_from_segment_row(updated_row)

        return request

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
                actual_primary_key = int(actual["pk"])
                actual_not_null = bool(actual["notnull"]) or (
                    actual_type == "INTEGER" and actual_primary_key > 0
                )
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

        _validate_effective_segment_checks(connection)
        _validate_monotonic_message_sequence(connection)


def _require_nonblank(field_name: str, value: object) -> None:
    _require_string(field_name, value)
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _require_string(field_name: str, value: object) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")


def _require_optional_string(field_name: str, value: object | None) -> None:
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or None")


def _require_positive_int(field_name: str, value: object) -> None:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an integer")
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")


def _parse_aware_iso_timestamp(
    value: str | None,
    *,
    field_name: str,
) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a timezone-aware ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(
            f"{field_name} must be a timezone-aware ISO timestamp"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be a timezone-aware ISO timestamp")
    return parsed.astimezone(UTC)


def _get_exact_segment_row(
    connection: sqlite3.Connection,
    *,
    profile_id: str,
    segment_id: str,
) -> sqlite3.Row:
    row = connection.execute(
        """
        select *
        from short_term_segments
        where profile_id = ? and id = ?
        """,
        (profile_id, segment_id),
    ).fetchone()
    if row is None:
        raise ShortTermStateError(
            "Short-term segment is unavailable for the requested profile"
        )
    return row


def _require_processing_owner(row: sqlite3.Row, owner: str) -> None:
    if row["state"] != SegmentState.PROCESSING.value:
        raise ShortTermStateError(
            f"Short-term segment {row['id']!r} is not processing"
        )
    if row["lease_owner"] != owner:
        raise ShortTermStateError(
            f"Short-term segment {row['id']!r} is leased by another owner"
        )


def _summary_request_from_segment_row(row: sqlite3.Row) -> SummaryRequest:
    segment_id = row["id"]
    try:
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict):
            raise TypeError("payload must be an object")

        required_fields = {
            "segment_id",
            "profile_id",
            "source_type",
            "breakpoint_id",
            "topic",
            "reason",
            "messages",
            "created_at",
        }
        missing_fields = sorted(required_fields - payload.keys())
        if missing_fields:
            raise ValueError(
                "payload is missing required fields: "
                + ", ".join(missing_fields)
            )

        for field_name in (
            "segment_id",
            "profile_id",
            "source_type",
            "breakpoint_id",
            "created_at",
        ):
            _require_nonblank(f"payload.{field_name}", payload[field_name])
        for field_name in ("topic", "reason"):
            _require_optional_string(
                f"payload.{field_name}",
                payload[field_name],
            )

        identity_pairs = (
            ("segment_id", "id"),
            ("profile_id", "profile_id"),
            ("source_type", "source_type"),
            ("breakpoint_id", "breakpoint_id"),
        )
        for payload_field, row_field in identity_pairs:
            if payload[payload_field] != row[row_field]:
                raise ValueError(
                    f"payload.{payload_field} does not match its row column"
                )

        message_payloads = payload["messages"]
        if not isinstance(message_payloads, list):
            raise TypeError("payload.messages must be a list")
        messages = tuple(
            _message_from_payload(message_payload, index=index)
            for index, message_payload in enumerate(message_payloads)
        )

        state = SegmentState(row["state"])
        attempt_count = row["attempt_count"]
        if type(attempt_count) is not int or attempt_count < 0:
            raise TypeError("row attempt_count must be a nonnegative integer")

        created_at = row["created_at"]
        _require_nonblank("row created_at", created_at)
        lease_expires_at = row["lease_expires_at"]
        if lease_expires_at is not None:
            _parse_aware_iso_timestamp(
                lease_expires_at,
                field_name="row lease_expires_at",
            )

        return SummaryRequest(
            segment_id=payload["segment_id"],
            profile_id=payload["profile_id"],
            source_type=payload["source_type"],
            breakpoint_id=payload["breakpoint_id"],
            topic=payload["topic"],
            reason=payload["reason"],
            messages=messages,
            state=state,
            attempt_count=attempt_count,
            created_at=created_at,
            lease_expires_at=lease_expires_at,
        )
    except (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise RuntimeError(
            f"Corrupt short-term segment payload for {segment_id!r}: {error}"
        ) from error


def _message_from_payload(payload: object, *, index: int) -> ShortTermMessage:
    if not isinstance(payload, dict):
        raise TypeError(f"payload.messages[{index}] must be an object")
    required_fields = {
        "id",
        "sequence",
        "role",
        "content",
        "source_id",
        "created_at",
    }
    missing_fields = sorted(required_fields - payload.keys())
    if missing_fields:
        raise ValueError(
            f"payload.messages[{index}] is missing required fields: "
            + ", ".join(missing_fields)
        )
    for field_name in ("id", "role", "created_at"):
        _require_nonblank(
            f"payload.messages[{index}].{field_name}",
            payload[field_name],
        )
    _require_string(
        f"payload.messages[{index}].content",
        payload["content"],
    )
    sequence = payload["sequence"]
    if type(sequence) is not int or sequence < 1:
        raise TypeError(
            f"payload.messages[{index}].sequence must be a positive integer"
        )
    source_id = payload["source_id"]
    _require_optional_string(
        f"payload.messages[{index}].source_id",
        source_id,
    )
    return ShortTermMessage(
        id=payload["id"],
        sequence=sequence,
        role=payload["role"],
        content=payload["content"],
        source_id=source_id,
        created_at=payload["created_at"],
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


def _summary_request_to_json(request: SummaryRequest) -> str:
    return json.dumps(
        request.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
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


def _validate_effective_segment_checks(connection: sqlite3.Connection) -> None:
    valid_probes = (
        (
            "state-pending",
            "pending",
            '{"messages":["queued"]}',
            '{"outcome":"waiting"}',
            2,
        ),
        (
            "state-processing",
            "processing",
            '["message-1","message-2"]',
            '["worker-active"]',
            2,
        ),
        (
            "state-completed",
            "completed",
            '{"messages":["summarized"]}',
            '["memory-1","memory-2"]',
            2,
        ),
        (
            "state-failed",
            "failed",
            '["message-failed"]',
            '{"error":"summary failed"}',
            2,
        ),
    )
    invalid_probes = (
        ("state", "not-a-state", "{}", "{}", 0),
        ("payload_json", "pending", "not-json", "{}", 0),
        ("result_json", "pending", "{}", "not-json", 0),
        ("attempt_count", "pending", "{}", "{}", -1),
    )

    with _rollback_only_validation(connection):
        for label, state, payload_json, result_json, attempt_count in valid_probes:
            try:
                _insert_validation_segment(
                    connection,
                    label=label,
                    state=state,
                    payload_json=payload_json,
                    result_json=result_json,
                    attempt_count=attempt_count,
                )
            except sqlite3.IntegrityError as error:
                raise RuntimeError(
                    "Incompatible short-term schema: overly restrictive segment "
                    f"constraint rejects valid {label}"
                ) from error

        for label, state, payload_json, result_json, attempt_count in invalid_probes:
            try:
                _insert_validation_segment(
                    connection,
                    label=label,
                    state=state,
                    payload_json=payload_json,
                    result_json=result_json,
                    attempt_count=attempt_count,
                )
            except sqlite3.IntegrityError:
                continue

            raise RuntimeError(
                "Incompatible short-term schema: "
                f"effective CHECK constraint is missing for {label}"
            )


def _insert_validation_segment(
    connection: sqlite3.Connection,
    *,
    label: str,
    state: str,
    payload_json: str,
    result_json: str,
    attempt_count: int,
) -> None:
    validation_id = uuid4().hex
    connection.execute(
        """
        insert into short_term_segments (
          id, profile_id, source_type, breakpoint_id, state,
          payload_json, result_json, attempt_count, created_at, updated_at
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"validation-segment-{label}-{validation_id}",
            "schema-validation",
            "schema-validation",
            f"validation-breakpoint-{label}-{validation_id}",
            state,
            payload_json,
            result_json,
            attempt_count,
            "1970-01-01T00:00:00+00:00",
            "1970-01-01T00:00:00+00:00",
        ),
    )


def _validate_monotonic_message_sequence(connection: sqlite3.Connection) -> None:
    with _rollback_only_validation(connection):
        first_id = f"validation-message-first-{uuid4().hex}"
        first = connection.execute(
            """
            insert into short_term_messages (
              id, profile_id, source_type, role, content, source_id, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                first_id,
                "schema-validation",
                "schema-validation",
                "system",
                "first",
                None,
                "1970-01-01T00:00:00+00:00",
            ),
        ).lastrowid
        connection.execute(
            "delete from short_term_messages where id = ?",
            (first_id,),
        )
        second = connection.execute(
            """
            insert into short_term_messages (
              id, profile_id, source_type, role, content, source_id, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"validation-message-second-{uuid4().hex}",
                "schema-validation",
                "schema-validation",
                "system",
                "second",
                None,
                "1970-01-01T00:00:00+00:00",
            ),
        ).lastrowid

        if first is None or second is None or second <= first:
            raise RuntimeError(
                "Incompatible short-term schema: message sequence requires "
                "AUTOINCREMENT monotonic non-reuse"
            )


@contextmanager
def _rollback_only_validation(connection: sqlite3.Connection):
    connection.execute("savepoint short_term_schema_validation")
    try:
        yield
    finally:
        connection.execute("rollback to short_term_schema_validation")
        connection.execute("release short_term_schema_validation")
