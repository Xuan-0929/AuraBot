"""
提供 TaskRuntimeEngine 使用的内存 FIFO 队列与并发准入槽。

FIFOScheduler 不创建线程，也不亲自执行 Task。它只在一个 asyncio event loop 中保存
Task UUID 的排队顺序和已占用槽位，通过 asyncio.Condition 让入队、准入、释放和关闭
安全地修改同一组内存状态，并唤醒正在等待“队列非空且存在空闲槽”的协程。

本模块不访问 SQLite、Pi RPC 或进程句柄：持久化状态由 TaskStore 负责，Task 的实际
执行由 TaskRuntimeEngine 创建的 asyncio.Task 负责，Pi 子进程由 Supervisor 管理。
"""

from __future__ import annotations

import asyncio
from collections import deque
from uuid import UUID


class SchedulerClosed(RuntimeError):
    """表示 Backend shutdown 已永久关闭新的 Task 调度准入。"""


class FIFOScheduler:
    """维护无重复的 FIFO 等待队列和受并发上限约束的 active 集合。

    ``_queue`` 决定等待 Task 的先后顺序，``_queued`` 提供常数时间的重复检查，
    ``_active`` 表示已经获得并发槽且尚未 release 的 Task。对这些共享状态的读写都在
    ``asyncio.Condition`` 的锁内完成，避免多个协程在 ``await`` 切换点交错修改状态。
    """

    def __init__(self, concurrency_limit: int) -> None:
        """创建一个最多允许 ``concurrency_limit`` 项 Task 同时 active 的调度器。

        并发上限必须至少为 1。

        构造函数只初始化进程内状态，不启动 dispatcher、协程
        或子进程；后续由 TaskRuntimeEngine 循环调用 ``admit()`` 获取可执行的 Task。
        """

        if concurrency_limit < 1:
            raise ValueError("concurrency_limit must be at least 1")
        self._limit = concurrency_limit

        # deque 保存真正的 FIFO 顺序：左侧是下一项应被准入的 Task。
        self._queue: deque[UUID] = deque()

        # set 与 deque 保存相同的 queued 成员，用于快速判断 Task 是否已经排队。
        self._queued: set[UUID] = set()

        # active 中的每个 Task 占用一个并发槽，直到调用 release() 才释放。
        self._active: set[UUID] = set()

        # shutdown 后调度器不可重新打开；等待中的 admit() 会被唤醒并抛出异常。
        self._closed = False

        # Condition 同时提供互斥锁和等待/通知机制。协程调用 wait() 时会临时释放锁，
        # 被 notify_all() 唤醒后重新获取锁，再次检查队列、槽位和关闭状态。
        self._condition = asyncio.Condition()

    async def enqueue(self, task_id: UUID) -> None:
        """把一项尚未排队且未运行的 Task 追加到 FIFO 队尾。

        同一个 Task 不会重复进入队列，也不会在仍占用 active 槽时再次入队。恢复或休眠
        后重新排队的 Task 会追加到队尾，不会插到已有等待者之前。成功入队后调用
        ``notify_all()``，让可能阻塞在 ``admit()`` 的 dispatcher 重新检查准入条件。

        调度器已 shutdown 时抛出 ``SchedulerClosed``；重复入队则保持幂等，不做修改。
        """

        async with self._condition:
            if self._closed:
                raise SchedulerClosed("Task scheduler is shut down.")
            if task_id not in self._queued and task_id not in self._active:
                self._queue.append(task_id)
                self._queued.add(task_id)
                self._condition.notify_all()

    async def remove(self, task_id: UUID) -> bool:
        """从等待队列移除一项尚未获得并发槽的 Task。

        本方法同时更新用于 FIFO 顺序的 ``_queue`` 和用于成员检查的 ``_queued``，但不
        操作 ``_active``，因此不能用来中断已经准入的 execution。Task 原本在队列中时
        返回 ``True``；Task 不在等待队列中时返回 ``False``，并保持状态不变。
        """

        async with self._condition:
            if task_id not in self._queued:
                return False
            self._queued.remove(task_id)
            self._queue.remove(task_id)
            return True

    async def admit(self) -> UUID:
        """等待并返回 FIFO 队首中可以占用一个并发槽的 Task。

        被 TaskRuntimeEngine 的 dispatcher 调用，用于调度新的 Task。

        准入条件必须同时满足：等待队列非空，并且 ``active_count`` 小于并发上限。如果
        条件暂不满足，``Condition.wait()`` 会挂起当前协程并释放 Condition 的锁，使
        ``enqueue()``、``release()`` 或 ``shutdown()`` 可以进入临界区修改状态。

        被通知后必须使用 ``while`` 重新检查条件，而不能假设一次唤醒就一定有可用 Task：
        多个等待协程可能同时被唤醒，状态也可能在当前协程重新拿到锁之前再次改变。
        成功准入时，从 deque 左侧取出最老 Task，并在同一临界区内把它从 queued 移入
        active，确保不会出现两个协程准入同一 Task。调度器关闭时抛出
        ``SchedulerClosed``，使 TaskRuntimeEngine 的 dispatcher 能退出循环。
        """

        async with self._condition:
            while True:
                if self._closed:
                    raise SchedulerClosed("Task scheduler is shut down.")
                if self._queue and len(self._active) < self._limit:
                    task_id = self._queue.popleft()
                    self._queued.remove(task_id)
                    self._active.add(task_id)
                    return task_id

                # wait() 不会阻塞整个线程；它只暂停当前协程，让 event loop 继续运行
                # enqueue/release/shutdown 等其他协程，收到通知后再从这里继续检查。
                await self._condition.wait()

    async def release(self, task_id: UUID) -> None:
        """释放一项已准入 Task 占用的并发槽，并唤醒等待准入的协程。

        Task execution 无论成功、失败、被取消或进入等待，都应最终调用本方法。只有 Task 确实在
        ``_active`` 中时才修改状态并发送通知；重复 release 保持幂等。通知本身不会把
        某项 Task 直接交给执行器，它只让 ``admit()`` 重新竞争锁并检查 FIFO 准入条件。
        """

        async with self._condition:
            if task_id in self._active:
                self._active.remove(task_id)
                self._condition.notify_all()

    async def shutdown(self) -> None:
        """永久关闭新准入，并唤醒所有阻塞在 ``admit()`` 的协程。

        shutdown 不清空 ``_queue`` 或 ``_active``，也不负责取消正在执行的 Task；这些
        生命周期由 TaskRuntimeEngine 和 PiProcessSupervisor 收口。``notify_all()``
        确保即使
        队列为空或并发槽已满，等待中的 ``admit()`` 也能醒来观察 ``_closed`` 并抛出
        ``SchedulerClosed``，从而避免 Backend 关闭时留下永远挂起的 dispatcher。
        """

        async with self._condition:
            self._closed = True
            self._condition.notify_all()

    @property
    def queued_count(self) -> int:
        """返回当前仍在等待 FIFO 准入的 Task 数量。"""

        return len(self._queued)

    @property
    def active_count(self) -> int:
        """返回当前已获得并发槽但尚未 release 的 Task 数量。"""

        return len(self._active)


__all__ = ["FIFOScheduler", "SchedulerClosed"]
