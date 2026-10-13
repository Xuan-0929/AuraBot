"""Adapt stable TaskManager methods into owner-scoped Main Agent tools and context facts.

Inspection, answers, pause/resume/cancel, terminal deletion and rerun all fresh-read owner truth. Rerun
creates a new normal Task from the previous command; the bridge never changes the Task state machine.
Startup reconciles current records before streaming newer important events.
Only bounded product projections leave this seam; prompts and working directories remain private."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import UUID, uuid4

from backend.task.models import (
    CreateTaskCommand,
    TaskNotFound,
    TaskRecord,
    TaskRuntimeUnavailable,
    TaskStateConflict,
    TaskStatus,
    TaskVersionConflict,
)

from .tool_runtime import ToolCall, ToolExecutorError


_IMPORTANT_EVENTS = frozenset({
    "task.created", "task.status_changed", "task.question_raised",
    "task.question_answered", "task.deleted",
})


class TaskBridge:
    """Owner-routing adapter around one already-started TaskManager instance."""

    def __init__(self, manager: Any, owner_profile_id: str) -> None:
        if manager is None:
            raise TypeError("task manager is required")
        if not isinstance(owner_profile_id, str) or not owner_profile_id.strip():
            raise ValueError("owner_profile_id must be non-empty text")
        self.manager = manager
        self.owner_profile_id = owner_profile_id.strip()

    async def inspect_tasks(self, call: ToolCall) -> dict[str, Any]:
        """Query only the requested owner-scoped public records through TaskManager."""

        limit = call.arguments.get("limit", 20)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ToolExecutorError("schema_invalid")
        raw_statuses = call.arguments.get("statuses", ())
        try:
            statuses = tuple(TaskStatus(value) for value in raw_statuses)
        except (TypeError, ValueError):
            raise ToolExecutorError("schema_invalid") from None
        page = await self.manager.list_tasks(
            limit=limit, statuses=statuses or None, owner_profile_id=self.owner_profile_id,
        )
        return {"tasks": [task_fact(task, include_details=False) for task in page.items]}

    async def current_facts(self) -> AsyncIterator[dict[str, Any]]:
        """Read current owner records in bounded pages, including deleted Task truth."""
        cursor = None
        while True:
            page = await self.manager.list_tasks(
                after_row_id=cursor, limit=200, owner_profile_id=self.owner_profile_id,
                include_deleted=True,
            )
            for task in page.items:
                yield task_fact(task)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
    async def facts(self, after_event_id: int) -> AsyncIterator[dict[str, Any]]:
        """Follow important facts newer than the startup watermark."""
        async for event in self.manager.iter_events(after_event_id, event_types=_IMPORTANT_EVENTS):
            fact = await self.fact_for_event(event)
            if fact is not None:
                yield fact

    async def answer_task_question(
        self, call: ToolCall, *, action_started: Callable[[], None] = lambda: None,
    ) -> dict[str, Any]:
        task = await self._owned(call.arguments.get("task_id"))
        question_id = _uuid(call.arguments.get("question_id"), "task_question_stale")
        if (
            task.status is not TaskStatus.WAITING
            or task.question_id != question_id
            or task.question_answer is not None
        ):
            raise ToolExecutorError("task_question_stale")
        try:
            action_started()
            updated = await self.manager.answer_question(
                task.id, question_id, call.arguments["answer"], task.version,
            )
        except (KeyError, TaskNotFound, TaskStateConflict):
            raise ToolExecutorError("task_question_stale") from None
        except TaskVersionConflict:
            raise ToolExecutorError("task_version_conflict") from None
        except TaskRuntimeUnavailable:
            raise ToolExecutorError("task_runtime_unavailable") from None
        return task_fact(updated)

    async def pause_task(
        self, call: ToolCall, *, action_started: Callable[[], None] = lambda: None,
    ) -> dict[str, Any]:
        return await self._control("pause_task", call, action_started)

    async def resume_task(
        self, call: ToolCall, *, action_started: Callable[[], None] = lambda: None,
    ) -> dict[str, Any]:
        return await self._control("resume_task", call, action_started)

    async def cancel_task(
        self, call: ToolCall, *, action_started: Callable[[], None] = lambda: None,
    ) -> dict[str, Any]:
        return await self._control("cancel_task", call, action_started)

    async def delete_task(self, call: ToolCall, *, action_started: Callable[[], None] = lambda: None) -> dict[str, Any]:
        task = await self._owned(call.arguments.get("task_id"))
        try:
            action_started()
            await self.manager.delete_task(task.id)
        except TaskStateConflict:
            raise ToolExecutorError("task_state_conflict") from None
        except TaskRuntimeUnavailable:
            raise ToolExecutorError("task_runtime_unavailable") from None
        return task_fact(await self.manager.get_task(task.id, include_deleted=True))

    async def rerun_task(self, call: ToolCall, *, action_started: Callable[[], None] = lambda: None) -> dict[str, Any]:
        task = await self._owned(call.arguments.get("task_id"))
        if task.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            raise ToolExecutorError("task_state_conflict")
        try:
            action_started()
            updated = await self.manager.create_task(CreateTaskCommand(
                title=task.title, prompt=task.prompt, workspace_id=task.workspace_id,
                owner_profile_id=self.owner_profile_id, idempotency_key=f"rerun:{uuid4().hex}",
                timeout_seconds=task.timeout_seconds, rerun_of_task_id=task.id,
            ))
        except TaskRuntimeUnavailable:
            raise ToolExecutorError("task_runtime_unavailable") from None
        return task_fact(updated)

    async def execute_control(
        self, call: ToolCall, *, action_started: Callable[[], None] = lambda: None,
    ) -> dict[str, Any]:
        """Dispatch one exact configured control while exposing its Manager-call ownership boundary."""

        executor = {
            "answer_task_question": self.answer_task_question,
            "pause_task": self.pause_task,
            "resume_task": self.resume_task,
            "cancel_task": self.cancel_task,
            "delete_task": self.delete_task,
            "rerun_task": self.rerun_task,
        }.get(call.name)
        if executor is None:
            raise ToolExecutorError("unknown_tool")
        return await executor(call, action_started=action_started)

    async def fact_for_event(self, event: Any) -> dict[str, Any] | None:
        """Fresh-project an important durable event; duplicate/noisy/foreign events disappear."""

        if getattr(event, "type", None) not in _IMPORTANT_EVENTS:
            return None
        try:
            task = await self.manager.get_task(event.task_id, include_deleted=event.type == "task.deleted")
        except (KeyError, TaskNotFound):
            return None
        if task.owner_profile_id != self.owner_profile_id:
            return None
        return task_fact(task)

    async def _owned(self, raw_task_id: object) -> TaskRecord:
        task_id = _uuid(raw_task_id, "task_not_found")
        try:
            task = await self.manager.get_task(task_id)
        except (KeyError, TaskNotFound):
            raise ToolExecutorError("task_not_found") from None
        if task.owner_profile_id != self.owner_profile_id:
            raise ToolExecutorError("task_not_found")
        return task

    async def _control(
        self, method_name: str, call: ToolCall, action_started: Callable[[], None],
    ) -> dict[str, Any]:
        task = await self._owned(call.arguments.get("task_id"))
        try:
            action_started()
            updated = await getattr(self.manager, method_name)(task.id, task.version)
        except TaskVersionConflict:
            raise ToolExecutorError("task_version_conflict") from None
        except TaskStateConflict:
            raise ToolExecutorError("task_state_conflict") from None
        except (KeyError, TaskNotFound):
            raise ToolExecutorError("task_not_found") from None
        except TaskRuntimeUnavailable:
            raise ToolExecutorError("task_runtime_unavailable") from None
        return task_fact(updated)


def task_fact(task: TaskRecord, *, include_details: bool = True) -> dict[str, Any]:
    """Project only bounded, user-relevant Task fields into Canonical Context or tool results."""

    fact: dict[str, Any] = {
        "id": f"task:{task.id}",
        "kind": "task",
        "task_id": str(task.id),
        "title": task.title[:200],
        "workspace_id": task.workspace_id,
        "status": task.status.value,
        "version": task.version,
    }
    if task.deleted_at is not None:
        fact["deleted"] = True
    if task.question_id is not None and task.question_answer is None:
        fact["question"] = {
            "question_id": str(task.question_id),
            "text": (task.question_text or "")[:700],
            "options": [option[:150] for option in task.question_options[:5]],
        }
    if include_details and task.result_text is not None:
        fact["result"] = task.result_text[:1_500]
    if include_details and task.error_code is not None:
        fact["error"] = {
            "code": task.error_code.value,
            "message": (task.error_message or "")[:500],
        }
    if task.pause_reason is not None:
        fact["pause_reason"] = task.pause_reason[:200]
    return fact


def _uuid(value: object, code: str) -> UUID:
    if not isinstance(value, str):
        raise ToolExecutorError(code)
    try:
        return UUID(value)
    except (ValueError, TypeError):
        raise ToolExecutorError(code) from None


__all__ = ["TaskBridge", "task_fact"]
