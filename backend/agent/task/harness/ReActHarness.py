import asyncio

from ...LLMClient import OpenAITool
from ...tools.core.ToolCallEntity import FinalResponse, ToolCallBatch
from ..Task import TaskState
from .Harness import Harness
from .prompts import system_prompt_ReAct
from .utils import build_common_prompt_values


class ReActLimitError(RuntimeError):
    """Raised when the basic ReAct loop exhausts an execution limit."""


class ReActHarness(Harness):
    """Task-local ReAct loop.

    Only the ReAct loop belongs here; common prompt and tool protocol behavior
    is inherited from Harness.
    """

    MAX_REACT_ROUNDS = 20
    MAX_TOOL_CALLS = 15

    def build_system_prompt(self) -> str:
        """Build the ReAct system prompt with the native format API."""

        return system_prompt_ReAct.format(
            **build_common_prompt_values(self.task)
        ).strip()

    async def run(self) -> str:
        """Run a bounded ReAct loop and retain all context on the Task."""

        if self.task.state is not TaskState.Initializing:
            raise RuntimeError("ReActHarness.run() can only execute a new Task")

        try:
            openai_tools = self.build_openai_tools()
            self._initialize_context()
            self.task.set_pending()
            self.task.set_running()

            async with asyncio.timeout(self.task.timeout_ms / 1000):
                result = await self._run_react_loop(openai_tools)
        except Exception:
            self.task.set_failed()
            raise

        self.task.set_completed()
        return result

    def _initialize_context(self) -> None:
        self.task.messages.extend(
            [
                {"role": "system", "content": self.build_system_prompt()},
                {"role": "user", "content": self.build_user_prompt()},
            ]
        )

    async def _run_react_loop(
        self,
        openai_tools: list[OpenAITool],
    ) -> str:
        tool_call_count = 0

        for _ in range(self.MAX_REACT_ROUNDS):
            choice = await self.llm_client.invoke(
                self.task.messages,
                tools=openai_tools or None,
                tool_choice="auto" if openai_tools else None,
            )
            self.task.messages.append(dict(choice.message))
            response = self.parse_completion(choice)

            if isinstance(response, FinalResponse):
                return response.content
            if not isinstance(response, ToolCallBatch):
                raise TypeError("unsupported ReAct model response")

            tool_call_count += len(response.calls)
            if tool_call_count > self.MAX_TOOL_CALLS:
                raise ReActLimitError(
                    "ReAct tool-call limit exceeded "
                    f"({self.MAX_TOOL_CALLS})"
                )

            resolved_calls = self.resolve_and_adapt_tool_calls(response.calls)
            results = await self.tool_call_gateway.execute_many(resolved_calls)
            self.task.messages.extend(
                self.build_tool_result_messages(results)
            )

        raise ReActLimitError(
            f"ReAct round limit exceeded ({self.MAX_REACT_ROUNDS})"
        )


__all__ = ["ReActHarness", "ReActLimitError"]
