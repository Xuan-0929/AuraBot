"""
提供 TaskManager 使用的内存 FIFO 队列与并发准入槽。

FifoScheduler 只保存 Task UUID 的 queued/active 集合，通过 asyncio.Condition 协调
enqueue、remove、admit、release 和 shutdown；它不访问 SQLite、Pi RPC 或进程句柄。
"""

from __future__ import annotations

import asyncio
from collections import deque
from uuid import UUID


class SchedulerClosed(RuntimeError):
    """Raised when admission has permanently stopped for Backend shutdown."""


class FifoScheduler:
    """A duplicate-free FIFO queue with an explicit active-slot set."""

    def __init__(self, concurrency_limit: int) -> None:
        if concurrency_limit < 1:
            raise ValueError("concurrency_limit must be at least 1")
        self._limit = concurrency_limit
        self._queue: deque[UUID] = deque()
        self._queued: set[UUID] = set()
        self._active: set[UUID] = set()
        self._closed = False
        self._condition = asyncio.Condition()

    async def enqueue(self, task_id: UUID) -> None:
        """Append a non-active Task once, preserving resume-at-tail semantics."""

        async with self._condition:
            if self._closed:
                raise SchedulerClosed("Task scheduler is shut down.")
            if task_id not in self._queued and task_id not in self._active:
                self._queue.append(task_id)
                self._queued.add(task_id)
                self._condition.notify_all()

    async def remove(self, task_id: UUID) -> bool:
        """Remove a queued Task without touching an admitted execution."""

        async with self._condition:
            if task_id not in self._queued:
                return False
            self._queued.remove(task_id)
            self._queue.remove(task_id)
            return True

    async def admit(self) -> UUID:
        """Wait for the oldest Task and one available concurrency slot."""

        async with self._condition:
            while True:
                if self._closed:
                    raise SchedulerClosed("Task scheduler is shut down.")
                if self._queue and len(self._active) < self._limit:
                    task_id = self._queue.popleft()
                    self._queued.remove(task_id)
                    self._active.add(task_id)
                    return task_id
                await self._condition.wait()

    async def release(self, task_id: UUID) -> None:
        """Release one admitted slot and wake the next FIFO waiter."""

        async with self._condition:
            if task_id in self._active:
                self._active.remove(task_id)
                self._condition.notify_all()

    async def shutdown(self) -> None:
        """Permanently stop admission while retaining the in-memory queue snapshot."""

        async with self._condition:
            self._closed = True
            self._condition.notify_all()

    @property
    def queued_count(self) -> int:
        return len(self._queued)

    @property
    def active_count(self) -> int:
        return len(self._active)


__all__ = ["FifoScheduler", "SchedulerClosed"]
