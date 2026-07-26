"""
负责进程内 Task 事件的有序广播、订阅队列管理及关闭语义。

runtime publisher 调用本模块发布已构造的事件 payload，gateway 的订阅消费者按 revision
读取快照和增量；本模块用有界队列保护慢消费者，并在溢出或关闭时终止订阅。本模块不将
runtime 快照投影为 wire 数据、不保存重放历史，也不拥有 Task 生命周期状态。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import uuid4

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


__all__ = ["TaskEventBroker", "TaskEventSubscription"]
