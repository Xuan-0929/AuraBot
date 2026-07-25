from __future__ import annotations

import sqlite3
from datetime import datetime

from ...short_term.errors import ShortTermStateError
from ...short_term.models import SegmentState
from .codec import (
    _processing_lease_expiry,
    _summary_request_from_segment_row,
)


def _recover_expired_segment_leases(
    connection: sqlite3.Connection,
    *,
    profile_id: str,
    source_type: str,
    now: datetime,
) -> int:
    rows = connection.execute(
        """
        select *
        from short_term_segments
        where profile_id = ?
          and source_type = ?
          and state = ?
        order by created_at asc, id asc
        """,
        (
            profile_id,
            source_type,
            SegmentState.PROCESSING.value,
        ),
    ).fetchall()

    expired_rows = []
    for row in rows:
        _summary_request_from_segment_row(row)
        lease_expires_at = _processing_lease_expiry(row)
        if lease_expires_at <= now:
            expired_rows.append(row)

    recovered_count = 0
    recovered_at = now.isoformat()
    for row in expired_rows:
        cursor = connection.execute(
            """
            update short_term_segments
            set state = ?,
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
                SegmentState.PENDING.value,
                recovered_at,
                row["id"],
                profile_id,
                source_type,
                SegmentState.PROCESSING.value,
                row["lease_owner"],
                row["lease_expires_at"],
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(
                "Short-term segment "
                f"{row['id']!r} changed while its lease was recovered"
            )
        recovered_count += 1
    return recovered_count


def _get_exact_segment_row(
    connection: sqlite3.Connection,
    *,
    profile_id: str,
    source_type: str,
    segment_id: str,
) -> sqlite3.Row:
    row = connection.execute(
        """
        select *
        from short_term_segments
        where profile_id = ? and source_type = ? and id = ?
        """,
        (profile_id, source_type, segment_id),
    ).fetchone()
    if row is None:
        raise ShortTermStateError(
            "Short-term segment is unavailable for the requested stream"
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


def _require_live_processing_lease(
    row: sqlite3.Row,
    now: datetime,
) -> str:
    lease_expires_at = row["lease_expires_at"]
    if _processing_lease_expiry(row) <= now:
        raise ShortTermStateError("Short-term segment lease is no longer active")
    return lease_expires_at
