from .Harness import Harness


class PlanAndSaveHarness(Harness):
    """Task-local planning and checkpoint loop."""

    async def run(self) -> object:
        raise NotImplementedError("PlanAndSave agent loop is not implemented yet")
