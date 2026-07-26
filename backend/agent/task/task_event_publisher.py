"""
负责将 Task runtime 的内部 record 转为不可变快照，并发布事件层可广播的 payload。

manager 在生命周期变化时调用本发布器；本模块先通过 runtime.snapshot_task 隔离可变
record，再交由 events 的公开投影和 broker 接口供 gateway 订阅。本模块不保存事件历史、
不实现队列订阅细节，也不处理 SSE HTTP 编码。
"""

from collections.abc import Callable
from typing import Any

from .events import (
    TaskEventBroker,
    TaskEventSubscription,
    task_snapshot_payload,
)
from .runtime import _TaskRuntimeRecord, snapshot_task


class TaskEventPublisher:
    def __init__(
        self,
        broker: TaskEventBroker,
        *,
        records: Callable[[], tuple[_TaskRuntimeRecord, ...]],
        ready: Callable[[], tuple[_TaskRuntimeRecord, ...]],
        counts: Callable[[], tuple[int, int]],
        is_closing: Callable[[], bool],
    ):
        self._broker = broker
        self._records = records
        self._ready = ready
        self._counts = counts
        self._is_closing = is_closing

    def subscribe(self) -> TaskEventSubscription:
        running_count, queued_count = self._counts()
        return self._broker.subscribe(
            tasks=[
                self._snapshot_payload(record)
                for record in self._records()
            ],
            running_count=running_count,
            queued_count=queued_count,
        )

    def publish(
        self,
        event: str,
        record: _TaskRuntimeRecord | None = None,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if self._is_closing():
            return
        payload = dict(extra or {})
        if record is not None:
            payload["task"] = self._snapshot_payload(record)
        self._try_publish(event, payload)

    def publish_queue(self) -> None:
        self.publish(
            "task.queue_changed",
            extra={
                "queue": [
                    {
                        "task_name": record.task.task_title,
                        "queue_position": record.queue_position,
                    }
                    for record in self._ready()
                ]
            },
        )

    def publish_runtime_closing(self) -> None:
        self._try_publish("runtime.closing", {})

    def close(self) -> None:
        self._broker.close()

    def _snapshot_payload(
        self,
        record: _TaskRuntimeRecord,
    ) -> dict[str, object]:
        snapshot = snapshot_task(record)
        return task_snapshot_payload(snapshot)

    def _try_publish(self, event: str, payload: dict[str, Any]) -> None:
        running_count, queued_count = self._counts()
        try:
            self._broker.publish(
                event,
                payload,
                running_count=running_count,
                queued_count=queued_count,
            )
        except RuntimeError:
            pass


__all__ = ["TaskEventPublisher"]
