"""Application-owned Task admission, observation and retention."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from ..capabilities.lease import CapabilitySession
from .admission import TaskAdmissionController
from .errors import DuplicateTaskNameError, TaskManagerClosedError
from .interruption import TaskInterruptionController
from .model import Task, TaskState
from .events import TaskEventBroker, TaskEventSubscription
from .harness.harness import Harness
from .harness.factory import HarnessFactory
from .runtime import (
    InterruptTaskResult,
    TaskListSnapshot,
    TaskSnapshot,
    TaskStartSpec,
    _TaskRuntimeRecord,
    snapshot_task,
)
from .runtime_registry import TaskRuntimeRegistry
from .retention import TaskRetentionController
from .scheduler import TERMINAL_STATES, TaskScheduler
from .shutdown import close_task_runtime, shutdown_task_runtime
from .task_event_publisher import TaskEventPublisher
from .validation import validate_manager_options


class TaskManager:
    """Own every Task from name reservation through retained cleanup."""

    TERMINAL_RETENTION_SECONDS = 100.0

    def __init__(
        self,
        *,
        harness_factory: HarnessFactory | Any | None = None,
        capability_session_factory: (
            Callable[[], CapabilitySession] | None
        ) = None,
        event_broker: TaskEventBroker | None = None,
        max_running: int = 8,
        default_timeout_ms: int = 300_000,
        max_timeout_ms: int = 1_800_000,
        retention_seconds: float = TERMINAL_RETENTION_SECONDS,
    ):
        validate_manager_options(
            max_running=max_running,
            default_timeout_ms=default_timeout_ms,
            max_timeout_ms=max_timeout_ms,
            retention_seconds=retention_seconds,
            capability_session_factory=capability_session_factory,
        )

        self._registry = TaskRuntimeRegistry()
        broker = event_broker or TaskEventBroker()
        self._events = TaskEventPublisher(
            broker,
            records=self._registry.list,
            ready=lambda: self._scheduler.ready,
            counts=lambda: (self.running_count, self.queued_count),
            is_closing=lambda: self._closing,
        )
        self._scheduler = TaskScheduler(
            max_running=max_running,
            harness_factory=harness_factory,
            capability_session_factory=capability_session_factory,
            is_retained=self._registry.owns,
            on_started=self._on_runtime_started,
            on_settled=self._on_runtime_settled,
            on_queue_changed=self._events.publish_queue,
        )
        self._admission = TaskAdmissionController(
            registry=self._registry,
            scheduler=self._scheduler,
            events=self._events,
            state_observer=self._on_state_change,
            is_closing=lambda: self._closing,
            default_timeout_ms=default_timeout_ms,
            max_timeout_ms=max_timeout_ms,
        )
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None
        self._retention = TaskRetentionController(
            seconds=float(retention_seconds),
            registry=self._registry,
            scheduler=self._scheduler,
            events=self._events,
            event_loop=lambda: self._main_loop,
            is_closing=lambda: self._closing,
        )
        self._interruption = TaskInterruptionController(
            registry=self._registry,
            scheduler=self._scheduler,
            retention=self._retention,
            events=self._events,
        )

    @property
    def retention_seconds(self) -> float:
        return self._retention.seconds

    @property
    def running_count(self) -> int:
        return self._scheduler.running_count

    @property
    def queued_count(self) -> int:
        return self._scheduler.queued_count

    @property
    def closing(self) -> bool:
        return self._closing

    def bind_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """绑定创建 runner、retention timer 和 shutdown task 的主事件循环。

        生产环境由 ``BackendRuntime`` 在 FastAPI lifespan 启动阶段调用。传入
        非 asyncio event loop 会抛出 ``TypeError``。
        """

        if not isinstance(loop, asyncio.AbstractEventLoop):
            raise TypeError("loop must be an asyncio event loop")
        self._main_loop = loop

    def _require_main_loop(self) -> asyncio.AbstractEventLoop:
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError(
                "TaskManager mutations require its asyncio event loop"
            ) from exc
        if self._main_loop is None:
            self._main_loop = running_loop
        elif self._main_loop is not running_loop:
            raise RuntimeError(
                "TaskManager cannot be used from another event loop"
            )
        return running_loop

    def start_task(self, spec: TaskStartSpec) -> TaskSnapshot:
        """Reserve, enqueue and return an immutable Task snapshot."""

        self._require_main_loop()
        return self._admission.start(spec)

    def get_task(self, task_name: str) -> TaskSnapshot | None:
        record = self._registry.get(task_name)
        return snapshot_task(record) if record is not None else None

    def list_tasks(self) -> TaskListSnapshot:
        return TaskListSnapshot(
            running_count=self.running_count,
            queued_count=self.queued_count,
            tasks=tuple(
                snapshot_task(record)
                for record in self._registry.list()
            ),
        )

    def get_runtime(self, task_name: str) -> Harness | None:
        """返回指定 Task 已构造的 Harness runtime，未找到或尚在排队则返回 ``None``。

        该接口主要供内部 Plan Application Service 根据 ``task_name`` 路由到
        ``PlanAndSolveHarness``；它不负责验证具体 Harness 类型。
        """

        record = self._registry.get(task_name)
        return record.harness if record is not None else None

    def subscribe_events(self) -> TaskEventSubscription:
        """创建 Task 状态事件订阅，并把当前原子快照作为第一条事件排队。

        后续事件由 ``TaskEventBroker`` 按 revision 有序投递。调用方必须最终关闭
        subscription；慢订阅者队列溢出时 broker 会主动结束该订阅。
        """

        return self._events.subscribe()

    def interrupt_task(self, task_name: str) -> InterruptTaskResult:
        """Idempotently interrupt a queued or running Task."""

        self._require_main_loop()
        return self._interruption.interrupt(task_name)

    def clear_task(self, task_name: str) -> bool:
        """立即删除一个 runner 已退出的终态 retained runtime。

        成功时取消 retention timer、注销状态 observer 并关闭 capability lease。
        Task 不存在、仍在运行或尚未进入终态时返回 ``False``。
        """

        self._require_main_loop()
        record = self._registry.get(task_name)
        if (
            record is None
            or record.task.state not in TERMINAL_STATES
            or record.runner is not None
        ):
            return False
        self._retention.remove(record, publish=True)
        return True

    async def aclose(self, *, grace_seconds: float = 5.0) -> None:
        """异步停止准入并完整关闭所有 Task runtime。

        关闭时发布 ``runtime.closing``、打断 queued/live Task、在
        ``grace_seconds`` 内等待 runner，随后取消仍未退出的 runner，释放 retained
        capability 并关闭事件流。多个调用方共享同一个 shielded close task，因此某个
        等待方被取消不会中断实际清理；重复调用是幂等的。
        """

        if grace_seconds < 0:
            raise ValueError("grace_seconds must be non-negative")
        self._require_main_loop()
        close_task = self._close_task
        if close_task is None:
            loop = asyncio.get_running_loop()
            close_task = loop.create_task(
                self._close_runtime(grace_seconds),
                name="aurabot-task-manager-close",
            )
            self._close_task = close_task
        await asyncio.shield(close_task)

    async def _close_runtime(self, grace_seconds: float) -> None:
        self._closing = True
        try:
            await close_task_runtime(
                grace_seconds=grace_seconds,
                registry=self._registry,
                scheduler=self._scheduler,
                events=self._events,
                interrupt=self.interrupt_task,
                remove=lambda record: self._retention.remove(
                    record,
                    publish=False,
                ),
            )
        finally:
            self._main_loop = None

    def shutdown(self) -> None:
        """立即执行同步清理，仅供没有活动事件循环的低层测试使用。

        该方法不会等待 runner 的 finally block，不应替代生产环境中的
        :meth:`aclose`。FastAPI/BackendRuntime shutdown 必须始终调用异步关闭接口。
        """

        self._closing = True
        try:
            shutdown_task_runtime(
                registry=self._registry,
                scheduler=self._scheduler,
                events=self._events,
                remove=lambda record: self._retention.remove(
                    record,
                    publish=False,
                ),
            )
        finally:
            self._main_loop = None

    def _on_runtime_started(self, record: _TaskRuntimeRecord) -> None:
        self._events.publish("task.started", record)

    def _on_runtime_settled(self, record: _TaskRuntimeRecord) -> None:
        self._events.publish("task.settled", record)
        self._retention.schedule(record)

    def _on_state_change(
        self,
        task: Task,
        previous_state: TaskState,
        current_state: TaskState,
    ) -> None:
        record = self._registry.get(task.task_title)
        if record is None or record.task is not task:
            return
        self._events.publish(
            "task.state_changed",
            record,
            extra={
                "previous_state": previous_state.value.lower(),
                "state": current_state.value.lower(),
            },
        )


__all__ = [
    "DuplicateTaskNameError",
    "TaskManager",
    "TaskManagerClosedError",
]
