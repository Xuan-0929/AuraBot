import asyncio
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

from ...LLMClient import ChatCompletionChoice, LLMClient, Message
from ...capabilities.lease import CapabilitySession
from ...integration.mcp import MCPClient
from ...tools.Tool import Tool
from ...tools.ToolGroup import ToolGroup
from ...tools.core.OpenAIChatToolProtocol import OpenAIChatToolProtocol
from ...tools.core.ToolCallEntity import (
    ResolvedToolCall,
    ToolCall,
    ToolExecutionResult,
    ToolProtocolResponse,
)
from ...tools.core.ToolCallGateway import ToolCallGateway
from ..Task import Task
from ..TaskEntity import TaskState
from .adaptors.Adaptor import Adaptor, AdaptorRouteError
from .adaptors.AdaptorRegistry import AdaptorRegistry


class HarnessInterruptedError(asyncio.CancelledError):
    """Raised when an external interrupt wins a Task state race."""


class HarnessStateTransitionError(RuntimeError):
    """Raised when a Harness cannot commit its expected Task transition."""


class Harness(ABC):
    """One task-local agent loop backed by a shared LLM client."""

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
        self._tool_call_gateway = (
            tool_call_gateway
            if tool_call_gateway is not None
            else ToolCallGateway(
                MCPClient(timeout_seconds=task.timeout_ms / 1000)
            )
        )
        self._adaptor_registry = (
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
        self._adaptors: dict[str, Adaptor] = {}
        self._adaptor_pipelines: dict[str, tuple[Adaptor, ...]] = {}
        self._adaptor_routes_built = False
        self._built_tool_groups: object | None = None
        self.inject_tool_group()

    @property
    def task(self) -> Task:
        return self._task

    @property
    def llm_client(self) -> LLMClient:
        return self._llm_client

    @property
    def tool_call_gateway(self) -> ToolCallGateway:
        return self._tool_call_gateway

    @property
    def adaptor_registry(self) -> AdaptorRegistry:
        return self._adaptor_registry

    @property
    def capability_session(self) -> CapabilitySession | None:
        return self._capability_session

    @property
    def adaptors(self) -> Mapping[str, Adaptor]:
        """Return Task-local Adaptor instances built for current Tools."""

        return MappingProxyType(self._adaptors)

    @abstractmethod
    def build_system_prompt(self) -> str:
        """Build the system prompt for this Harness algorithm."""

        raise NotImplementedError(
            "Harness subclasses must implement build_system_prompt()"
        )

    @abstractmethod
    def inject_tool_group(self) -> None:
        """Install ToolGroups required by this concrete Harness algorithm."""

        raise NotImplementedError(
            "Harness subclasses must implement inject_tool_group()"
        )

    def _inject_required_tool_groups(
        self,
        required_tool_groups: Sequence[ToolGroup],
    ) -> None:
        """Atomically prepend canonical Harness-required ToolGroups."""

        required = tuple(required_tool_groups)
        if any(not isinstance(group, ToolGroup) for group in required):
            raise TypeError("required_tool_groups must contain ToolGroup values")

        required_names = tuple(group.name for group in required)
        if len(set(required_names)) != len(required_names):
            raise ValueError(
                "required ToolGroups must have unique group names"
            )

        installed_required: list[ToolGroup] = []
        matched_group_ids: set[int] = set()
        for canonical_group in required:
            matching_groups = tuple(
                group
                for group in self.task.tool_groups
                if group.name == canonical_group.name
            )
            if len(matching_groups) > 1:
                raise ValueError(
                    "multiple ToolGroups use reserved name "
                    f"{canonical_group.name!r}"
                )
            if matching_groups:
                existing = matching_groups[0]
                if not self._tool_groups_equal(existing, canonical_group):
                    raise ValueError(
                        f"ToolGroup {canonical_group.name!r} conflicts with "
                        "the canonical required ToolGroup"
                    )
                installed_required.append(existing)
                matched_group_ids.add(id(existing))
            else:
                installed_required.append(canonical_group)

        ordinary_groups = tuple(
            group
            for group in self.task.tool_groups
            if id(group) not in matched_group_ids
        )
        candidate = (*installed_required, *ordinary_groups)
        if (
            len(candidate) == len(self.task.tool_groups)
            and all(
                candidate_group is existing_group
                for candidate_group, existing_group in zip(
                    candidate,
                    self.task.tool_groups,
                )
            )
        ):
            return

        self.task.update_tool_groups(candidate)

    @staticmethod
    def _tool_groups_equal(left: ToolGroup, right: ToolGroup) -> bool:
        return (
            left.name == right.name
            and left.description == right.description
            and len(left.tools) == len(right.tools)
            and all(
                Harness._tools_equal(left_tool, right_tool)
                for left_tool, right_tool in zip(left.tools, right.tools)
            )
        )

    @staticmethod
    def _tools_equal(left: Tool, right: Tool) -> bool:
        return (
            left.name == right.name
            and left.description == right.description
            and left.server_url == right.server_url
            and left.input_schema == right.input_schema
            and left.output_schema == right.output_schema
            and left.adaptor_names == right.adaptor_names
        )

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
        """Atomically build OpenAI definitions and required Adaptors."""

        definitions = OpenAIChatToolProtocol.encode_tools(
            self.task.tool_groups
        )
        adaptor_names = tuple(
            dict.fromkeys(
                adaptor_name
                for group in self.task.tool_groups
                for tool in group.tools
                for adaptor_name in tool.adaptor_names
            )
        )
        candidate_adaptors = self.adaptor_registry.create_many(
            adaptor_names,
            self.task,
        )
        candidate_pipelines = {
            tool.name: tuple(
                candidate_adaptors[name] for name in tool.adaptor_names
            )
            for group in self.task.tool_groups
            for tool in group.tools
        }

        self._adaptors = candidate_adaptors
        self._adaptor_pipelines = candidate_pipelines
        self._adaptor_routes_built = True
        self._built_tool_groups = self.task.tool_groups
        return definitions

    def parse_completion(
        self,
        choice: ChatCompletionChoice,
    ) -> ToolProtocolResponse:
        """Parse one OpenAI Chat choice without executing its calls."""

        return OpenAIChatToolProtocol.parse_completion(
            choice.message,
            choice.finish_reason,
        )

    def resolve_and_adapt_tool_calls(
        self,
        tool_calls: Sequence[ToolCall],
    ) -> list[ResolvedToolCall]:
        """Resolve calls, then apply each Tool's Adaptor pipeline in order."""

        resolved_calls = self.task.tool_call_resolver.resolve(tool_calls)
        if not self._adaptor_routes_built:
            raise AdaptorRouteError(
                "build_openai_tools() must run before adapting tool calls"
            )
        if self._built_tool_groups is not self.task.tool_groups:
            raise AdaptorRouteError(
                "build_openai_tools() must rebuild routes after "
                "Task.update_tool_groups()"
            )
        adapted_calls: list[ResolvedToolCall] = []

        for resolved_call in resolved_calls:
            pipeline = self._adaptor_pipelines.get(resolved_call.tool.name)
            if pipeline is None:
                raise AdaptorRouteError(
                    f"Adaptor pipeline is not built for tool "
                    f"{resolved_call.tool.name!r}"
                )
            adapted_call = resolved_call
            for adaptor in pipeline:
                adapted_call = adaptor.adapt(adapted_call)
                if not isinstance(adapted_call, ResolvedToolCall):
                    raise TypeError(
                        "Adaptor.adapt() must return ResolvedToolCall"
                    )
            adapted_calls.append(adapted_call)

        return adapted_calls

    def build_tool_result_messages(
        self,
        results: Sequence[ToolExecutionResult],
    ) -> list[Message]:
        """Build ordered role=tool messages for the next model round."""

        return OpenAIChatToolProtocol.encode_tool_messages(results)

    @abstractmethod
    async def run(self) -> object:
        """Run this harness's agent loop and return its eventual result."""
        raise NotImplementedError


__all__ = [
    "Harness",
    "HarnessInterruptedError",
    "HarnessStateTransitionError",
]
