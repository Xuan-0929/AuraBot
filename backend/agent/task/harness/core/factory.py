"""
负责按 TaskType 选择 builder，并组装 Task-local Harness。

runtime 提供 Task、LLM、工具 gateway、adaptor registry 与 CapabilitySession 后，factory
构造对应执行策略；本模块不运行 agent loop，也不拥有 Task 生命周期或 Plan 状态。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from ....llm_client import LLMClient
from ....capabilities.lease import CapabilitySession
from ....tools.core.gateway import ToolCallGateway
from ...domain import Task, TaskType
from .base import Harness
from ..strategies.plan_and_solve import PlanAndSolveHarness
from ..strategies.react import ReActHarness
from ..adaptors.registry import AdaptorRegistry


ToolCallGatewayFactory = Callable[[Task], ToolCallGateway]


@dataclass(frozen=True, slots=True)
class HarnessBuildContext:
    task: Task
    task_llm_client: LLMClient
    tool_call_gateway: ToolCallGateway | None
    adaptor_registry: AdaptorRegistry
    capability_session: CapabilitySession | None


HarnessBuilder = Callable[[HarnessBuildContext], Harness]


def _build_react(context: HarnessBuildContext) -> Harness:
    return ReActHarness(
        task=context.task,
        llm_client=context.task_llm_client,
        tool_call_gateway=context.tool_call_gateway,
        adaptor_registry=context.adaptor_registry,
        capability_session=context.capability_session,
    )


def _build_plan_and_solve(context: HarnessBuildContext) -> Harness:
    return PlanAndSolveHarness(
        task=context.task,
        llm_client=context.task_llm_client,
        tool_call_gateway=context.tool_call_gateway,
        adaptor_registry=context.adaptor_registry,
        capability_session=context.capability_session,
    )


class HarnessFactory:
    """Build task-local Harnesses through TaskType-specific builders."""

    DEFAULT_HARNESS_BUILDERS: Mapping[TaskType, HarnessBuilder] = {
        TaskType.ReAct: _build_react,
        TaskType.PlanAndSolve: _build_plan_and_solve,
    }

    def __init__(
        self,
        *,
        task_llm_client: LLMClient,
        harness_builders: Mapping[TaskType, HarnessBuilder] | None = None,
        adaptor_registry: AdaptorRegistry | None = None,
        tool_call_gateway_factory: ToolCallGatewayFactory | None = None,
    ):
        if task_llm_client is None:
            raise TypeError("task_llm_client must be provided")
        self._task_llm_client = task_llm_client
        self._adaptor_registry = (
            adaptor_registry
            if adaptor_registry is not None
            else AdaptorRegistry()
        )
        if (
            tool_call_gateway_factory is not None
            and not callable(tool_call_gateway_factory)
        ):
            raise TypeError("tool_call_gateway_factory must be callable")
        self._tool_call_gateway_factory = tool_call_gateway_factory
        self._harness_builders = dict(
            self.DEFAULT_HARNESS_BUILDERS
            if harness_builders is None
            else harness_builders
        )
        for task_type, builder in self._harness_builders.items():
            self._validate_registration(task_type, builder)

    @property
    def task_llm_client(self) -> LLMClient:
        return self._task_llm_client

    @property
    def adaptor_registry(self) -> AdaptorRegistry:
        return self._adaptor_registry

    def create(
        self,
        task: Task,
        *,
        capability_session: CapabilitySession | None = None,
    ) -> Harness:
        if not isinstance(task, Task):
            raise TypeError("task must be a Task")
        if not isinstance(task.task_type, TaskType):
            raise TypeError("task.task_type must be a TaskType")
        try:
            builder = self._harness_builders[task.task_type]
        except KeyError as exc:
            raise ValueError(
                f"No harness registered for task type: {task.task_type.value}"
            ) from exc

        gateway = (
            self._tool_call_gateway_factory(task)
            if self._tool_call_gateway_factory is not None
            else None
        )
        harness = builder(
            HarnessBuildContext(
                task=task,
                task_llm_client=self._task_llm_client,
                tool_call_gateway=gateway,
                adaptor_registry=self._adaptor_registry,
                capability_session=capability_session,
            )
        )
        if not isinstance(harness, Harness):
            raise TypeError("harness builder must return Harness")
        if harness.task is not task:
            raise ValueError("harness builder must preserve the Task instance")
        return harness

    def register(
        self,
        task_type: TaskType,
        builder: HarnessBuilder,
        *,
        replace: bool = False,
    ) -> None:
        self._validate_registration(task_type, builder)
        if task_type in self._harness_builders and not replace:
            raise ValueError(
                f"Harness already registered for task type: {task_type.value}"
            )
        self._harness_builders[task_type] = builder

    def registered_task_types(self) -> tuple[TaskType, ...]:
        return tuple(self._harness_builders)

    @staticmethod
    def _validate_registration(
        task_type: TaskType,
        builder: HarnessBuilder,
    ) -> None:
        if not isinstance(task_type, TaskType):
            raise TypeError("task_type must be a TaskType")
        if not callable(builder):
            raise TypeError("harness builder must be callable")


__all__ = [
    "HarnessBuildContext",
    "HarnessBuilder",
    "HarnessFactory",
    "ToolCallGatewayFactory",
]
