from __future__ import annotations

import json
import sqlite3
import threading
from datetime import timedelta
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
from .short_term_store.codec import (
    _breakpoint_from_row,
    _message_from_row,
    _now_iso,
    _parse_aware_iso_timestamp,
    _require_nonblank,
    _require_optional_string,
    _require_positive_int,
    _summary_request_from_segment_row,
    _summary_request_to_json,
    _validate_completed_result,
)
from .short_term_store.delivery import (
    _get_exact_segment_row,
    _recover_expired_segment_leases,
    _require_live_processing_lease,
    _require_processing_owner,
)
from .short_term_store.files import (
    _prepare_sqlite_file,
    _validate_sqlite_file_identity,
)
from .short_term_store.legacy import (
    LegacySnapshotImportResult,
    _legacy_snapshot_marker_key,
    _legacy_snapshot_marker_value,
    _load_legacy_snapshot,
)
from .short_term_store.schema import (
    SCHEMA_VERSION,
    _IndexMetadata,
    _SCHEMA_STATEMENTS,
    _V1_EXPLICIT_INDEXES,
    _V1_REQUIRED_UNIQUE_COLUMNS,
    _V1_TABLE_COLUMNS,
    _normalize_declared_type,
    _normalize_default,
    _read_table_indexes,
    _validate_effective_segment_checks,
    _validate_monotonic_message_sequence,
)

__all__ = ["LegacySnapshotImportResult", "SQLiteShortTermStore"]


class SQLiteShortTermStore:
    def __init__(self, path: str | Path):
        expected_identity = None
        if str(path) == ":memory:":
            self.path: str | Path = ":memory:"
        else:
            self.path = Path(path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            expected_identity = _prepare_sqlite_file(self.path)

        connection = sqlite3.connect(
            self.path,
            check_same_thread=False,
        )
        try:
            if expected_identity is not None:
                _validate_sqlite_file_identity(
                    self.path,
                    expected_identity=expected_identity,
                )
        except BaseException:
            connection.close()
            raise

        self._connection: sqlite3.Connection | None = connection
        connection.row_factory = sqlite3.Row
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

    def import_legacy_snapshot(
        self,
        *,
        profile_id: str,
        source_type: str,
        legacy_path: str | Path,
    ) -> LegacySnapshotImportResult:
        _require_nonblank("profile_id", profile_id)
        _require_nonblank("source_type", source_type)
        path = Path(legacy_path)
        marker_key = _legacy_snapshot_marker_key(profile_id, source_type)

        with self._lock:
            connection = self._get_connection()
            try:
                with connection:
                    connection.execute("begin immediate")
                    marker = connection.execute(
                        """
                        select 1
                        from short_term_schema_info
                        where key = ?
                        """,
                        (marker_key,),
                    ).fetchone()
                    if marker is not None:
                        return LegacySnapshotImportResult(
                            imported=False,
                            reason="already_imported",
                            message_count=0,
                            breakpoint_count=0,
                        )

                    has_state = connection.execute(
                        """
                        select (
                          exists(
                            select 1
                            from short_term_messages
                            where profile_id = ? and source_type = ?
                          )
                          or exists(
                            select 1
                            from short_term_breakpoints
                            where profile_id = ? and source_type = ?
                          )
                          or exists(
                            select 1
                            from short_term_segments
                            where profile_id = ? and source_type = ?
                          )
                        ) as has_state
                        """,
                        (
                            profile_id,
                            source_type,
                            profile_id,
                            source_type,
                            profile_id,
                            source_type,
                        ),
                    ).fetchone()["has_state"]
                    if has_state:
                        connection.execute(
                            """
                            insert into short_term_schema_info(
                              key, value, updated_at
                            )
                            values (?, ?, ?)
                            """,
                            (
                                marker_key,
                                _legacy_snapshot_marker_value(
                                    profile_id,
                                    source_type,
                                    path,
                                    status="existing_state_won",
                                ),
                                _now_iso(),
                            ),
                        )
                        return LegacySnapshotImportResult(
                            imported=False,
                            reason="existing_state",
                            message_count=0,
                            breakpoint_count=0,
                        )

                    try:
                        messages, breakpoints = _load_legacy_snapshot(
                            path,
                            profile_id=profile_id,
                            source_type=source_type,
                        )
                    except FileNotFoundError:
                        return LegacySnapshotImportResult(
                            imported=False,
                            reason="legacy_absent",
                            message_count=0,
                            breakpoint_count=0,
                        )
                    message_sequences: list[int] = []
                    for message in messages:
                        cursor = connection.execute(
                            """
                            insert into short_term_messages (
                              id, profile_id, source_type, role, content,
                              source_id, created_at
                            )
                            values (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                f"message_{uuid4().hex}",
                                profile_id,
                                source_type,
                                message.role,
                                message.content,
                                message.source_id,
                                message.created_at,
                            ),
                        )
                        if cursor.lastrowid is None:
                            raise RuntimeError(
                                "SQLite did not assign a legacy message sequence"
                            )
                        message_sequences.append(cursor.lastrowid)

                    for breakpoint in breakpoints:
                        connection.execute(
                            """
                            insert into short_term_breakpoints (
                              id, profile_id, source_type, message_sequence,
                              topic, reason, created_at
                            )
                            values (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                breakpoint.id,
                                profile_id,
                                source_type,
                                message_sequences[breakpoint.message_index - 1],
                                breakpoint.topic,
                                breakpoint.reason,
                                breakpoint.created_at,
                            ),
                        )

                    connection.execute(
                        """
                        insert into short_term_schema_info(key, value, updated_at)
                        values (?, ?, ?)
                        """,
                        (
                            marker_key,
                            _legacy_snapshot_marker_value(
                                profile_id,
                                source_type,
                                path,
                                status="imported",
                            ),
                            _now_iso(),
                        ),
                    )
            except sqlite3.IntegrityError as error:
                raise ValueError(
                    f"Invalid legacy short-term snapshot: {error}"
                ) from error

        return LegacySnapshotImportResult(
            imported=True,
            reason="imported",
            message_count=len(messages),
            breakpoint_count=len(breakpoints),
        )

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
        try:
            lease_expires_at = (
                claimed_at + timedelta(seconds=lease_seconds)
            ).isoformat()
        except OverflowError as error:
            raise ValueError(
                "lease_seconds produces an out-of-range lease_expires_at"
            ) from error

        with self._lock:
            connection = self._get_connection()
            with connection:
                connection.execute("begin immediate")
                _recover_expired_segment_leases(
                    connection,
                    profile_id=profile_id,
                    source_type=source_type,
                    now=claimed_at,
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
                      and profile_id = ?
                      and source_type = ?
                    """,
                    (
                        SegmentState.PROCESSING.value,
                        owner,
                        lease_expires_at,
                        claimed_at_iso,
                        row["id"],
                        SegmentState.PENDING.value,
                        profile_id,
                        source_type,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ShortTermStateError(
                        "Short-term segment "
                        f"{row['id']!r} changed while it was being claimed"
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
        source_type: str,
        segment_id: str,
        owner: str,
        outcome: str,
        episode_id: str | None,
        memory_ids: list[str],
        reason: str | None,
        now: str | None = None,
    ) -> SummaryRequest:
        _require_nonblank("profile_id", profile_id)
        _require_nonblank("source_type", source_type)
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
                    source_type=source_type,
                    segment_id=segment_id,
                )
                if row["state"] == SegmentState.COMPLETED.value:
                    request = _summary_request_from_segment_row(row)
                    _validate_completed_result(row)
                else:
                    _summary_request_from_segment_row(row)
                    _require_processing_owner(row, owner)
                    transition_at = _parse_aware_iso_timestamp(
                        now,
                        field_name="now",
                    )
                    observed_lease = _require_live_processing_lease(
                        row,
                        transition_at,
                    )
                    completed_at = transition_at.isoformat()
                    cursor = connection.execute(
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
                          and profile_id = ?
                          and source_type = ?
                          and state = ?
                          and lease_owner = ?
                          and lease_expires_at = ?
                        """,
                        (
                            SegmentState.COMPLETED.value,
                            result_json,
                            completed_at,
                            completed_at,
                            segment_id,
                            profile_id,
                            source_type,
                            SegmentState.PROCESSING.value,
                            owner,
                            observed_lease,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise ShortTermStateError(
                            "Short-term segment "
                            f"{segment_id!r} changed while it was acknowledged"
                        )
                    updated_row = _get_exact_segment_row(
                        connection,
                        profile_id=profile_id,
                        source_type=source_type,
                        segment_id=segment_id,
                    )
                    request = _summary_request_from_segment_row(updated_row)

        return request

    def release_segment(
        self,
        *,
        profile_id: str,
        source_type: str,
        segment_id: str,
        owner: str,
        error: str,
        max_attempts: int,
        now: str | None = None,
    ) -> SummaryRequest:
        _require_nonblank("profile_id", profile_id)
        _require_nonblank("source_type", source_type)
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
                    source_type=source_type,
                    segment_id=segment_id,
                )
                _summary_request_from_segment_row(row)
                _require_processing_owner(row, owner)
                transition_at = _parse_aware_iso_timestamp(
                    now,
                    field_name="now",
                )
                observed_lease = _require_live_processing_lease(
                    row,
                    transition_at,
                )
                next_state = (
                    SegmentState.FAILED
                    if row["attempt_count"] >= max_attempts
                    else SegmentState.PENDING
                )
                cursor = connection.execute(
                    """
                    update short_term_segments
                    set state = ?,
                        last_error = ?,
                        lease_owner = null,
                        lease_expires_at = null,
                        updated_at = ?
                    where id = ?
                      and profile_id = ?
                      and source_type = ?
                      and state = ?
                      and lease_owner = ?
                      and lease_expires_at = ?
                    """,
                    (
                        next_state.value,
                        error,
                        transition_at.isoformat(),
                        segment_id,
                        profile_id,
                        source_type,
                        SegmentState.PROCESSING.value,
                        owner,
                        observed_lease,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ShortTermStateError(
                        "Short-term segment "
                        f"{segment_id!r} changed while it was released"
                    )
                updated_row = _get_exact_segment_row(
                    connection,
                    profile_id=profile_id,
                    source_type=source_type,
                    segment_id=segment_id,
                )
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
        recovered_at = _parse_aware_iso_timestamp(now, field_name="now")

        with self._lock:
            connection = self._get_connection()
            with connection:
                connection.execute("begin immediate")
                recovered_count = _recover_expired_segment_leases(
                    connection,
                    profile_id=profile_id,
                    source_type=source_type,
                    now=recovered_at,
                )

        return recovered_count

    def requeue_failed_segment(
        self,
        *,
        profile_id: str,
        source_type: str,
        segment_id: str,
    ) -> SummaryRequest:
        _require_nonblank("profile_id", profile_id)
        _require_nonblank("source_type", source_type)
        _require_nonblank("segment_id", segment_id)

        with self._lock:
            connection = self._get_connection()
            with connection:
                connection.execute("begin immediate")
                row = _get_exact_segment_row(
                    connection,
                    profile_id=profile_id,
                    source_type=source_type,
                    segment_id=segment_id,
                )
                _summary_request_from_segment_row(row)
                if row["state"] != SegmentState.FAILED.value:
                    raise ShortTermStateError(
                        f"Short-term segment {segment_id!r} is not failed"
                    )
                cursor = connection.execute(
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
                      and profile_id = ?
                      and source_type = ?
                      and state = ?
                    """,
                    (
                        SegmentState.PENDING.value,
                        _now_iso(),
                        segment_id,
                        profile_id,
                        source_type,
                        SegmentState.FAILED.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ShortTermStateError(
                        "Short-term segment "
                        f"{segment_id!r} changed while it was requeued"
                    )
                updated_row = _get_exact_segment_row(
                    connection,
                    profile_id=profile_id,
                    source_type=source_type,
                    segment_id=segment_id,
                )
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
                    insert or ignore into short_term_schema_info(
                      key, value, updated_at
                    )
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
