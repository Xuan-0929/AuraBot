from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import Any

from ...LLMClient import ChatCompletionChoice, LLMClient, Message
from ...tools.core.OpenAIChatToolProtocol import OpenAIChatToolProtocol
from ...tools.core.ToolCallEntity import (
    ResolvedToolCall,
    ToolCall,
    ToolExecutionResult,
    ToolProtocolResponse,
)
from ..Task import Task
from .adaptors.Adaptor import Adaptor, AdaptorRouteError
from .adaptors.FileSystemAdaptor import FileSystemAdaptor


AdaptorFactory = Callable[[Task], Adaptor]


class Harness(ABC):
    """One task-local agent loop backed by a shared LLM client."""

    DEFAULT_ADAPTOR_FACTORIES: Mapping[str, AdaptorFactory] = {
        "FileSystemAdaptor": lambda task: FileSystemAdaptor(task.workdir),
    }

    def __init__(
        self,
        *,
        task: Task,
        llm_client: LLMClient,
        adaptor_factories: Mapping[str, AdaptorFactory] | None = None,
    ):
        self._task = task
        self._llm_client = llm_client
        configured_factories = dict(self.DEFAULT_ADAPTOR_FACTORIES)
        if adaptor_factories is not None:
            configured_factories.update(adaptor_factories)
        if any(
            not isinstance(name, str)
            or not name
            or name != name.strip()
            or not callable(factory)
            for name, factory in configured_factories.items()
        ):
            raise TypeError(
                "adaptor_factories must map non-empty names to callables"
            )
        self._adaptor_factories = configured_factories
        self._adaptors: dict[str, Adaptor] = {}
        self._adaptor_routes_built = False
        self._built_tool_groups: object | None = None

    @property
    def task(self) -> Task:
        return self._task

    @property
    def llm_client(self) -> LLMClient:
        return self._llm_client

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
        adaptor_names = dict.fromkeys(
            tool.adaptor_name
            for group in self.task.tool_groups
            for tool in group.tools
            if tool.adaptor_name is not None
        )
        candidate_adaptors: dict[str, Adaptor] = {}
        for adaptor_name in adaptor_names:
            factory = self._adaptor_factories.get(adaptor_name)
            if factory is None:
                raise AdaptorRouteError(
                    f"no Adaptor factory is registered for {adaptor_name!r}"
                )
            adaptor = factory(self.task)
            if not isinstance(adaptor, Adaptor):
                raise TypeError(
                    f"Adaptor factory {adaptor_name!r} must return Adaptor"
                )
            candidate_adaptors[adaptor_name] = adaptor

        self._adaptors = candidate_adaptors
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
        """Resolve calls first, then apply each Tool's configured Adaptor."""

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
            adaptor_name = resolved_call.tool.adaptor_name
            if adaptor_name is None:
                adapted_calls.append(resolved_call)
                continue

            adaptor = self._adaptors.get(adaptor_name)
            if adaptor is None:
                raise AdaptorRouteError(
                    f"Adaptor route {adaptor_name!r} is not built for tool "
                    f"{resolved_call.tool.name!r}"
                )
            adapted_call = adaptor.adapt(resolved_call)
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


__all__ = ["AdaptorFactory", "Harness"]
