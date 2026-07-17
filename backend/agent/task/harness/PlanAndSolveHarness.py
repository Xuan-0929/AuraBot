from .Harness import Harness


class PlanAndSolveHarness(Harness):
    """Task-local planning and checkpoint loop."""

    async def run(self) -> object:
        raise NotImplementedError