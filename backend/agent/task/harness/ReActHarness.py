from .Harness import Harness
from .prompts import system_prompt_ReAct
from .utils import build_common_prompt_values


class ReActHarness(Harness):
    """Task-local ReAct loop.

    Only the ReAct loop belongs here; common prompt and tool protocol behavior
    is inherited from Harness.
    """

    def build_system_prompt(self) -> str:
        """Build the ReAct system prompt with the native format API."""

        return system_prompt_ReAct.format(
            **build_common_prompt_values(self.task)
        ).strip()


    async def run(self) -> object:
        raise NotImplementedError("ReAct agent loop is not implemented yet")
