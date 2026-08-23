"""
定义版本化 Task REST/SSE 的 camelCase 公共 DTO。

这些 Pydantic 模型只映射 AuraBot 产品记录与业务事件，明确排除 Pi session/path、raw
message、thinking、Tool 细节、stderr 和 RPC id；TaskManager 与 Store 保持 Python 模型。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator

from ...task.models import (
    CreateTaskCommand,
    TaskErrorCode,
    TaskEvent,
    TaskPage,
    TaskRecord,
    TaskStatus,
)
from .errors import CamelModel


class CreateTaskRequest(CamelModel):
    title: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=200_000)
    working_directory: Path
    timeout_seconds: int | None = Field(
        default=None, ge=1, le=86_400, strict=True
    )
    rerun_of_task_id: UUID | None = None

    @field_validator("working_directory")
    @classmethod
    def require_absolute_working_directory(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("workingDirectory must be an absolute path")
        return value

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def reject_explicit_null_timeout(cls, value: object) -> object:
        if value is None:
            raise ValueError("timeoutSeconds cannot be null")
        return value

    def to_command(self) -> CreateTaskCommand:
        return CreateTaskCommand(
            title=self.title,
            prompt=self.prompt,
            working_directory=self.working_directory,
            timeout_seconds=self.timeout_seconds,
            rerun_of_task_id=self.rerun_of_task_id,
        )


class TaskCommandRequest(CamelModel):
    expected_version: int = Field(ge=1)


class TaskResponse(CamelModel):
    id: UUID
    row_id: int
    title: str
    prompt: str
    working_directory: Path
    status: TaskStatus
    timeout_seconds: int
    active_runtime_ms: int
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

    @classmethod
    def from_record(cls, task: TaskRecord) -> "TaskResponse":
        return cls(
            id=task.id,
            row_id=task.row_id,
            title=task.title,
            prompt=task.prompt,
            working_directory=task.working_directory,
            status=task.status,
            timeout_seconds=task.timeout_seconds,
            active_runtime_ms=task.active_runtime_ms,
            result_text=task.result_text,
            error_code=task.error_code,
            error_message=task.error_message,
            pause_reason=task.pause_reason,
            rerun_of_task_id=task.rerun_of_task_id,
            version=task.version,
            created_at=task.created_at,
            updated_at=task.updated_at,
            started_at=task.started_at,
            finished_at=task.finished_at,
            deleted_at=task.deleted_at,
        )


class TaskPageResponse(CamelModel):
    items: tuple[TaskResponse, ...]
    next_cursor: int | None

    @classmethod
    def from_page(cls, page: TaskPage) -> "TaskPageResponse":
        return cls(
            items=tuple(TaskResponse.from_record(item) for item in page.items),
            next_cursor=page.next_cursor,
        )


class TaskEventResponse(CamelModel):
    event_id: int
    task_sequence: int
    task_id: UUID
    type: str
    occurred_at: datetime
    payload: dict[str, Any]

    @classmethod
    def from_event(cls, event: TaskEvent) -> "TaskEventResponse":
        return cls(
            event_id=event.event_id,
            task_sequence=event.task_sequence,
            task_id=event.task_id,
            type=event.type,
            occurred_at=event.occurred_at,
            payload=dict(event.payload),
        )


__all__ = [
    "CreateTaskRequest",
    "TaskCommandRequest",
    "TaskEventResponse",
    "TaskPageResponse",
    "TaskResponse",
]
