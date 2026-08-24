"""解析 AuraBot 专属 Pi UI Question 并构造 session continuation prompt。

本模块只处理无状态的协议映射：识别固定 marker、规范化问题选项，并从已持久化 Task
与 Question 构造恢复提示。SQLite、RPC 发送、进程回收和产品状态转换由其他模块负责。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..models import TaskQuestion, TaskRecord

QUESTION_MARKER = "AURABOT_TASK_QUESTION_V1\n"
RESUME_PROMPT = (
    "Resume the existing AuraBot Task session. Inspect the persisted session context, "
    "completed Tool results, and the original goal before continuing safely."
)


@dataclass(frozen=True, slots=True)
class ParsedQuestionRequest:
    """Validated AuraBot question values consumed by the execution coroutine."""

    request_id: str
    question: str
    options: tuple[str, ...]


def parse_question_request(payload: object) -> ParsedQuestionRequest | None:
    """Return one AuraBot input/select request, or None for unrelated Pi dialogs."""

    if not isinstance(payload, Mapping):
        return None
    request_id = payload.get("id")
    method = payload.get("method")
    title = payload.get("title")
    if not (
        isinstance(request_id, str)
        and method in {"input", "select"}
        and isinstance(title, str)
        and title.startswith(QUESTION_MARKER)
    ):
        return None
    raw_options = payload.get("options")
    options = (
        tuple(value for value in raw_options if isinstance(value, str))
        if isinstance(raw_options, list)
        else ()
    )
    return ParsedQuestionRequest(
        request_id=request_id,
        question=title[len(QUESTION_MARKER) :],
        options=options,
    )


def dialog_response_id(payload: object) -> str | None:
    """Return the request id for a supported Pi dialog that AuraBot must reject."""

    if not isinstance(payload, Mapping):
        return None
    request_id = payload.get("id")
    method = payload.get("method")
    if isinstance(request_id, str) and method in {
        "input",
        "select",
        "confirm",
        "editor",
    }:
        return request_id
    return None


def continuation_prompt(
    task: TaskRecord, undelivered: TaskQuestion | None
) -> str | None:
    """Derive Pi restart input only from durable Task and Question snapshots."""

    if undelivered is not None:
        return (
            f"{RESUME_PROMPT}\n"
            f"The Main Agent answered the pending question: {undelivered.answer}"
        )
    if task.started_at is not None:
        return RESUME_PROMPT
    return None


__all__ = [
    "QUESTION_MARKER",
    "RESUME_PROMPT",
    "ParsedQuestionRequest",
    "continuation_prompt",
    "dialog_response_id",
    "parse_question_request",
]
