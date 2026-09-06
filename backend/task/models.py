"""
定义 AuraBot Task 产品模型、错误类别和纯状态转换规则。

本模块不访问 SQLite、Pi RPC 或 FastAPI；Store 负责保存这些值，TaskManager 负责
编排转换和 workspace 解析。CreateTaskCommand 仅携带 workspace/owner/idempotency 业务
标识，所有调用方必须通过 Manager 验证同一张产品状态图。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID


class TaskStatus(StrEnum):
    """Public product lifecycle of one Task execution."""

    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskErrorCode(StrEnum):
    """Stable public failure categories; diagnostics remain in structured logs."""

    SPAWN_FAILED = "spawn_failed"
    STARTUP_TIMEOUT = "startup_timeout"
    PROMPT_REJECTED = "prompt_rejected"
    RESOURCE_LOAD_FAILED = "resource_load_failed"
    PROTOCOL_ERROR = "protocol_error"
    UNEXPECTED_EXIT = "unexpected_exit"
    MODEL_ERROR = "model_error"
    TIMEOUT = "timeout"
    SESSION_MISSING = "session_missing"
    RESULT_UNAVAILABLE = "result_unavailable"
    INTERNAL_ERROR = "internal_error"


TERMINAL_TASK_STATUSES = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)

_ALLOWED_STATUS_TRANSITIONS: Mapping[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.QUEUED: frozenset(
        {TaskStatus.RUNNING, TaskStatus.PAUSED, TaskStatus.CANCELLED}
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.WAITING,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.PAUSED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.WAITING: frozenset(
        {
            TaskStatus.QUEUED,
            TaskStatus.PAUSED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.PAUSED: frozenset(
        {TaskStatus.QUEUED, TaskStatus.WAITING, TaskStatus.CANCELLED}
    ),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


class TaskRuntimeError(RuntimeError):
    """Base class for stable Task Control Plane failures."""


class TaskRuntimeUnavailable(RuntimeError):
    """Raised when startup, shutdown or fail-stop prevents Task admission."""


class TaskCommandError(TaskRuntimeError):
    """Raised when an internal Task command violates the Control Plane contract."""


class TaskNotFound(TaskRuntimeError):
    """Raised when a Task is absent or hidden by logical deletion."""


class TaskStateConflict(TaskRuntimeError):
    """Raised when a command is invalid for the current product status."""

    def __init__(self, current: TaskStatus, target: TaskStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Task cannot transition from {current.value} to {target.value}.")


class TaskVersionConflict(TaskRuntimeError):
    """Raised when compare-and-swap observes a stale expected version."""

    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"Task version conflict: expected {expected}, actual {actual}.")


def validate_status_transition(current: TaskStatus, target: TaskStatus) -> TaskStatus:
    """Return the target for an allowed/idempotent transition, otherwise raise."""

    if current == target or target in _ALLOWED_STATUS_TRANSITIONS[current]:
        return target
    raise TaskStateConflict(current, target)


@dataclass(frozen=True, slots=True)
class CreateTaskCommand:
    """Validated business input used by TaskManager and Store."""

    title: str
    prompt: str
    workspace_id: str
    owner_profile_id: str
    idempotency_key: str
    timeout_seconds: int | None = None
    rerun_of_task_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class TaskRecord:
    """持久化 Task 快照；问题只保留当前一次，历史由事件保存。"""

    id: UUID
    row_id: int
    title: str
    prompt: str
    working_directory: Path
    workspace_id: str
    owner_profile_id: str
    idempotency_key: str
    status: TaskStatus
    timeout_seconds: int
    active_runtime_ms: int
    question_id: UUID | None
    question_text: str | None
    question_options: tuple[str, ...]
    question_answer: str | None
    result_text: str | None
    error_code: TaskErrorCode | None
    error_message: str | None
    pause_reason: str | None
    rerun_of_task_id: UUID | None
    version: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    deleted_at: datetime | None


@dataclass(frozen=True, slots=True)
class TaskEvent:
    """Persisted business event envelope replayed by SSE."""

    event_id: int
    task_sequence: int
    task_id: UUID
    type: str
    occurred_at: datetime
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class TaskPage:
    """Cursor page ordered by monotonically increasing Task row id."""

    items: tuple[TaskRecord, ...]
    next_cursor: int | None


__all__ = [
    "CreateTaskCommand",
    "TERMINAL_TASK_STATUSES",
    "TaskErrorCode",
    "TaskCommandError",
    "TaskEvent",
    "TaskNotFound",
    "TaskPage",
    "TaskRecord",
    "TaskRuntimeError",
    "TaskRuntimeUnavailable",
    "TaskStateConflict",
    "TaskStatus",
    "TaskVersionConflict",
    "validate_status_transition",
]
