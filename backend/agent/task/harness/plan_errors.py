"""Stable Plan business-error semantics shared across application layers."""

from types import MappingProxyType


PLAN_ERROR_MESSAGES = MappingProxyType({
    "invalid_task_state": "The Task state does not allow this Plan operation.",
    "plan_already_exists": (
        "A different Plan already exists for this Task. Read it before updating."
    ),
    "plan_not_found": "No Plan exists for this Task. Create one first.",
    "invalid_plan": "The submitted Plan violates the Plan invariants.",
    "completed_step_immutable": (
        "Completed Plan steps are immutable and must remain an unchanged prefix."
    ),
    "step_not_found": "The requested Plan step does not exist.",
    "step_not_current": "Only the current pending Plan step can be completed.",
    "step_already_completed": (
        "The Plan step is already completed with a different summary."
    ),
})


class PlanOperationError(RuntimeError):
    """A stable, safe business error returned by Plan operations."""

    def __init__(self, code: str):
        if code not in PLAN_ERROR_MESSAGES:
            raise ValueError(f"unknown Plan error code: {code}")
        self.code = code
        self.message = PLAN_ERROR_MESSAGES[code]
        super().__init__(self.message)


__all__ = ["PLAN_ERROR_MESSAGES", "PlanOperationError"]
