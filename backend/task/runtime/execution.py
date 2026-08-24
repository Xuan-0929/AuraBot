"""运行一个 Task 的 Pi session 主协程并投影为持久化产品状态。

``run_task`` 负责单次 queued→starting→running 执行、Pi event 消费、Question 提出、
active timeout 和 completed/failed 收口。进程内 registry、FIFO slot、锁与 fail-stop 由
TaskRuntimeEngine 拥有；SQLite transaction 与模型映射仍由 TaskStore 拥有。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Mapping
from uuid import UUID

from ..models import (
    TERMINAL_TASK_STATUSES,
    TaskErrorCode,
    TaskStatus,
)
from ..persistence.store import TaskPersistenceError, TaskStore
from ..settings import TaskSettings
from .process import (
    PiProcessStartError,
    PiProcessSupervisor,
    RunningPiProcess,
    StopIntent,
    TaskLaunchSpec,
)
from .questions import (
    continuation_prompt,
    dialog_response_id,
    parse_question_request,
)
from .rpc import PiProtocolError

if TYPE_CHECKING:
    from .engine import TaskRuntimeEngine


async def run_task(
    task_id: UUID,
    *,
    runtime: TaskRuntimeEngine,
    store: TaskStore,
    supervisor: PiProcessSupervisor,
    settings: TaskSettings,
) -> None:
    """Execute one admitted Task until completion, waiting, failure or cancellation."""

    try:
        async with runtime.task_guard(task_id):
            queued = await store.get_task(task_id)
            if queued.status != TaskStatus.QUEUED:
                return
            runtime.start_active_clock(queued)
            starting, _ = await store.transition_task(
                task_id,
                TaskStatus.STARTING,
                expected_version=queued.version,
                pi_session_id=str(task_id),
                pi_session_path=settings.session_directory(task_id),
            )
            await runtime.notify_committed_event()

        undelivered = await store.undelivered_answer(task_id)
        spec = TaskLaunchSpec(
            task_id=task_id,
            title=starting.title,
            prompt=starting.prompt,
            working_directory=starting.working_directory,
            continuation_prompt=continuation_prompt(queued, undelivered),
        )
        running_process = await asyncio.wait_for(
            supervisor.start_task(spec),
            timeout=runtime.remaining_seconds(starting),
        )
        async with runtime.task_guard(task_id):
            current = await store.get_task(task_id)
            if current.status != TaskStatus.STARTING:
                return
            if undelivered is not None:
                running, _, _ = await store.deliver_answer_and_transition(
                    task_id,
                    undelivered.id,
                    TaskStatus.RUNNING,
                    expected_version=current.version,
                )
            else:
                running, _ = await store.transition_task(
                    task_id,
                    TaskStatus.RUNNING,
                    expected_version=current.version,
                )
            del running
            await runtime.notify_committed_event()

        await _consume_events(
            task_id,
            runtime=runtime,
            store=store,
            supervisor=supervisor,
            running_process=running_process,
        )
    except asyncio.CancelledError:
        raise
    except TaskPersistenceError:
        # Engine owns the one fail-stop path and must see durability loss directly.
        raise
    except TimeoutError:
        await _fail_task(
            task_id,
            TaskErrorCode.TIMEOUT,
            "Task exceeded its active runtime timeout.",
            runtime=runtime,
            store=store,
            supervisor=supervisor,
        )
    except PiProcessStartError as error:
        await _fail_task(
            task_id,
            error.code,
            str(error),
            runtime=runtime,
            store=store,
            supervisor=supervisor,
        )
    except PiProtocolError:
        await _fail_task(
            task_id,
            TaskErrorCode.PROTOCOL_ERROR,
            "Pi violated the RPC protocol.",
            runtime=runtime,
            store=store,
            supervisor=supervisor,
        )
    except BaseException:
        await _fail_task(
            task_id,
            TaskErrorCode.INTERNAL_ERROR,
            "Task execution failed internally.",
            runtime=runtime,
            store=store,
            supervisor=supervisor,
        )


async def _consume_events(
    task_id: UUID,
    *,
    runtime: TaskRuntimeEngine,
    store: TaskStore,
    supervisor: PiProcessSupervisor,
    running_process: RunningPiProcess,
) -> None:
    iterator = running_process.rpc.events()
    last_run_model_failed = False
    while True:
        current = await store.get_task(task_id)
        try:
            if current.status == TaskStatus.WAITING:
                event = await anext(iterator)
            else:
                event = await asyncio.wait_for(
                    anext(iterator), timeout=runtime.remaining_seconds(current)
                )
        except StopAsyncIteration:
            current = await store.get_task(task_id)
            if current.status == TaskStatus.WAITING:
                return
            raise PiProcessStartError(
                TaskErrorCode.UNEXPECTED_EXIT,
                "Pi exited before the Task reached agent_settled.",
            )

        if event.type == "text_output":
            text = event.payload.get("text")
            if isinstance(text, str) and text:
                await store.append_output(task_id, text)
                await runtime.notify_committed_event()
        elif event.type == "extension_ui_request":
            await _handle_extension_ui(
                task_id,
                event.payload,
                runtime=runtime,
                store=store,
                running_process=running_process,
            )
        elif event.type == "extension_error":
            raise PiProcessStartError(
                TaskErrorCode.RESOURCE_LOAD_FAILED,
                "A Pi extension failed while running the Task.",
            )
        elif event.type == "agent_end":
            last_run_model_failed = _agent_end_has_model_error(event.payload)
        elif event.type == "agent_settled":
            latest = await store.get_task(task_id)
            if latest.status == TaskStatus.WAITING:
                continue
            if last_run_model_failed:
                await _fail_task(
                    task_id,
                    TaskErrorCode.MODEL_ERROR,
                    "Pi model execution failed.",
                    runtime=runtime,
                    store=store,
                    supervisor=supervisor,
                )
                return
            result = await running_process.last_assistant_text()
            await supervisor.stop(task_id, StopIntent.COMPLETE)
            if result is None:
                await _fail_task(
                    task_id,
                    TaskErrorCode.RESULT_UNAVAILABLE,
                    "Pi completed without a final assistant result.",
                    runtime=runtime,
                    store=store,
                    supervisor=supervisor,
                )
                return
            async with runtime.task_guard(task_id):
                current = await store.get_task(task_id)
                await store.transition_task(
                    task_id,
                    TaskStatus.COMPLETED,
                    expected_version=current.version,
                    active_runtime_ms=runtime.stop_active_clock(current),
                    result_text=result,
                )
                await runtime.notify_committed_event()
            return


async def _handle_extension_ui(
    task_id: UUID,
    payload: object,
    *,
    runtime: TaskRuntimeEngine,
    store: TaskStore,
    running_process: RunningPiProcess,
) -> None:
    question = parse_question_request(payload)
    if question is None:
        request_id = dialog_response_id(payload)
        if request_id is not None:
            await running_process.rpc.send_extension_ui_response(request_id, None)
        return

    async with runtime.task_guard(task_id):
        current = await store.get_task(task_id)
        await store.raise_question(
            task_id,
            question.question,
            question.options,
            question.request_id,
            expected_version=current.version,
            active_runtime_ms=runtime.stop_active_clock(current),
        )
        await runtime.notify_committed_event()
        runtime.schedule_hibernate(task_id)


async def _fail_task(
    task_id: UUID,
    code: TaskErrorCode,
    message: str,
    *,
    runtime: TaskRuntimeEngine,
    store: TaskStore,
    supervisor: PiProcessSupervisor,
) -> None:
    await supervisor.stop(task_id, StopIntent.CANCEL)
    async with runtime.task_guard(task_id):
        current = await store.get_task(task_id)
        if current.status in TERMINAL_TASK_STATUSES or current.status == TaskStatus.PAUSED:
            return
        if current.status == TaskStatus.WAITING:
            current, _ = await store.transition_task(
                task_id,
                TaskStatus.CANCELLING,
                expected_version=current.version,
                active_runtime_ms=runtime.stop_active_clock(current),
            )
            await runtime.notify_committed_event()
        await store.transition_task(
            task_id,
            TaskStatus.FAILED,
            expected_version=current.version,
            active_runtime_ms=runtime.stop_active_clock(current),
            error_code=code,
            error_message=message,
        )
        await runtime.notify_committed_event()


def _agent_end_has_model_error(payload: object) -> bool:
    """Read only the final assistant stop reason from one Pi agent_end event."""

    if not isinstance(payload, Mapping):
        return False
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return False
    return any(
        isinstance(message, Mapping)
        and message.get("role") == "assistant"
        and message.get("stopReason") == "error"
        for message in reversed(messages)
    )


__all__ = ["run_task"]
