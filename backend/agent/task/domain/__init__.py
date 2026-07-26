"""
提供 Task 领域层的稳定公开入口，并承载独立的 Plan 子领域。

上层 Harness 与 runtime 只从这里取得 Task 实体及生命周期类型；具体状态同步留在
task 模块，Plan 行为留在 plan 子包，本包不负责调度、事件投影或传输适配。
"""

from . import plan
from .task import Task, TaskInactiveError, TaskState, TaskType

__all__ = [
    "Task",
    "TaskInactiveError",
    "TaskState",
    "TaskType",
    "plan",
]
