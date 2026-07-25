"""Stable, client-safe errors for native Plan tools."""

from __future__ import annotations


ERROR_MESSAGES = {
    "invalid_task_context": (
        "The Plan tool request is missing valid Task routing context."
    ),
    "task_not_found": "The requested Task runtime was not found.",
    "invalid_task_type": "The requested Task runtime is not Plan-and-Solve.",
    "invalid_task_state": "The Task state does not allow this Plan operation.",
    "plan_already_exists": (
        "A different Plan already exists. Read it before updating."
    ),
    "plan_not_found": "No Plan exists for this Task. Create one first.",
    "invalid_plan": "The submitted Plan violates the Plan invariants.",
    "completed_step_immutable": (
        "Completed Plan steps must remain an unchanged prefix."
    ),
    "step_not_found": "The requested Plan step does not exist.",
    "step_not_current": "Only the current pending Plan step can be completed.",
    "step_already_completed": (
        "The Plan step is already completed with a different summary."
    ),
    "backend_unavailable": "The backend Plan service is unavailable.",
    "backend_timeout": "The backend Plan request timed out.",
    "backend_protocol_error": (
        "The backend Plan service returned an invalid response."
    ),
    "internal_error": "Plan tool execution failed.",
}


class PlanToolFailure(Exception):
    """An expected Plan failure safe to expose to MCP clients."""

    def __init__(self, code: str):
        if code not in ERROR_MESSAGES:
            raise ValueError(f"unknown Plan Tool error code: {code}")
        self.code = code
        self.message = ERROR_MESSAGES[code]
        super().__init__(self.message)


__all__ = ["ERROR_MESSAGES", "PlanToolFailure"]
