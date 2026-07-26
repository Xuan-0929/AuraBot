"""
负责解析 Plan-and-Solve 最终响应首行的稳定状态协议。

Harness 将 LLM 文本交给 parser，得到 completed、failed 或 waiting_for_info 状态；
本模块不生成提示词、不改变 Task/Plan 状态，也不处理 HTTP 或 MCP transport。
"""

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
