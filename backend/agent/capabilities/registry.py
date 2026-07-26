"""Application-owned lifecycle registry for agent capability sessions."""

from __future__ import annotations

from threading import RLock

from ..skills.SkillGroupManager import SkillGroupManager
from ..tools.ToolGroupManager import ToolGroupManager
from .lease import CapabilitySession


class CapabilitySessionRegistry:
    """Create, track and close every CapabilitySession in one application."""

    def __init__(
        self,
        tool_group_manager: ToolGroupManager,
        skill_group_manager: SkillGroupManager,
    ):
        self._tool_group_manager = tool_group_manager
        self._skill_group_manager = skill_group_manager
        self._sessions: set[CapabilitySession] = set()
        self._closed = False
        self._lock = RLock()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def create(self) -> CapabilitySession:
        with self._lock:
            if self._closed:
                raise RuntimeError("CapabilitySessionRegistry is closed")
            session = CapabilitySession(
                self._tool_group_manager,
                self._skill_group_manager,
                on_close=self._discard,
            )
            self._sessions.add(session)
            return session

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sessions = tuple(self._sessions)
        first_error: BaseException | None = None
        for session in sessions:
            try:
                session.close()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        with self._lock:
            self._sessions.clear()
        if first_error is not None:
            raise first_error

    def _discard(self, session: CapabilitySession) -> None:
        with self._lock:
            self._sessions.discard(session)


__all__ = ["CapabilitySessionRegistry"]
