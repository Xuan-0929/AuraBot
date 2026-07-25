from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import uuid4


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
      completed_at text,
      check (
        state != 'processing'
        or (
          lease_owner is not null
          and length(trim(lease_owner)) > 0
          and lease_expires_at is not null
        )
      )
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
            None,
            None,
        ),
        (
            "state-processing",
            "processing",
            '["message-1","message-2"]',
            '["worker-active"]',
            2,
            "schema-worker",
            "1970-01-01T00:01:00+00:00",
        ),
        (
            "state-completed",
            "completed",
            '{"messages":["summarized"]}',
            '["memory-1","memory-2"]',
            2,
            None,
            None,
        ),
        (
            "state-failed",
            "failed",
            '["message-failed"]',
            '{"error":"summary failed"}',
            2,
            None,
            None,
        ),
    )
    invalid_probes = (
        ("state", "not-a-state", "{}", "{}", 0, None, None),
        ("payload_json", "pending", "not-json", "{}", 0, None, None),
        ("result_json", "pending", "{}", "not-json", 0, None, None),
        ("attempt_count", "pending", "{}", "{}", -1, None, None),
        (
            "processing lease null owner",
            "processing",
            "{}",
            "{}",
            0,
            None,
            "1970-01-01T00:01:00+00:00",
        ),
        (
            "processing lease blank owner",
            "processing",
            "{}",
            "{}",
            0,
            " ",
            "1970-01-01T00:01:00+00:00",
        ),
        (
            "processing lease null expiry",
            "processing",
            "{}",
            "{}",
            0,
            "schema-worker",
            None,
        ),
    )

    with _rollback_only_validation(connection):
        for (
            label,
            state,
            payload_json,
            result_json,
            attempt_count,
            lease_owner,
            lease_expires_at,
        ) in valid_probes:
            try:
                _insert_validation_segment(
                    connection,
                    label=label,
                    state=state,
                    payload_json=payload_json,
                    result_json=result_json,
                    attempt_count=attempt_count,
                    lease_owner=lease_owner,
                    lease_expires_at=lease_expires_at,
                )
            except sqlite3.IntegrityError as error:
                raise RuntimeError(
                    "Incompatible short-term schema: overly restrictive segment "
                    f"constraint rejects valid {label}"
                ) from error

        for (
            label,
            state,
            payload_json,
            result_json,
            attempt_count,
            lease_owner,
            lease_expires_at,
        ) in invalid_probes:
            try:
                _insert_validation_segment(
                    connection,
                    label=label,
                    state=state,
                    payload_json=payload_json,
                    result_json=result_json,
                    attempt_count=attempt_count,
                    lease_owner=lease_owner,
                    lease_expires_at=lease_expires_at,
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
    lease_owner: str | None,
    lease_expires_at: str | None,
) -> None:
    validation_id = uuid4().hex
    connection.execute(
        """
        insert into short_term_segments (
          id, profile_id, source_type, breakpoint_id, state,
          payload_json, result_json, attempt_count,
          lease_owner, lease_expires_at, created_at, updated_at
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            lease_owner,
            lease_expires_at,
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
