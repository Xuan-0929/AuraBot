"""
提供 Task 应用能力的稳定门面，组合领域实体与 runtime facade 的公开类型。

应用层通过这里创建、查询和中断 Task；领域实现由 domain 提供，runtime 生命周期
暂由现有平铺模块提供并将在后续迁移，本模块不导出内部 record、scheduler 或 controller。
"""

from .domain import Task, TaskInactiveError, TaskState, TaskType
from .manager import (
    DuplicateTaskNameError,
    TaskManager,
    TaskManagerClosedError,
)
from .runtime import (
    InterruptTaskOutcome,
    InterruptTaskResult,
    TaskListSnapshot,
    TaskSnapshot,
    TaskStartSpec,
)

__all__ = [
    "DuplicateTaskNameError",
    "InterruptTaskOutcome",
    "InterruptTaskResult",
    "Task",
    "TaskInactiveError",
    "TaskListSnapshot",
    "TaskManager",
    "TaskManagerClosedError",
    "TaskSnapshot",
    "TaskStartSpec",
    "TaskState",
    "TaskType",
]
