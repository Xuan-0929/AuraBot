"""
提供 Task runtime 的应用级 facade 与不可变输入/查询 DTO。

调用方通过 TaskManager 启动、观察和中断 Task，manager 在内部组合 registry、
scheduler、lifecycle controller 与 events；本包不导出内部 record 或控制器实现。
"""

from .lifecycle.errors import DuplicateTaskNameError, TaskManagerClosedError
from .manager import TaskManager
from .records import (
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
    "TaskListSnapshot",
    "TaskManager",
    "TaskManagerClosedError",
    "TaskSnapshot",
    "TaskStartSpec",
]
