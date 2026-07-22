from .Harness import Harness
from .prompts import system_prompt_PlanAndSolve
from .utils import build_common_prompt_values


class PlanAndSolveHarness(Harness):
    """Task-local planning and checkpoint loop."""

    def build_system_prompt(self) -> str:
        """Build the Plan-and-Solve prompt with the native format API."""

        return system_prompt_PlanAndSolve.format(
            **build_common_prompt_values(self.task)
        ).strip()

    async def run(self) -> object:
        raise NotImplementedError(
            "Plan-and-Solve agent loop is not implemented yet"
        )
