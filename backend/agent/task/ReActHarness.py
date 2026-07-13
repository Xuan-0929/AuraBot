from .Harness import Harness


class ReActHarness(Harness):
    """Task-local ReAct loop.

    Prompt assembly, tool-call parsing, and tool execution will be implemented
    here. Construction already follows the common Harness contract.
    """

    async def run(self) -> object:
        raise NotImplementedError("ReAct agent loop is not implemented yet")
