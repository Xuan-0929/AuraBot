"""
负责将 Task 事件订阅转换为 SSE 响应，并编码快照加增量的传输边界。

本路由从应用服务取得 events 包公开的订阅对象，持续读取事件并使用运行时配置发送
keepalive；TaskEventBroker 负责事件次序和关闭，TaskEventPublisher 负责构造 payload。
本模块不维护订阅状态、不投影 runtime record，也不调度或持久化 Task。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ...agent.task.events import TaskEventSubscription
from ..runtime import BackendRuntime
from ..services.task_service import TaskApplicationService
from .dependencies import get_backend_runtime, get_task_service

router = APIRouter(prefix="/task", tags=["task"])
TaskServiceDependency = Annotated[
    TaskApplicationService,
    Depends(get_task_service),
]
RuntimeDependency = Annotated[BackendRuntime, Depends(get_backend_runtime)]


@router.get("/events")
async def task_events(
    service: TaskServiceDependency,
    runtime: RuntimeDependency,
) -> StreamingResponse:
    subscription: TaskEventSubscription = service.subscribe()

    async def generate() -> AsyncIterator[str]:
        try:
            while True:
                try:
                    event = await subscription.next_event(
                        runtime.settings.sse_keepalive_seconds
                    )
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                except StopAsyncIteration:
                    return
                else:
                    yield encode_sse_event(event)
        finally:
            subscription.close()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def encode_sse_event(event: dict[str, Any]) -> str:
    stream_id = event["stream_id"]
    revision = event["revision"]
    event_name = event["event"]
    data = json.dumps(
        event,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        f"id: {stream_id}:{revision}\n"
        f"event: {event_name}\n"
        f"data: {data}\n\n"
    )


__all__ = ["encode_sse_event", "router", "task_events"]
