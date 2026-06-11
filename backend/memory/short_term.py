from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass
class ShortTermMessage:
    role: str
    content: str
    source_id: str | None = None
    created_at: str = ""


@dataclass
class ShortTermBreakpoint:
    id: str
    message_index: int
    topic: str | None
    reason: str | None
    created_at: str


class ShortTermMemoryBuffer:
    """PRD-v2 short-term memory buffer.

    The buffer tracks chat-agent context and topic breakpoints. When breakpoint
    or context limits are reached, it returns the oldest complete segment for
    the main agent to summarize. It does not write long-term memory directly.
    """

    def __init__(
        self,
        *,
        profile_id: str,
        source_type: str,
        max_breakpoints: int = 4,
        max_chars: int = 3000,
        persistence_path: str | Path | None = None,
        service: Any | None = None,
        max_messages: int | None = None,
    ):
        self.profile_id = profile_id
        self.source_type = source_type
        self.max_breakpoints = max_breakpoints
        self.max_chars = max_chars
        self.persistence_path = Path(persistence_path) if persistence_path else None
        self.messages: list[ShortTermMessage] = []
        self.breakpoints: list[ShortTermBreakpoint] = []
        self._load()

    def add(self, *, role: str, content: str, source_id: str | None = None) -> dict[str, Any] | None:
        return self.add_message(role=role, content=content, source_id=source_id)

    def add_message(
        self,
        *,
        role: str,
        content: str,
        source_id: str | None = None,
    ) -> dict[str, Any] | None:
        self.messages.append(
            ShortTermMessage(
                role=role,
                content=content,
                source_id=source_id,
                created_at=now_iso(),
            )
        )
        self._persist()

        if self._context_too_large():
            return self.pop_oldest_segment_for_summary()

        return None

    def mark_breakpoint(
        self,
        *,
        topic: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any] | None:
        if not self.messages:
            return None

        message_index = len(self.messages)
        if self.breakpoints and self.breakpoints[-1].message_index == message_index:
            return None

        self.breakpoints.append(
            ShortTermBreakpoint(
                id=f"breakpoint_{uuid4().hex}",
                message_index=message_index,
                topic=topic,
                reason=reason,
                created_at=now_iso(),
            )
        )
        self._persist()

        if len(self.breakpoints) >= self.max_breakpoints:
            return self.pop_oldest_segment_for_summary()

        return None

    def pop_oldest_segment_for_summary(self) -> dict[str, Any] | None:
        if not self.breakpoints:
            return None

        breakpoint = self.breakpoints.pop(0)
        segment_messages = self.messages[: breakpoint.message_index]
        self.messages = self.messages[breakpoint.message_index :]

        for remaining in self.breakpoints:
            remaining.message_index -= breakpoint.message_index

        segment = {
            "profile_id": self.profile_id,
            "source_type": self.source_type,
            "breakpoint_id": breakpoint.id,
            "topic": breakpoint.topic,
            "reason": breakpoint.reason,
            "message_count": len(segment_messages),
            "messages": [asdict(message) for message in segment_messages],
            "content": "\n".join(
                f"{message.role}: {message.content}" for message in segment_messages
            ),
            "created_at": breakpoint.created_at,
            "handoff": "chat_agent_summary_required",
        }
        self._persist()
        return segment

    def _context_too_large(self) -> bool:
        return sum(len(message.content) for message in self.messages) >= self.max_chars

    def _persist(self) -> None:
        if not self.persistence_path:
            return

        self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "profile_id": self.profile_id,
            "source_type": self.source_type,
            "messages": [asdict(message) for message in self.messages],
            "breakpoints": [asdict(breakpoint) for breakpoint in self.breakpoints],
        }
        self.persistence_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load(self) -> None:
        if not self.persistence_path or not self.persistence_path.exists():
            return

        payload = json.loads(self.persistence_path.read_text(encoding="utf-8"))
        if payload.get("profile_id") != self.profile_id:
            return

        self.messages = [
            ShortTermMessage(**message)
            for message in payload.get("messages", [])
        ]
        self.breakpoints = [
            ShortTermBreakpoint(**breakpoint)
            for breakpoint in payload.get("breakpoints", [])
        ]


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
