"""Task startup specifications and retained runtime records."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..capabilities.lease import CapabilitySession
from .Task import Task
from .TaskEntity import TaskType
from .harness.Harness import Harness


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


@dataclass(slots=True)
class TaskRuntimeRecord:
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


__all__ = ["TaskRuntimeRecord", "TaskStartSpec"]
