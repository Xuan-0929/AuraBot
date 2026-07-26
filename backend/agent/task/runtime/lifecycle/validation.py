"""
负责校验 TaskManager 配置与公开 TaskStartSpec 的输入约束。

准入层在创建领域 Task 前调用这些纯校验函数，失败时返回稳定的 Python 异常；
本模块不创建 runtime record、不调度 Harness，也不执行传输层数据转换。
"""

import re
from collections.abc import Callable

from ...domain import TaskType
from ..records import TaskStartSpec

_TASK_NAME_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]")


def validate_manager_options(
    *,
    max_running: int,
    default_timeout_ms: int,
    max_timeout_ms: int,
    retention_seconds: float,
    capability_session_factory: Callable | None,
) -> None:
    if (
        not isinstance(max_running, int)
        or isinstance(max_running, bool)
        or max_running < 1
    ):
        raise ValueError("max_running must be a positive integer")
    if (
        not isinstance(default_timeout_ms, int)
        or isinstance(default_timeout_ms, bool)
        or default_timeout_ms < 1
    ):
        raise ValueError("default_timeout_ms must be positive")
    if (
        not isinstance(max_timeout_ms, int)
        or isinstance(max_timeout_ms, bool)
        or max_timeout_ms < default_timeout_ms
    ):
        raise ValueError(
            "max_timeout_ms must be at least default_timeout_ms"
        )
    if retention_seconds < 0:
        raise ValueError("retention_seconds must be non-negative")
    if (
        capability_session_factory is not None
        and not callable(capability_session_factory)
    ):
        raise TypeError("capability_session_factory must be callable")


def validate_start_spec(spec: TaskStartSpec) -> None:
    if not isinstance(spec, TaskStartSpec):
        raise TypeError("spec must be a TaskStartSpec")
    _validate_task_name(spec.task_name)
    if not isinstance(spec.task_type, TaskType):
        raise TypeError("task_type must be a TaskType")
    for field_name in ("parent_session_id", "task_prompt"):
        value = getattr(spec, field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")
    for names in (spec.tool_group_names, spec.skill_group_names):
        if not isinstance(names, tuple):
            raise TypeError("capability group names must be tuples")
        if len(set(names)) != len(names):
            raise ValueError("capability group names must be unique")
        if any(
            not isinstance(name, str)
            or not name
            or name != name.strip()
            for name in names
        ):
            raise ValueError(
                "capability group names must be non-empty trimmed strings"
            )


def _validate_task_name(task_name: str) -> None:
    if (
        not isinstance(task_name, str)
        or not 1 <= len(task_name) <= 128
        or task_name != task_name.strip()
        or "/" in task_name
        or _TASK_NAME_CONTROL_PATTERN.search(task_name) is not None
    ):
        raise ValueError(
            "task_name must be 1-128 trimmed characters without '/', NUL or controls"
        )


__all__ = ["validate_manager_options", "validate_start_spec"]
