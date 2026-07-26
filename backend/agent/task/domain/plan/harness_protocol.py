"""
负责定义 Plan 应用服务识别 planning-capable Harness 所需的最窄结构协议。

应用层以 runtime-checkable PlanningHarness 检查 task 与 planning_runtime 属性；
注解边只在类型检查期引用领域类型，本模块不依赖具体 Harness、TaskManager 或 transport。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..task import Task
    from .service import PlanningRuntime


@runtime_checkable
class PlanningHarness(Protocol):
    """由支持 Plan 的 Harness 实现的结构化能力契约。"""

    @property
    def task(self) -> Task:
        ...

    @property
    def planning_runtime(self) -> PlanningRuntime:
        ...


__all__ = ["PlanningHarness"]
