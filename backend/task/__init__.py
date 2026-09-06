"""公开 AuraBot Task runtime 的 Manager 门面、产品模型和领域错误。

Main Agent 从本包取得 Manager 与产品模型，Gateway 只消费状态快照；Store、process 和 RPC 是 Manager 的
内部实现，不通过包边界暴露持久化或 Pi 协议细节。
"""

from .manager import TaskManager
from .models import (
    CreateTaskCommand,
    TaskCommandError,
    TaskErrorCode,
    TaskEvent,
    TaskNotFound,
    TaskPage,
    TaskRecord,
    TaskRuntimeUnavailable,
    TaskStateConflict,
    TaskStatus,
    TaskVersionConflict,
)

__all__ = [
    "CreateTaskCommand",
    "TaskCommandError",
    "TaskErrorCode",
    "TaskEvent",
    "TaskManager",
    "TaskNotFound",
    "TaskPage",
    "TaskRecord",
    "TaskRuntimeUnavailable",
    "TaskStateConflict",
    "TaskStatus",
    "TaskVersionConflict",
]
