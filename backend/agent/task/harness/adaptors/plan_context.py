"""
负责把可信 Task 名称注入 Plan MCP 调用的私有 metadata。

工具执行管线传入已解析调用后，本 adaptor 复制 metadata 并写入 aurabot namespace；
它依赖 Task 领域实体，但不修改模型参数、不调用 MCP，也不管理 Plan 生命周期。
"""

from __future__ import annotations

from ....tools.core.model import ResolvedToolCall
from ...domain import Task
from .base import Adaptor, AdaptorRouteError


class PlanContextAdaptor(Adaptor):
    """Add `_meta.aurabot.task_name` without changing model arguments."""

    def __init__(self, task: Task):
        if not isinstance(task, Task):
            raise TypeError("task must be a Task")
        self._task = task

    def adapt(self, entity: ResolvedToolCall) -> ResolvedToolCall:
        if not isinstance(entity, ResolvedToolCall):
            raise TypeError("entity must be a ResolvedToolCall")

        task_name = self._task.task_title
        if (
            not isinstance(task_name, str)
            or not task_name
            or task_name != task_name.strip()
        ):
            raise AdaptorRouteError(
                "PlanContextAdaptor requires a non-empty trimmed Task title"
            )

        metadata = dict(entity.mcp_meta)
        existing_namespace = metadata.get("aurabot", {})
        if not isinstance(existing_namespace, dict):
            raise AdaptorRouteError(
                "MCP metadata namespace 'aurabot' must be an object"
            )
        metadata["aurabot"] = {
            **existing_namespace,
            "task_name": task_name,
        }
        return ResolvedToolCall(
            tool=entity.tool,
            arguments=entity.arguments,
            call_id=entity.call_id,
            mcp_meta=metadata,
        )


__all__ = ["PlanContextAdaptor"]
