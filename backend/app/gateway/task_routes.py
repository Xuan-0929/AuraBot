"""
把版本化 Task REST/SSE 请求映射到 TaskManager 稳定门面。

路由只做输入验证、产品 DTO 映射、稳定错误 envelope 和数据库 cursor SSE；它不持有
subprocess、不解析 Pi event，也不把 Pi 原始协议或私有 session 数据暴露给客户端。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import cast
from uuid import UUID

from fastapi import APIRouter, FastAPI, Header, Query, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse

from ...task import (
    TaskCommandError,
    TaskManager,
    TaskNotFound,
    TaskRequestError,
    TaskRuntimeUnavailable,
    TaskStateConflict,
    TaskStatus,
    TaskVersionConflict,
)
from .errors import error_response
from .task_schemas import (
    CreateTaskRequest,
    TaskCommandRequest,
    TaskEventResponse,
    TaskPageResponse,
    TaskResponse,
)

router = APIRouter(prefix="/api/v1", tags=["tasks"])
SSE_KEEPALIVE_SECONDS = 15.0


def _manager(request: Request) -> TaskManager:
    return cast(TaskManager, request.app.state.task_manager)


@router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(body: CreateTaskRequest, request: Request) -> TaskResponse:
    return TaskResponse.from_record(
        await _manager(request).create_task(body.to_command())
    )


@router.get("/tasks", response_model=TaskPageResponse)
async def list_tasks(
    request: Request,
    cursor: int | None = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    task_status: list[TaskStatus] | None = Query(default=None, alias="status"),
) -> TaskPageResponse:
    page = await _manager(request).list_tasks(
        after_row_id=cursor, limit=limit, statuses=task_status
    )
    return TaskPageResponse.from_page(page)


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: UUID, request: Request) -> TaskResponse:
    return TaskResponse.from_record(await _manager(request).get_task(task_id))


@router.post("/tasks/{task_id}/pause", response_model=TaskResponse)
async def pause_task(
    task_id: UUID, body: TaskCommandRequest, request: Request
) -> TaskResponse:
    return TaskResponse.from_record(
        await _manager(request).pause_task(task_id, body.expected_version)
    )


@router.post("/tasks/{task_id}/resume", response_model=TaskResponse)
async def resume_task(
    task_id: UUID, body: TaskCommandRequest, request: Request
) -> TaskResponse:
    return TaskResponse.from_record(
        await _manager(request).resume_task(task_id, body.expected_version)
    )


@router.post("/tasks/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(
    task_id: UUID, body: TaskCommandRequest, request: Request
) -> TaskResponse:
    return TaskResponse.from_record(
        await _manager(request).cancel_task(task_id, body.expected_version)
    )


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: UUID, request: Request) -> Response:
    await _manager(request).delete_task(task_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/task-events")
async def task_events(
    request: Request,
    task_id: UUID | None = Query(default=None, alias="taskId"),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    try:
        cursor = int(last_event_id) if last_event_id is not None else 0
    except ValueError as exc:
        raise TaskRequestError("Last-Event-ID must be a non-negative integer.") from exc
    if cursor < 0:
        raise TaskRequestError("Last-Event-ID must be a non-negative integer.")

    async def stream() -> AsyncIterator[str]:
        iterator = _manager(request).iter_events(cursor, task_id)
        pending = asyncio.create_task(anext(iterator), name="task-sse-next-event")
        try:
            while True:
                done, _ = await asyncio.wait(
                    {pending}, timeout=SSE_KEEPALIVE_SECONDS
                )
                if not done:
                    yield ": keepalive\n\n"
                    continue
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    return
                public = TaskEventResponse.from_event(event)
                yield (
                    f"id: {event.event_id}\n"
                    f"event: {event.type}\n"
                    f"data: {public.model_dump_json(by_alias=True)}\n\n"
                )
                pending = asyncio.create_task(
                    anext(iterator), name="task-sse-next-event"
                )
        finally:
            if not pending.done():
                pending.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pending
            with contextlib.suppress(RuntimeError):
                await iterator.aclose()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def register_task_exception_handlers(application: FastAPI) -> None:
    """Install stable mappings for errors intentionally exposed by TaskManager."""

    async def not_found(_request: Request, exception: TaskNotFound) -> JSONResponse:
        return error_response(404, "task_not_found", str(exception))

    async def state_conflict(
        _request: Request, exception: TaskStateConflict
    ) -> JSONResponse:
        return error_response(409, "task_state_conflict", str(exception))

    async def version_conflict(
        _request: Request, exception: TaskVersionConflict
    ) -> JSONResponse:
        return error_response(409, "task_version_conflict", str(exception))

    async def unavailable(
        _request: Request, exception: TaskRuntimeUnavailable
    ) -> JSONResponse:
        return error_response(503, "task_runtime_unavailable", str(exception))

    async def invalid_command(
        _request: Request, exception: TaskCommandError | TaskRequestError
    ) -> JSONResponse:
        return error_response(400, "invalid_task_request", str(exception))

    application.add_exception_handler(TaskNotFound, not_found)
    application.add_exception_handler(TaskStateConflict, state_conflict)
    application.add_exception_handler(TaskVersionConflict, version_conflict)
    application.add_exception_handler(TaskRuntimeUnavailable, unavailable)
    application.add_exception_handler(TaskCommandError, invalid_command)
    application.add_exception_handler(TaskRequestError, invalid_command)


__all__ = ["register_task_exception_handlers", "router"]
