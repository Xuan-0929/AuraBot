"""
运行中的 Agent 对进程级共享能力对象所持有的生命周期租约。

请不要因为 Task 已经有 ``tool_groups`` 和 ``skill_groups`` 属性而删除本模块。
Task 上的这两个属性只是当前 Agent 可以使用哪些能力的授权快照，不记录这些共享对象
由哪个 Manager 创建、是否已经 acquire、应当在什么时候 release，也不应该让通用
Task 实体依赖 ToolGroupManager、SkillGroupManager 或引用计数协议。

``CapabilitySession`` 由完整的 Agent runtime 持有，而不是由 Task 领域实体持有。它把
一次运行过程中取得的 ToolGroup 和 SkillGroup 引用集中在同一个资源边界内，保证：

1. 同一个 runtime 对同名 capability 最多持有一个 Manager 引用；
2. 普通授权能力和 Harness 后续注入的必需能力（例如 Plan ToolGroup）使用同一份租约；
3. capability 部分获取成功、后续获取或 Harness 构造失败时可以统一回滚；
4. ``close()`` 幂等，并且某一类资源释放失败时仍会尝试释放其他资源；
5. 能力引用随 retained runtime 一起保留，直到 clear、保留期到期或 backend shutdown
   才归还 Manager，而不是在 Task 刚进入终态时过早销毁；
6. ToolGroup/SkillGroup 不是 Task-specific，未来 chat-agent 等其他 Agent runtime
   也可以复用同一套 lease 协议。

因此，本模块负责的是“共享 capability 的 acquire/release 所有权”，Task 属性负责的
是“能力授权与调用视图”。如果移除这层租约，就必须在 TaskManager、Harness 以及未来
其他 Agent runtime 中重复实现去重、回滚和释放逻辑，或者反过来污染 Task 实体。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from threading import RLock

from ..skills.skill_group import SkillGroup
from ..skills.manager import SkillGroupManager
from ..tools.tool_group import ToolGroup
from ..tools.manager import ToolGroupManager


class CapabilitySession:
    """Hold one reference to every capability used by a running agent.

    A TaskManager owns the session and closes it only when the retained runtime
    is removed. Harnesses may acquire additional required groups through the
    same session, so native and database-backed capabilities share one
    lifecycle.
    """

    def __init__(
        self,
        tool_group_manager: ToolGroupManager,
        skill_group_manager: SkillGroupManager,
        *,
        on_close: Callable[["CapabilitySession"], None] | None = None,
    ):
        if not isinstance(tool_group_manager, ToolGroupManager):
            raise TypeError("tool_group_manager must be a ToolGroupManager")
        if not isinstance(skill_group_manager, SkillGroupManager):
            raise TypeError("skill_group_manager must be a SkillGroupManager")
        self._tool_group_manager = tool_group_manager
        self._skill_group_manager = skill_group_manager
        self._tool_groups: dict[str, ToolGroup] = {}
        self._skill_groups: dict[str, SkillGroup] = {}
        self._closed = False
        self._on_close = on_close
        self._lock = RLock()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def acquire_tool_groups(
        self,
        names: Sequence[str],
    ) -> tuple[ToolGroup, ...]:
        requested = _normalize_names(names)
        with self._lock:
            self._require_open()
            missing = tuple(
                name for name in requested if name not in self._tool_groups
            )
            acquired = self._tool_group_manager.acquire(missing)
            self._tool_groups.update(
                (group.name, group) for group in acquired
            )
            return tuple(self._tool_groups[name] for name in requested)

    def acquire_skill_groups(
        self,
        names: Sequence[str],
    ) -> tuple[SkillGroup, ...]:
        requested = _normalize_names(names)
        with self._lock:
            self._require_open()
            missing = tuple(
                name for name in requested if name not in self._skill_groups
            )
            acquired = self._skill_group_manager.acquire(missing)
            self._skill_groups.update(
                (group.name, group) for group in acquired
            )
            return tuple(self._skill_groups[name] for name in requested)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            tool_groups = tuple(self._tool_groups.values())
            skill_groups = tuple(self._skill_groups.values())
            self._tool_groups.clear()
            self._skill_groups.clear()
            self._closed = True
        first_error: BaseException | None = None
        try:
            self._tool_group_manager.release(tool_groups)
        except BaseException as exc:
            first_error = exc
        try:
            self._skill_group_manager.release(skill_groups)
        except BaseException as exc:
            if first_error is None:
                first_error = exc
        try:
            if self._on_close is not None:
                self._on_close(self)
        except BaseException as exc:
            if first_error is None:
                first_error = exc
        if first_error is not None:
            raise first_error

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("CapabilitySession is closed")


def _normalize_names(names: Sequence[str]) -> tuple[str, ...]:
    if isinstance(names, (str, bytes)) or not isinstance(names, Sequence):
        raise TypeError("capability names must be a sequence")
    snapshot = tuple(dict.fromkeys(names))
    if any(
        not isinstance(name, str)
        or not name
        or name != name.strip()
        for name in snapshot
    ):
        raise ValueError("capability names must be non-empty trimmed strings")
    return snapshot


__all__ = ["CapabilitySession"]
