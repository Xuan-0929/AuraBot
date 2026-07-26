"""Tool protocol, routing, adaptation and execution for one Task runtime."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

from ...llm_client import ChatCompletionChoice, Message
from ...tools.core.openai_chat_protocol import OpenAIChatToolProtocol
from ...tools.core.model import (
    ResolvedToolCall,
    ToolCall,
    ToolExecutionResult,
    ToolProtocolResponse,
)
from ...tools.core.gateway import ToolCallGateway
from ..model import Task
from .adaptors.adaptor import Adaptor, AdaptorRouteError
from .adaptors.registry import AdaptorRegistry


class ToolExecutionPipeline:
    """Own the complete tool-call path shared by all Harness algorithms."""

    def __init__(
        self,
        *,
        task: Task,
        tool_call_gateway: ToolCallGateway,
        adaptor_registry: AdaptorRegistry,
    ):
        self._task = task
        self._tool_call_gateway = tool_call_gateway
        self._adaptor_registry = adaptor_registry
        self._adaptors: dict[str, Adaptor] = {}
        self._adaptor_pipelines: dict[str, tuple[Adaptor, ...]] = {}
        self._routes_built = False
        self._built_tool_groups: object | None = None

    @property
    def tool_call_gateway(self) -> ToolCallGateway:
        return self._tool_call_gateway

    @property
    def adaptor_registry(self) -> AdaptorRegistry:
        return self._adaptor_registry

    @property
    def adaptors(self) -> Mapping[str, Adaptor]:
        return MappingProxyType(self._adaptors)

    def build_openai_tools(self) -> list[dict[str, Any]]:
        definitions = OpenAIChatToolProtocol.encode_tools(
            self._task.tool_groups
        )
        adaptor_names = tuple(
            dict.fromkeys(
                adaptor_name
                for group in self._task.tool_groups
                for tool in group.tools
                for adaptor_name in tool.adaptor_names
            )
        )
        candidate_adaptors = self.adaptor_registry.create_many(
            adaptor_names,
            self._task,
        )
        candidate_pipelines = {
            tool.name: tuple(
                candidate_adaptors[name] for name in tool.adaptor_names
            )
            for group in self._task.tool_groups
            for tool in group.tools
        }

        self._adaptors = candidate_adaptors
        self._adaptor_pipelines = candidate_pipelines
        self._routes_built = True
        self._built_tool_groups = self._task.tool_groups
        return definitions

    @staticmethod
    def parse_completion(
        choice: ChatCompletionChoice,
    ) -> ToolProtocolResponse:
        return OpenAIChatToolProtocol.parse_completion(
            choice.message,
            choice.finish_reason,
        )

    def resolve_and_adapt_tool_calls(
        self,
        tool_calls: Sequence[ToolCall],
    ) -> list[ResolvedToolCall]:
        resolved_calls = self._task.tool_call_resolver.resolve(tool_calls)
        if not self._routes_built:
            raise AdaptorRouteError(
                "build_openai_tools() must run before adapting tool calls"
            )
        if self._built_tool_groups is not self._task.tool_groups:
            raise AdaptorRouteError(
                "build_openai_tools() must rebuild routes after "
                "Task.update_tool_groups()"
            )
        adapted_calls: list[ResolvedToolCall] = []
        for resolved_call in resolved_calls:
            pipeline = self._adaptor_pipelines.get(resolved_call.tool.name)
            if pipeline is None:
                raise AdaptorRouteError(
                    "Adaptor pipeline is not built for tool "
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

    async def execute_calls(
        self,
        tool_calls: Sequence[ToolCall],
    ) -> list[ToolExecutionResult]:
        resolved_calls = self.resolve_and_adapt_tool_calls(tool_calls)
        return await self.tool_call_gateway.execute_many(resolved_calls)

    @staticmethod
    def build_tool_result_messages(
        results: Sequence[ToolExecutionResult],
    ) -> list[Message]:
        return OpenAIChatToolProtocol.encode_tool_messages(results)


__all__ = ["ToolExecutionPipeline"]
