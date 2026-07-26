"""
负责由应用进程统一拥有 SkillGroup 与 ToolGroup manager 的生命周期。

BackendRuntime 启动时构造 registry，Task 准入通过它创建独立 CapabilitySession，
关闭时按所有权顺序释放共享 manager；本模块不选择 Harness 或修改 Task 状态。
"""

from __future__ import annotations

from threading import RLock

from ..skills.manager import SkillGroupManager
from ..tools.manager import ToolGroupManager
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
