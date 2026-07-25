from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .codec import _parse_aware_iso_timestamp


@dataclass(frozen=True)
class LegacySnapshotImportResult:
    imported: bool
    reason: str
    message_count: int
    breakpoint_count: int


@dataclass(frozen=True)
class _LegacyMessage:
    role: str
    content: str
    source_id: str | None
    created_at: str


@dataclass(frozen=True)
class _LegacyBreakpoint:
    id: str
    message_index: int
    topic: str | None
    reason: str | None
    created_at: str

_LEGACY_TOP_LEVEL_KEYS = frozenset(
    {"profile_id", "source_type", "messages", "breakpoints"}
)
_LEGACY_MESSAGE_KEYS = frozenset(
    {"role", "content", "source_id", "created_at"}
)
_LEGACY_BREAKPOINT_KEYS = frozenset(
    {"id", "message_index", "topic", "reason", "created_at"}
)

def _load_legacy_snapshot(
    path: Path,
    *,
    profile_id: str,
    source_type: str,
) -> tuple[list[_LegacyMessage], list[_LegacyBreakpoint]]:
    try:
        raw_snapshot = path.read_text(encoding="utf-8")
        snapshot = json.loads(
            raw_snapshot,
            object_pairs_hook=_legacy_object_without_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(
            f"Invalid legacy short-term JSON at {path}: malformed JSON"
        ) from error

    if not isinstance(snapshot, dict):
        raise ValueError("Invalid legacy short-term snapshot: root must be an object")
    _reject_unknown_legacy_keys(
        snapshot,
        allowed=_LEGACY_TOP_LEVEL_KEYS,
        prefix="Invalid legacy short-term snapshot",
    )
    if snapshot.get("profile_id") != profile_id:
        raise ValueError(
            "Invalid legacy short-term snapshot: profile_id does not match "
            f"{profile_id!r}"
        )
    if snapshot.get("source_type") != source_type:
        raise ValueError(
            "Invalid legacy short-term snapshot: source_type does not match "
            f"{source_type!r}"
        )

    raw_messages = snapshot.get("messages")
    raw_breakpoints = snapshot.get("breakpoints")
    if not isinstance(raw_messages, list):
        raise ValueError(
            "Invalid legacy short-term snapshot: messages must be a list"
        )
    if not isinstance(raw_breakpoints, list):
        raise ValueError(
            "Invalid legacy short-term snapshot: breakpoints must be a list"
        )

    messages = [
        _validate_legacy_message(record, index=index)
        for index, record in enumerate(raw_messages, start=1)
    ]
    breakpoints = _validate_legacy_breakpoints(
        raw_breakpoints,
        message_count=len(messages),
    )
    return messages, breakpoints


def _legacy_snapshot_marker_key(
    profile_id: str,
    source_type: str,
) -> str:
    canonical_scope = json.dumps(
        [profile_id, source_type],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical_scope.encode("utf-8")).hexdigest()
    return f"legacy_json_imported:{digest}"


def _legacy_snapshot_marker_value(
    profile_id: str,
    source_type: str,
    legacy_path: Path,
    *,
    status: str,
) -> str:
    return json.dumps(
        {
            "legacy_filename": legacy_path.name,
            "profile_id": profile_id,
            "status": status,
            "source_type": source_type,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validate_legacy_message(
    record: object,
    *,
    index: int,
) -> _LegacyMessage:
    prefix = f"Invalid legacy message at index {index}"
    if not isinstance(record, dict):
        raise ValueError(f"{prefix}: record must be an object")
    _reject_unknown_legacy_keys(
        record,
        allowed=_LEGACY_MESSAGE_KEYS,
        prefix=prefix,
    )

    role = _required_legacy_field(record, "role", prefix)
    content = _required_legacy_field(record, "content", prefix)
    source_id = _required_legacy_field(record, "source_id", prefix)
    created_at = _required_legacy_field(record, "created_at", prefix)
    if not isinstance(role, str):
        raise ValueError(f"{prefix}: role must be a string")
    if not isinstance(content, str):
        raise ValueError(f"{prefix}: content must be a string")
    if source_id is not None and not isinstance(source_id, str):
        raise ValueError(f"{prefix}: source_id must be a string or null")
    _validate_legacy_timestamp(created_at, prefix=prefix)

    return _LegacyMessage(
        role=role,
        content=content,
        source_id=source_id,
        created_at=created_at,
    )


def _validate_legacy_breakpoints(
    records: list[object],
    *,
    message_count: int,
) -> list[_LegacyBreakpoint]:
    breakpoints = []
    seen_ids: set[str] = set()
    seen_indexes: set[int] = set()
    previous_index = 0

    for position, record in enumerate(records, start=1):
        prefix = f"Invalid legacy breakpoint at index {position}"
        if not isinstance(record, dict):
            raise ValueError(f"{prefix}: record must be an object")
        _reject_unknown_legacy_keys(
            record,
            allowed=_LEGACY_BREAKPOINT_KEYS,
            prefix=prefix,
        )

        breakpoint_id = _required_legacy_field(record, "id", prefix)
        message_index = _required_legacy_field(
            record,
            "message_index",
            prefix,
        )
        topic = _required_legacy_field(record, "topic", prefix)
        reason = _required_legacy_field(record, "reason", prefix)
        created_at = _required_legacy_field(record, "created_at", prefix)

        if not isinstance(breakpoint_id, str) or not breakpoint_id.strip():
            raise ValueError(f"{prefix}: id must be a nonblank string")
        if type(message_index) is not int:
            raise ValueError(f"{prefix}: message_index must be an integer")
        if not 1 <= message_index <= message_count:
            raise ValueError(
                f"{prefix}: message_index must be between 1 and {message_count}"
            )
        if topic is not None and not isinstance(topic, str):
            raise ValueError(f"{prefix}: topic must be a string or null")
        if reason is not None and not isinstance(reason, str):
            raise ValueError(f"{prefix}: reason must be a string or null")
        _validate_legacy_timestamp(created_at, prefix=prefix)

        if breakpoint_id in seen_ids:
            raise ValueError(f"{prefix}: duplicate id {breakpoint_id!r}")
        if message_index in seen_indexes:
            raise ValueError(
                f"{prefix}: duplicate message_index {message_index}"
            )
        if message_index <= previous_index:
            raise ValueError(
                f"{prefix}: message indexes must be strictly increasing"
            )
        seen_ids.add(breakpoint_id)
        seen_indexes.add(message_index)
        previous_index = message_index
        breakpoints.append(
            _LegacyBreakpoint(
                id=breakpoint_id,
                message_index=message_index,
                topic=topic,
                reason=reason,
                created_at=created_at,
            )
        )

    return breakpoints


def _legacy_object_without_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(
                f"Invalid legacy short-term JSON: duplicate key {key!r}"
            )
        result[key] = value
    return result


def _reject_unknown_legacy_keys(
    record: dict[object, object],
    *,
    allowed: frozenset[str],
    prefix: str,
) -> None:
    unknown = sorted(str(key) for key in set(record) - allowed)
    if unknown:
        raise ValueError(
            f"{prefix}: unknown keys: {', '.join(unknown)}"
        )


def _required_legacy_field(
    record: dict[object, object],
    field_name: str,
    prefix: str,
) -> object:
    if field_name not in record:
        raise ValueError(f"{prefix}: missing {field_name}")
    return record[field_name]


def _validate_legacy_timestamp(value: object, *, prefix: str) -> None:
    try:
        _parse_aware_iso_timestamp(value, field_name="created_at")
    except ValueError as error:
        raise ValueError(f"{prefix}: {error}") from error
