"""Shared Harness orchestration contract."""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any

from ...llm_client import ChatCompletionChoice, LLMClient, Message
from ...capabilities.lease import CapabilitySession
from ...integration.mcp import MCPClient
from ...tools.tool_group import ToolGroup
from ...tools.core.model import (
    ResolvedToolCall,
    ToolCall,
    ToolExecutionResult,
    ToolProtocolResponse,
)
from ...tools.core.gateway import ToolCallGateway
from ...tools.native import create_native_tool_groups
from ..model import Task, TaskState
from .adaptors.adaptor import Adaptor
from .adaptors.registry import AdaptorRegistry
from .required_tool_groups import inject_required_tool_groups
from .tool_execution_pipeline import ToolExecutionPipeline


class HarnessInterruptedError(asyncio.CancelledError):
    """Raised when an external interrupt wins a Task state race."""


class HarnessStateTransitionError(RuntimeError):
    """Raised when a Harness cannot commit its expected Task transition."""


class Harness(ABC):
    """One task-local agent loop backed by a shared LLM client."""

    required_tool_group_names: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        task: Task,
        llm_client: LLMClient,
        tool_call_gateway: ToolCallGateway | None = None,
        adaptor_registry: AdaptorRegistry | None = None,
        capability_session: CapabilitySession | None = None,
    ):
        self._task = task
        self._llm_client = llm_client
        gateway = (
            tool_call_gateway
            if tool_call_gateway is not None
            else ToolCallGateway(
                MCPClient(timeout_seconds=task.timeout_ms / 1000)
            )
        )
        registry = (
            adaptor_registry
            if adaptor_registry is not None
            else AdaptorRegistry()
        )
        if (
            capability_session is not None
            and not isinstance(capability_session, CapabilitySession)
        ):
            raise TypeError(
                "capability_session must be a CapabilitySession or None"
            )
        self._capability_session = capability_session
        self._tool_execution_pipeline = ToolExecutionPipeline(
            task=task,
            tool_call_gateway=gateway,
            adaptor_registry=registry,
        )
        self._install_required_tool_groups()

    @property
    def task(self) -> Task:
        return self._task

    @property
    def llm_client(self) -> LLMClient:
        return self._llm_client

    @property
    def tool_call_gateway(self) -> ToolCallGateway:
        return self.tool_execution_pipeline.tool_call_gateway

    @property
    def adaptor_registry(self) -> AdaptorRegistry:
        return self.tool_execution_pipeline.adaptor_registry

    @property
    def tool_execution_pipeline(self) -> ToolExecutionPipeline:
        return self._tool_execution_pipeline

    @property
    def capability_session(self) -> CapabilitySession | None:
        return self._capability_session

    @property
    def adaptors(self) -> Mapping[str, Adaptor]:
        """Return Task-local Adaptor instances built for current Tools."""

        return self.tool_execution_pipeline.adaptors

    @abstractmethod
    def build_system_prompt(self) -> str:
        """Build the system prompt for this Harness algorithm."""

        raise NotImplementedError(
            "Harness subclasses must implement build_system_prompt()"
        )

    def _install_required_tool_groups(self) -> None:
        """Install ToolGroups declared by the concrete Harness."""

        names = self.required_tool_group_names
        if (
            not isinstance(names, tuple)
            or any(not isinstance(name, str) or not name for name in names)
            or len(set(names)) != len(names)
        ):
            raise TypeError(
                "required_tool_group_names must be unique non-empty strings"
            )
        required = (
            self.capability_session.acquire_tool_groups(names)
            if self.capability_session is not None
            else create_native_tool_groups(names)
        )
        self._inject_required_tool_groups(required)

    def _inject_required_tool_groups(
        self,
        required_tool_groups: Sequence[ToolGroup],
    ) -> None:
        inject_required_tool_groups(self.task, required_tool_groups)

    def build_user_prompt(self) -> str:
        """Return the Task request used to initialize the agent conversation."""

        return self.task.task_prompt

    def _transition_task_state(
        self,
        expected: TaskState,
        target: TaskState,
    ) -> None:
        """Commit one Harness-selected state transition through Task CAS."""

        if self.task.compare_and_set_state(expected, target):
            return

        current = self.task.state
        if current is TaskState.Interrupted:
            raise HarnessInterruptedError

        raise HarnessStateTransitionError(
            f"{type(self).__name__} could not transition Task "
            f"from {expected.value} to {target.value}; "
            f"current state is {current.value}"
        )

    def _require_task_state(self, expected: TaskState) -> None:
        """Assert the state expected by the concrete Harness algorithm."""

        current = self.task.state
        if current is expected:
            return
        if current is TaskState.Interrupted:
            raise HarnessInterruptedError

        raise HarnessStateTransitionError(
            f"{type(self).__name__} expected Task state "
            f"{expected.value}, got {current.value}"
        )

    def build_openai_tools(self) -> list[dict[str, Any]]:
        return self.tool_execution_pipeline.build_openai_tools()

    def parse_completion(
        self,
        choice: ChatCompletionChoice,
    ) -> ToolProtocolResponse:
        """Parse one OpenAI Chat choice without executing its calls."""

        return self.tool_execution_pipeline.parse_completion(choice)

    def resolve_and_adapt_tool_calls(
        self,
        tool_calls: Sequence[ToolCall],
    ) -> list[ResolvedToolCall]:
        return self.tool_execution_pipeline.resolve_and_adapt_tool_calls(
            tool_calls
        )

    async def execute_tool_calls(
        self,
        tool_calls: Sequence[ToolCall],
    ) -> list[ToolExecutionResult]:
        return await self.tool_execution_pipeline.execute_calls(tool_calls)

    def build_tool_result_messages(
        self,
        results: Sequence[ToolExecutionResult],
    ) -> list[Message]:
        """Build ordered role=tool messages for the next model round."""

        return self.tool_execution_pipeline.build_tool_result_messages(
            results
        )

    @abstractmethod
    async def run(self) -> object:
        """Run this harness's agent loop and return its eventual result."""
        raise NotImplementedError


__all__ = [
    "Harness",
    "HarnessInterruptedError",
    "HarnessStateTransitionError",
]
