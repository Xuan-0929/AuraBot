"""
负责进程内 Task 事件广播，以及 runtime snapshot 到传输字典的当前投影。

runtime 发布快照和队列变化后，本模块按 revision 扇出至订阅队列；gateway 消费这些
事件形成 SSE。本模块暂不持久化历史，也不拥有 Task 调度或生命周期状态。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import uuid4

from .runtime import TaskSnapshot
from .domain import TaskType

_CLOSED = object()


class TaskEventSubscription:
    def __init__(
        self,
        broker: "TaskEventBroker",
        queue: asyncio.Queue[dict[str, Any] | object],
    ):
        self._broker = broker
        self._queue = queue
        self._closed = False

    async def next_event(
        self,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if self._closed:
            raise StopAsyncIteration
        try:
            item = (
                await self._queue.get()
                if timeout is None
                else await asyncio.wait_for(self._queue.get(), timeout)
            )
        except asyncio.TimeoutError:
            raise
        if item is _CLOSED:
            self._closed = True
            self._broker.unsubscribe(self)
            raise StopAsyncIteration
        return item

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._broker.unsubscribe(self)


class TaskEventBroker:
    """Broadcast ordered runtime deltas without retaining replay history."""

    def __init__(
        self,
        *,
        queue_size: int = 256,
        stream_id: str | None = None,
    ):
        if (
            not isinstance(queue_size, int)
            or isinstance(queue_size, bool)
            or queue_size < 1
        ):
            raise ValueError("queue_size must be a positive integer")
        self._queue_size = queue_size
        self._stream_id = stream_id or str(uuid4())
        self._revision = 0
        self._subscriptions: dict[
            TaskEventSubscription,
            asyncio.Queue[dict[str, Any] | object],
        ] = {}
        self._closed = False
        self._lock = RLock()

    @property
    def stream_id(self) -> str:
        return self._stream_id

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def subscribe(
        self,
        *,
        tasks: list[dict[str, Any]],
        running_count: int,
        queued_count: int,
    ) -> TaskEventSubscription:
        with self._lock:
            if self._closed:
                raise RuntimeError("TaskEventBroker is closed")
            # One physical slot is reserved for the close sentinel. Event
            # overflow is still evaluated against the configured queue size,
            # so graceful shutdown can terminate after draining every event
            # that the broker already accepted.
            queue: asyncio.Queue[dict[str, Any] | object] = asyncio.Queue(
                maxsize=self._queue_size + 1
            )
            subscription = TaskEventSubscription(self, queue)
            queue.put_nowait(
                self._envelope(
                    "task.snapshot",
                    self._revision,
                    {"tasks": tasks},
                    running_count,
                    queued_count,
                )
            )
            self._subscriptions[subscription] = queue
            return subscription

    def unsubscribe(self, subscription: TaskEventSubscription) -> None:
        with self._lock:
            self._subscriptions.pop(subscription, None)

    def publish(
        self,
        event: str,
        payload: dict[str, Any],
        *,
        running_count: int,
        queued_count: int,
    ) -> dict[str, Any]:
        if not isinstance(event, str) or not event:
            raise ValueError("event must be a non-empty string")
        with self._lock:
            if self._closed:
                raise RuntimeError("TaskEventBroker is closed")
            self._revision += 1
            envelope = self._envelope(
                event,
                self._revision,
                payload,
                running_count,
                queued_count,
            )
            for subscription, queue in tuple(
                self._subscriptions.items()
            ):
                if queue.qsize() >= self._queue_size:
                    self._subscriptions.pop(subscription, None)
                    _replace_queue_with_closed(queue)
                else:
                    queue.put_nowait(envelope)
            return envelope

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            subscriptions = tuple(self._subscriptions.values())
            self._subscriptions.clear()
        for queue in subscriptions:
            _append_closed(queue)

    def _envelope(
        self,
        event: str,
        revision: int,
        payload: dict[str, Any],
        running_count: int,
        queued_count: int,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "stream_id": self._stream_id,
            "revision": revision,
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "event": event,
            "running_count": running_count,
            "queued_count": queued_count,
            "payload": payload,
        }


def task_snapshot_payload(snapshot: TaskSnapshot) -> dict[str, Any]:
    """Project an immutable Task snapshot into the SSE wire representation."""

    return {
        "task_id": snapshot.task_id,
        "task_name": snapshot.task_name,
        "task_type": {
            TaskType.ReAct: "react",
            TaskType.PlanAndSolve: "plan_and_solve",
            TaskType.Goal: "goal",
        }[snapshot.task_type],
        "state": snapshot.state.value.lower(),
        "queue_position": snapshot.queue_position,
        "created_at": _iso(snapshot.created_at),
        "started_at": _iso(snapshot.started_at),
        "settled_at": _iso(snapshot.settled_at),
        "result": snapshot.result,
        "error": (
            {
                "code": snapshot.error.code,
                "message": snapshot.error.message,
            }
            if snapshot.error is not None
            else None
        ),
    }


def _replace_queue_with_closed(
    queue: asyncio.Queue[dict[str, Any] | object],
) -> None:
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    queue.put_nowait(_CLOSED)


def _append_closed(
    queue: asyncio.Queue[dict[str, Any] | object],
) -> None:
    try:
        queue.put_nowait(_CLOSED)
    except asyncio.QueueFull:
        _replace_queue_with_closed(queue)


def _iso(value: datetime | None) -> str | None:
    return (
        value.isoformat().replace("+00:00", "Z")
        if value is not None
        else None
    )


__all__ = [
    "TaskEventBroker",
    "TaskEventSubscription",
    "task_snapshot_payload",
]
