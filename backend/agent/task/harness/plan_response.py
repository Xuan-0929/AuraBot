"""Plan-and-Solve final-response protocol."""

from typing import Literal


PlanFinalStatus = Literal["completed", "failed", "waiting_for_info"]


class PlanAndSolveFinalResponseError(ValueError):
    """Raised when a final response violates the declared status protocol."""


def parse_plan_final_status(content: str) -> PlanFinalStatus:
    first_non_empty_line = next(
        (line.strip() for line in content.splitlines() if line.strip()),
        None,
    )
    statuses: dict[str, PlanFinalStatus] = {
        "Status: completed": "completed",
        "Status: failed": "failed",
        "Status: waiting_for_info": "waiting_for_info",
    }
    try:
        return statuses[first_non_empty_line]
    except (KeyError, TypeError) as exc:
        raise PlanAndSolveFinalResponseError(
            "Plan-and-Solve final response must begin with exactly "
            "'Status: completed' or 'Status: failed'"
        ) from exc


__all__ = [
    "PlanAndSolveFinalResponseError",
    "PlanFinalStatus",
    "parse_plan_final_status",
]
