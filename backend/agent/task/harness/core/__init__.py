"""
汇集 Harness 的抽象基类、builder factory 与共享工具执行基础设施。

具体 strategies 继承 Harness，runtime 通过 HarnessFactory 组装 task-local 实例；
本包不实现 ReAct/Plan-and-Solve 算法，也不负责 Task 调度或 Plan 领域规则。
"""

from .base import (
    Harness,
    HarnessInterruptedError,
    HarnessStateTransitionError,
)
from .factory import HarnessBuildContext, HarnessFactory


__all__ = [
    "Harness",
    "HarnessBuildContext",
    "HarnessFactory",
    "HarnessInterruptedError",
    "HarnessStateTransitionError",
]
