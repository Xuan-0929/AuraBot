from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..stores.short_term_sqlite import SQLiteShortTermStore
from .models import ShortTermBreakpoint, ShortTermMessage, SummaryRequest
from .policy import ShortTermPolicy
from .store import ShortTermStore


class ShortTermMemoryBuffer:
    """Compatibility facade for reliable short-term memory storage."""

    def __init__(
        self,
        *,
        profile_id: str,
        source_type: str,
        max_breakpoints: int = 4,
        max_chars: int = 3000,
        persistence_path: str | Path | None = None,
        store: ShortTermStore | None = None,
        max_summary_attempts: int = 3,
        service: Any | None = None,
        max_messages: int | None = None,
    ):
        _require_nonblank("profile_id", profile_id)
        _require_nonblank("source_type", source_type)
        policy = ShortTermPolicy(
            max_breakpoints=max_breakpoints,
            max_chars=max_chars,
        )
        _require_positive_int("max_summary_attempts", max_summary_attempts)
        if store is not None and persistence_path is not None:
            raise ValueError(
                "store and persistence_path cannot both be provided"
            )

        self.profile_id = profile_id
        self.source_type = source_type
        self.max_breakpoints = policy.max_breakpoints
        self.max_chars = policy.max_chars
        self.max_summary_attempts = max_summary_attempts
        self.persistence_path = (
            Path(persistence_path) if persistence_path is not None else None
        )
        self._policy = policy
        self._owns_store = store is None
        self._closed = False
        self._store: ShortTermStore = (
            SQLiteShortTermStore(
                self.persistence_path
                if self.persistence_path is not None
                else ":memory:"
            )
            if store is None
            else store
        )

    @property
    def messages(self) -> list[ShortTermMessage]:
        return self._store.list_messages(self.profile_id, self.source_type)

    @property
    def breakpoints(self) -> list[ShortTermBreakpoint]:
        return self._store.list_breakpoints(
            self.profile_id,
            self.source_type,
        )

    def add(
        self,
        *,
        role: str,
        content: str,
        source_id: str | None = None,
    ) -> dict[str, Any] | None:
        return self.add_message(
            role=role,
            content=content,
            source_id=source_id,
        )

    def add_message(
        self,
        *,
        role: str,
        content: str,
        source_id: str | None = None,
    ) -> dict[str, Any] | None:
        _require_nonblank("role", role)
        _require_nonblank("content", content)
        _require_optional_string("source_id", source_id)
        self._store.append_message(
            profile_id=self.profile_id,
            source_type=self.source_type,
            role=role,
            content=content,
            source_id=source_id,
        )
        return self._prepare_if_needed()

    def mark_breakpoint(
        self,
        *,
        topic: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any] | None:
        _require_optional_string("topic", topic)
        _require_optional_string("reason", reason)
        breakpoint = self._store.add_breakpoint(
            profile_id=self.profile_id,
            source_type=self.source_type,
            topic=topic,
            reason=reason,
        )
        if breakpoint is None:
            return None
        return self._prepare_if_needed()

    def pop_oldest_segment_for_summary(self) -> dict[str, Any] | None:
        request = self._store.prepare_oldest_segment(
            profile_id=self.profile_id,
            source_type=self.source_type,
        )
        return request.to_dict() if request is not None else None

    def claim_next_summary(
        self,
        *,
        owner: str,
        lease_seconds: int,
        now: str | None = None,
    ) -> SummaryRequest | None:
        return self._store.claim_next_segment(
            profile_id=self.profile_id,
            source_type=self.source_type,
            owner=owner,
            lease_seconds=lease_seconds,
            now=now,
        )

    def acknowledge_summary(
        self,
        *,
        segment_id: str,
        owner: str,
        outcome: str,
        episode_id: str | None,
        memory_ids: list[str],
        reason: str | None,
    ) -> SummaryRequest:
        return self._store.acknowledge_segment(
            profile_id=self.profile_id,
            segment_id=segment_id,
            owner=owner,
            outcome=outcome,
            episode_id=episode_id,
            memory_ids=memory_ids,
            reason=reason,
        )

    def release_summary(
        self,
        *,
        segment_id: str,
        owner: str,
        error: str,
        max_attempts: int | None = None,
    ) -> SummaryRequest:
        effective_max_attempts = (
            self.max_summary_attempts
            if max_attempts is None
            else max_attempts
        )
        _require_positive_int("max_attempts", effective_max_attempts)
        return self._store.release_segment(
            profile_id=self.profile_id,
            segment_id=segment_id,
            owner=owner,
            error=error,
            max_attempts=effective_max_attempts,
        )

    def recover_expired_summaries(
        self,
        *,
        now: str | None = None,
    ) -> int:
        return self._store.recover_expired_leases(
            profile_id=self.profile_id,
            source_type=self.source_type,
            now=now,
        )

    def requeue_failed_summary(
        self,
        *,
        segment_id: str,
    ) -> SummaryRequest:
        return self._store.requeue_failed_segment(
            profile_id=self.profile_id,
            segment_id=segment_id,
        )

    def close(self) -> None:
        if self._owns_store and not self._closed:
            self._store.close()
            self._closed = True

    def _prepare_if_needed(self) -> dict[str, Any] | None:
        stats = self._store.get_stats(
            self.profile_id,
            self.source_type,
        )
        if not self._policy.should_prepare(stats):
            return None
        return self.pop_oldest_segment_for_summary()


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _require_nonblank(field_name: str, value: object) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _require_optional_string(
    field_name: str,
    value: object | None,
) -> None:
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or None")


def _require_positive_int(field_name: str, value: object) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
