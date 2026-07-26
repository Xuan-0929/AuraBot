"""Strict public Task HTTP request and response models."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...agent.task.TaskEntity import TaskType
from ...agent.task.runtime import TaskStartSpec

_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]")


class GatewayModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateTaskBody(GatewayModel):
    task_name: str = Field(min_length=1, max_length=128)
    parent_session_id: str = Field(min_length=1)
    task_type: Literal["react", "plan_and_solve"]
    prompt: str = Field(min_length=1)
    workdir: str | None = None
    constraints: str | None = None
    skill_groups: list[str] = Field(default_factory=list)
    tool_groups: list[str] = Field(default_factory=list)
    timeout_ms: int | None = Field(default=None, ge=1, le=1_800_000)

    @field_validator("task_name")
    @classmethod
    def validate_task_name(cls, value: str) -> str:
        if (
            value != value.strip()
            or "/" in value
            or _CONTROL_PATTERN.search(value) is not None
        ):
            raise ValueError(
                "task_name must be trimmed and contain no slash or controls"
            )
        return value

    @field_validator("skill_groups", "tool_groups")
    @classmethod
    def validate_group_names(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("group names must be unique")
        if any(
            not isinstance(value, str)
            or not value
            or value != value.strip()
            for value in values
        ):
            raise ValueError("group names must be non-empty and trimmed")
        return values

    def to_spec(self) -> TaskStartSpec:
        task_type = {
            "react": TaskType.ReAct,
            "plan_and_solve": TaskType.PlanAndSolve,
        }[self.task_type]
        return TaskStartSpec(
            task_name=self.task_name,
            parent_session_id=self.parent_session_id,
            task_type=task_type,
            task_prompt=self.prompt,
            workdir=self.workdir,
            constraints=self.constraints,
            skill_group_names=tuple(self.skill_groups),
            tool_group_names=tuple(self.tool_groups),
            timeout_ms=self.timeout_ms,
        )


class TaskView(GatewayModel):
    task_id: int
    task_name: str
    task_type: Literal["react", "plan_and_solve", "goal"]
    state: Literal[
        "initializing",
        "thinking",
        "pushing",
        "planning",
        "concluding",
        "waiting",
        "completed",
        "failed",
        "interrupted",
    ]
    queue_position: int | None
    created_at: str | None
    started_at: str | None
    settled_at: str | None
    result: Any = None
    error: dict[str, str] | None = None


class TaskListResponse(GatewayModel):
    running_count: int
    queued_count: int
    tasks: list[TaskView]


__all__ = [
    "CreateTaskBody",
    "GatewayModel",
    "TaskListResponse",
    "TaskView",
]
