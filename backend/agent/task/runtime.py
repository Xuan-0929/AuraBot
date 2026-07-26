"""
负责定义 Task runtime 启动 DTO、不可变查询快照及内部可变 record。

manager 与 lifecycle controller 围绕 _TaskRuntimeRecord 协作，并通过 snapshot_task
向应用层暴露副本；本模块不调度 Harness，也不发布事件或处理传输协议。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from ..capabilities.lease import CapabilitySession
from .domain import Task, TaskState, TaskType
from .harness.harness import Harness


@dataclass(frozen=True, slots=True)
class TaskStartSpec:
    task_name: str
    parent_session_id: str
    task_type: TaskType
    task_prompt: str
    workdir: str | None = None
    constraints: str | None = None
    skill_group_names: tuple[str, ...] = ()
    tool_group_names: tuple[str, ...] = ()
    timeout_ms: int | None = None


class InterruptTaskOutcome(Enum):
    NotFound = "NotFound"
    Interrupted = "Interrupted"
    AlreadyInterrupted = "AlreadyInterrupted"
    AlreadySettled = "AlreadySettled"


@dataclass(frozen=True, slots=True)
class InterruptTaskResult:
    task_name: str
    outcome: InterruptTaskOutcome
    task_id: int | None
    previous_state: TaskState | None
    state: TaskState | None


@dataclass(slots=True)
class _TaskRuntimeRecord:
    task: Task
    start_spec: TaskStartSpec
    harness: Harness | None = None
    runner: asyncio.Task[None] | None = None
    capabilities: CapabilitySession | None = None
    queue_position: int | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    settled_at: datetime | None = None
    result: Any = None
    error: dict[str, str] | None = None
    unsubscribe_state: Callable[[], None] | None = field(
        default=None,
        repr=False,
    )


@dataclass(frozen=True, slots=True)
class TaskFailure:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    task_id: int
    task_name: str
    task_type: TaskType
    state: TaskState
    queue_position: int | None
    created_at: datetime | None
    started_at: datetime | None
    settled_at: datetime | None
    result: Any = None
    error: TaskFailure | None = None


@dataclass(frozen=True, slots=True)
class TaskListSnapshot:
    running_count: int
    queued_count: int
    tasks: tuple[TaskSnapshot, ...]


def snapshot_task(record: _TaskRuntimeRecord) -> TaskSnapshot:
    error = (
        TaskFailure(**record.error)
        if record.error is not None
        else None
    )
    return TaskSnapshot(
        task_id=record.task.task_id,
        task_name=record.task.task_title,
        task_type=record.task.task_type,
        state=record.task.state,
        queue_position=record.queue_position,
        created_at=record.created_at,
        started_at=record.started_at,
        settled_at=record.settled_at,
        result=deepcopy(record.result),
        error=error,
    )


__all__ = [
    "InterruptTaskOutcome",
    "InterruptTaskResult",
    "TaskFailure",
    "TaskListSnapshot",
    "TaskSnapshot",
    "TaskStartSpec",
    "snapshot_task",
]
