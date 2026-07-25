from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class SegmentState(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class ShortTermMessage:
    id: str
    sequence: int
    role: str
    content: str
    source_id: str | None
    created_at: str


@dataclass(frozen=True)
class ShortTermBreakpoint:
    id: str
    message_sequence: int
    message_index: int
    topic: str | None
    reason: str | None
    created_at: str


@dataclass(frozen=True)
class BufferStats:
    message_count: int
    breakpoint_count: int
    char_count: int


@dataclass(frozen=True)
class SummaryRequest:
    segment_id: str
    profile_id: str
    source_type: str
    breakpoint_id: str
    topic: str | None
    reason: str | None
    messages: tuple[ShortTermMessage, ...]
    state: SegmentState
    attempt_count: int
    created_at: str
    lease_expires_at: str | None = None

    def __post_init__(self) -> None:
        messages = tuple(self.messages)
        if not all(isinstance(message, ShortTermMessage) for message in messages):
            raise TypeError("messages must contain only ShortTermMessage instances")
        object.__setattr__(self, "messages", messages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "profile_id": self.profile_id,
            "source_type": self.source_type,
            "breakpoint_id": self.breakpoint_id,
            "topic": self.topic,
            "reason": self.reason,
            "messages": [asdict(message) for message in self.messages],
            "content": "\n".join(
                f"{message.role}: {message.content}" for message in self.messages
            ),
            "message_count": len(self.messages),
            "state": self.state.value,
            "attempt_count": self.attempt_count,
            "created_at": self.created_at,
            "lease_expires_at": self.lease_expires_at,
            "handoff": "chat_agent_summary_required",
        }
