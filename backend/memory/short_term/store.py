from __future__ import annotations

from typing import Protocol

from .models import (
    BufferStats,
    ShortTermBreakpoint,
    ShortTermMessage,
    SummaryRequest,
)


class ShortTermStore(Protocol):
    def close(self) -> None:
        raise NotImplementedError

    def append_message(
        self,
        *,
        profile_id: str,
        source_type: str,
        role: str,
        content: str,
        source_id: str | None,
    ) -> ShortTermMessage:
        raise NotImplementedError

    def add_breakpoint(
        self,
        *,
        profile_id: str,
        source_type: str,
        topic: str | None,
        reason: str | None,
    ) -> ShortTermBreakpoint | None:
        raise NotImplementedError

    def list_messages(
        self,
        profile_id: str,
        source_type: str,
    ) -> list[ShortTermMessage]:
        raise NotImplementedError

    def list_breakpoints(
        self,
        profile_id: str,
        source_type: str,
    ) -> list[ShortTermBreakpoint]:
        raise NotImplementedError

    def get_stats(self, profile_id: str, source_type: str) -> BufferStats:
        raise NotImplementedError

    def prepare_oldest_segment(
        self,
        *,
        profile_id: str,
        source_type: str,
    ) -> SummaryRequest | None:
        raise NotImplementedError

    def claim_next_segment(
        self,
        *,
        profile_id: str,
        source_type: str,
        owner: str,
        lease_seconds: int,
        now: str | None = None,
    ) -> SummaryRequest | None:
        raise NotImplementedError

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
        raise NotImplementedError

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
        raise NotImplementedError

    def recover_expired_leases(
        self,
        *,
        profile_id: str,
        source_type: str,
        now: str | None = None,
    ) -> int:
        raise NotImplementedError

    def requeue_failed_segment(
        self,
        *,
        profile_id: str,
        source_type: str,
        segment_id: str,
    ) -> SummaryRequest:
        raise NotImplementedError
