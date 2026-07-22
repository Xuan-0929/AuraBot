from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from ...LLMClient import ChatCompletionChoice, LLMClient, Message
from ...tools.core.OpenAIChatToolProtocol import OpenAIChatToolProtocol
from ...tools.core.ToolCallEntity import (
    ToolExecutionResult,
    ToolProtocolResponse,
)
from ..Task import Task


class Harness(ABC):
    """One task-local agent loop backed by a shared LLM client."""

    def __init__(
        self,
        *,
        task: Task,
        llm_client: LLMClient,
    ):
        self._task = task
        self._llm_client = llm_client

    @property
    def task(self) -> Task:
        return self._task

    @property
    def llm_client(self) -> LLMClient:
        return self._llm_client

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
        """Build the function-tool definitions authorized for this Task."""

        return OpenAIChatToolProtocol.encode_tools(self.task.tool_groups)

    def parse_completion(
        self,
        choice: ChatCompletionChoice,
    ) -> ToolProtocolResponse:
        """Parse one OpenAI Chat choice without executing its calls."""

        return OpenAIChatToolProtocol.parse_completion(
            choice.message,
            choice.finish_reason,
        )

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
