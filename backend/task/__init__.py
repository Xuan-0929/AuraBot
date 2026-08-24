"""提供 AuraBot 第二轮 Python Pi Task Control Plane 的公开包边界。

具体产品模型、持久化、调度、RPC、进程监督与编排分别位于子模块；Gateway 和未来
Main Agent 只从本入口取得稳定业务门面、DTO 所需模型与明确 domain exceptions。
"""

from .manager import TaskManager
from .models import (
    CreateTaskCommand,
    TaskCommandError,
    TaskErrorCode,
    TaskEvent,
    TaskNotFound,
    TaskPage,
    TaskQuestion,
    TaskRecord,
    TaskRequestError,
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
    "TaskQuestion",
    "TaskRecord",
    "TaskRequestError",
    "TaskRuntimeUnavailable",
    "TaskStateConflict",
    "TaskStatus",
    "TaskVersionConflict",
]
