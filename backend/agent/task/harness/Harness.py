from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

from ...LLMClient import ChatCompletionChoice, LLMClient, Message
from ...integration.mcp import MCPClient
from ...tools.core.OpenAIChatToolProtocol import OpenAIChatToolProtocol
from ...tools.core.ToolCallEntity import (
    ResolvedToolCall,
    ToolCall,
    ToolExecutionResult,
    ToolProtocolResponse,
)
from ...tools.core.ToolCallGateway import ToolCallGateway
from ..Task import Task
from .adaptors.Adaptor import Adaptor, AdaptorRouteError
from .adaptors.AdaptorRegistry import AdaptorRegistry


class Harness(ABC):
    """One task-local agent loop backed by a shared LLM client."""

    def __init__(
        self,
        *,
        task: Task,
        llm_client: LLMClient,
        tool_call_gateway: ToolCallGateway | None = None,
        adaptor_registry: AdaptorRegistry | None = None,
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
        self._adaptors: dict[str, Adaptor] = {}
        self._adaptor_pipelines: dict[str, tuple[Adaptor, ...]] = {}
        self._adaptor_routes_built = False
        self._built_tool_groups: object | None = None

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
    def adaptors(self) -> Mapping[str, Adaptor]:
        """Return Task-local Adaptor instances built for current Tools."""

        return MappingProxyType(self._adaptors)

    @abstractmethod
    def build_system_prompt(self) -> str:
        """Build the system prompt for this Harness algorithm."""

        raise NotImplementedError(
            "Harness subclasses must implement build_system_prompt()"
        )

    def build_user_prompt(self) -> str:
        """Return the Task request used to initialize the agent conversation."""

        return self.task.task_prompt

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


__all__ = ["Harness"]
