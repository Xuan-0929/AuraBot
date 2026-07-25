from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from ...short_term.models import (
    SegmentState,
    ShortTermBreakpoint,
    ShortTermMessage,
    SummaryRequest,
)


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
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(
                f"{field_name} must be a timezone-aware ISO timestamp"
            )
        return parsed.astimezone(UTC)
    except (OverflowError, ValueError) as error:
        raise ValueError(
            f"{field_name} must be a timezone-aware ISO timestamp"
        ) from error

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

        payload_created_at = _parse_aware_iso_timestamp(
            payload["created_at"],
            field_name="payload.created_at",
        )
        row_created_at = row["created_at"]
        _require_nonblank("row created_at", row_created_at)
        parsed_row_created_at = _parse_aware_iso_timestamp(
            row_created_at,
            field_name="row created_at",
        )
        if payload_created_at != parsed_row_created_at:
            raise ValueError(
                "payload.created_at does not match its row column instant"
            )

        message_payloads = payload["messages"]
        if not isinstance(message_payloads, list):
            raise TypeError("payload.messages must be a list")
        if not message_payloads:
            raise ValueError("payload.messages must contain at least one message")

        messages = []
        message_ids: set[str] = set()
        message_sequences: set[int] = set()
        previous_sequence = 0
        for index, message_payload in enumerate(message_payloads):
            message = _message_from_payload(message_payload, index=index)
            if message.id in message_ids:
                raise ValueError(
                    f"payload.messages[{index}].id must be unique"
                )
            if message.sequence in message_sequences:
                raise ValueError(
                    f"payload.messages[{index}].sequence must be unique"
                )
            if message.sequence <= previous_sequence:
                raise ValueError(
                    "payload message sequences must be strictly ascending"
                )
            message_ids.add(message.id)
            message_sequences.add(message.sequence)
            previous_sequence = message.sequence
            messages.append(message)

        if "state" in payload:
            _require_string("payload.state", payload["state"])
            SegmentState(payload["state"])
        if "attempt_count" in payload:
            payload_attempt_count = payload["attempt_count"]
            if type(payload_attempt_count) is not int or payload_attempt_count < 0:
                raise TypeError(
                    "payload.attempt_count must be a nonnegative integer"
                )
        if "lease_expires_at" in payload:
            payload_lease_expires_at = payload["lease_expires_at"]
            if payload_lease_expires_at is not None:
                _parse_aware_iso_timestamp(
                    payload_lease_expires_at,
                    field_name="payload.lease_expires_at",
                )

        state = SegmentState(row["state"])
        attempt_count = row["attempt_count"]
        if type(attempt_count) is not int or attempt_count < 0:
            raise TypeError("row attempt_count must be a nonnegative integer")

        lease_expires_at = row["lease_expires_at"]
        if state == SegmentState.PROCESSING:
            _processing_lease_expiry(row)
        elif lease_expires_at is not None:
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
            messages=tuple(messages),
            state=state,
            attempt_count=attempt_count,
            created_at=row_created_at,
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
    _parse_aware_iso_timestamp(
        payload["created_at"],
        field_name=f"payload.messages[{index}].created_at",
    )
    return ShortTermMessage(
        id=payload["id"],
        sequence=sequence,
        role=payload["role"],
        content=payload["content"],
        source_id=source_id,
        created_at=payload["created_at"],
    )


def _processing_lease_expiry(row: sqlite3.Row) -> datetime:
    try:
        _require_nonblank("lease_owner", row["lease_owner"])
        lease_expires_at = row["lease_expires_at"]
        if lease_expires_at is None:
            raise ValueError(
                "lease_expires_at must be a timezone-aware ISO timestamp"
            )
        return _parse_aware_iso_timestamp(
            lease_expires_at,
            field_name="lease_expires_at",
        )
    except (TypeError, ValueError) as error:
        raise RuntimeError(
            "Persisted short-term segment "
            f"{row['id']!r} has invalid processing lease: {error}"
        ) from error


def _validate_completed_result(row: sqlite3.Row) -> None:
    segment_id = row["id"]
    try:
        result = json.loads(row["result_json"])
        if not isinstance(result, dict):
            raise TypeError("result must be an object")

        required_fields = {
            "outcome",
            "episode_id",
            "memory_ids",
            "reason",
        }
        missing_fields = sorted(required_fields - result.keys())
        if missing_fields:
            raise ValueError(
                "result is missing required fields: "
                + ", ".join(missing_fields)
            )

        _require_nonblank("result.outcome", result["outcome"])
        _require_optional_string("result.episode_id", result["episode_id"])
        memory_ids = result["memory_ids"]
        if type(memory_ids) is not list or not all(
            isinstance(memory_id, str) for memory_id in memory_ids
        ):
            raise TypeError("result.memory_ids must be a list of strings")
        _require_optional_string("result.reason", result["reason"])
    except (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise RuntimeError(
            f"Corrupt short-term segment result for {segment_id!r}: {error}"
        ) from error


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
