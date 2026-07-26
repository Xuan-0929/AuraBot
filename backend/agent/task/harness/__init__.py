"""
提供 Task Harness 层的稳定公开入口，并隐藏内部执行管线与提示词细节。

runtime 通过 HarnessFactory 构造具体策略，策略再使用 core、adaptors 与 prompting
完成 agent loop；本包不导出 Plan 领域实现，也不拥有 Task 调度和生命周期控制。
"""

from .core import (
    Harness,
    HarnessBuildContext,
    HarnessFactory,
    HarnessInterruptedError,
    HarnessStateTransitionError,
)
from .strategies import PlanAndSolveHarness, ReActHarness


__all__ = [
    "Harness",
    "HarnessBuildContext",
    "HarnessFactory",
    "HarnessInterruptedError",
    "HarnessStateTransitionError",
    "PlanAndSolveHarness",
    "ReActHarness",
]
