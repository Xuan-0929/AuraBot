"""Application-owned Task admission, execution, observation and retention."""

from __future__ import annotations

import asyncio
import re
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from ..capabilities.lease import CapabilitySession
from ..skills.SkillGroupManager import SkillGroupManager
from ..tools.ToolGroupManager import ToolGroupManager
from .Task import Task
from .TaskEntity import (
    InterruptTaskOutcome,
    InterruptTaskResult,
    PeekTaskResult,
    TaskState,
    TaskType,
)
from .events import TaskEventBroker, TaskEventSubscription
from .harness.Harness import Harness
from .harness.HarnessFactory import HarnessFactory
from .runtime import TaskRuntimeRecord, TaskStartSpec


TERMINAL_STATES = {
    TaskState.Completed,
    TaskState.Failed,
    TaskState.Interrupted,
}
ACTIVE_STATES = {
    TaskState.Initializing,
    TaskState.Thinking,
    TaskState.Pushing,
    TaskState.Planning,
    TaskState.Concluding,
}
_TASK_NAME_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]")


class DuplicateTaskNameError(ValueError):
    """Raised while a live or retained Task still owns a public name."""


class TaskManagerClosedError(RuntimeError):
    """Raised when admission is attempted during application shutdown."""


class TaskManager:
    """Own every Task from name reservation through retained cleanup."""

    TERMINAL_RETENTION_SECONDS = 100.0

    def __init__(
        self,
        *,
        harness_factory: HarnessFactory | Any | None = None,
        tool_group_manager: ToolGroupManager | None = None,
        skill_group_manager: SkillGroupManager | None = None,
        event_broker: TaskEventBroker | None = None,
        max_running: int = 8,
        default_timeout_ms: int = 300_000,
        max_timeout_ms: int = 1_800_000,
        retention_seconds: float = TERMINAL_RETENTION_SECONDS,
    ):
        """构造一个 application-owned Task 运行时管理器。

        Manager 自身不使用进程全局单例；``BackendRuntime`` 应为每个 backend
        application 构造并持有唯一实例。构造阶段只保存依赖与准入配置，不创建
        Task、Harness 或 asyncio runner。正式接收 Task 前应调用
        :meth:`bind_event_loop` 绑定 backend 主事件循环。
        """

        if (
            not isinstance(max_running, int)
            or isinstance(max_running, bool)
            or max_running < 1
        ):
            raise ValueError("max_running must be a positive integer")
        if (
            not isinstance(default_timeout_ms, int)
            or isinstance(default_timeout_ms, bool)
            or default_timeout_ms < 1
        ):
            raise ValueError("default_timeout_ms must be positive")
        if (
            not isinstance(max_timeout_ms, int)
            or isinstance(max_timeout_ms, bool)
            or max_timeout_ms < default_timeout_ms
        ):
            raise ValueError(
                "max_timeout_ms must be at least default_timeout_ms"
            )
        if retention_seconds < 0:
            raise ValueError("retention_seconds must be non-negative")

        self._harness_factory = harness_factory
        self._tool_group_manager = tool_group_manager or ToolGroupManager()
        self._skill_group_manager = (
            skill_group_manager or SkillGroupManager()
        )
        self._event_broker = event_broker or TaskEventBroker()
        self._max_running = max_running
        self._default_timeout_ms = default_timeout_ms
        self._max_timeout_ms = max_timeout_ms
        self._retention_seconds = float(retention_seconds)

        self._records_by_id: dict[int, TaskRuntimeRecord] = {}
        self._records_by_name: dict[str, TaskRuntimeRecord] = {}
        self._ready: deque[TaskRuntimeRecord] = deque()
        self._running_names: set[str] = set()
        self._cleanup_handles: dict[str, asyncio.TimerHandle] = {}
        self._next_task_id = 1
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None
        self._lock = RLock()

    @property
    def retention_seconds(self) -> float:
        """返回终态 runtime 在自动清理前的保留秒数。"""

        return self._retention_seconds

    @property
    def running_count(self) -> int:
        """返回当前占用执行 slot 的 Harness runner 数量。"""

        with self._lock:
            return len(self._running_names)

    @property
    def queued_count(self) -> int:
        """返回当前 FIFO ready queue 中等待执行的 Task 数量。"""

        with self._lock:
            return len(self._ready)

    @property
    def closing(self) -> bool:
        """返回 Manager 是否已经停止接收新 Task 并进入关闭流程。"""

        with self._lock:
            return self._closing

    def bind_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """绑定创建 runner、retention timer 和 shutdown task 的主事件循环。

        生产环境由 ``BackendRuntime`` 在 FastAPI lifespan 启动阶段调用。传入
        非 asyncio event loop 会抛出 ``TypeError``。
        """

        if not isinstance(loop, asyncio.AbstractEventLoop):
            raise TypeError("loop must be an asyncio event loop")
        with self._lock:
            self._main_loop = loop

    def start_task(self, spec: TaskStartSpec) -> TaskRuntimeRecord:
        """接收一份启动规格并返回新建的 retained runtime record。

        该方法原子保留 ``task_name``、生成内部 ``task_id``、构造通用 Task 并
        加入 FIFO 队列；若存在空闲 slot，还会立即装配 capability、Harness 和
        asyncio runner。排队时间不计入 Task timeout。

        同名 live/queued/retained Task 会抛出 ``DuplicateTaskNameError``；
        Manager 关闭后会抛出 ``TaskManagerClosedError``。返回的 record 仍由
        Manager 所有，调用方不应自行销毁或替换其中的 runtime 对象。
        """

        if not isinstance(spec, TaskStartSpec):
            raise TypeError("spec must be a TaskStartSpec")
        _validate_task_name(spec.task_name)
        _validate_start_spec(spec)
        timeout_ms = (
            self._default_timeout_ms
            if spec.timeout_ms is None
            else spec.timeout_ms
        )
        if (
            not isinstance(timeout_ms, int)
            or isinstance(timeout_ms, bool)
            or not 1 <= timeout_ms <= self._max_timeout_ms
        ):
            raise ValueError(
                f"timeout_ms must be between 1 and {self._max_timeout_ms}"
            )
        with self._lock:
            if self._closing:
                raise TaskManagerClosedError("TaskManager is closing")
            if spec.task_name in self._records_by_name:
                raise DuplicateTaskNameError(
                    f"Task name is already reserved: {spec.task_name!r}"
                )
            task_id = self._next_task_id
            self._next_task_id += 1
            task = Task(
                task_id=task_id,
                task_title=spec.task_name,
                parent_session_id=spec.parent_session_id,
                task_type=spec.task_type,
                task_prompt=spec.task_prompt,
                workdir=spec.workdir,
                constraints=spec.constraints,
                timeout_ms=timeout_ms,
            )
            record = TaskRuntimeRecord(
                task=task,
                start_spec=spec,
                created_at=_utc_now(),
            )
            record.unsubscribe_state = task.add_state_observer(
                self._on_state_change
            )
            self._records_by_id[task_id] = record
            self._records_by_name[spec.task_name] = record
            self._ready.append(record)
            self._refresh_queue_positions_locked()
            self._publish_locked("task.created", record)
            self._publish_queue_locked()
            self._drain_ready_locked()
            return record

    def get_record(self, task_name: str) -> TaskRuntimeRecord | None:
        """按公开 ``task_name`` 查找当前 queued/live/retained record。

        未找到时返回 ``None``。返回对象仍是 Manager 所有的内部 runtime record，
        适合 Application Service 查询，不应由调用方直接修改。
        """

        with self._lock:
            return self._records_by_name.get(task_name)

    def list_records(self) -> tuple[TaskRuntimeRecord, ...]:
        """返回当前全部 queued/live/retained record 的不可变容器快照。

        tuple 本身是快照，其中的 record 仍为活动对象，其字段可能随 Task 执行继续
        更新。需要稳定的 HTTP/SSE 表示时应使用 :meth:`record_view`。
        """

        with self._lock:
            return tuple(self._records_by_id.values())

    def record_view(self, record: TaskRuntimeRecord) -> dict[str, Any]:
        """把仍由本 Manager 注册的 record 编码为安全的对外字典。

        record 已清理、来自其他 Manager 或被同名新 runtime 取代时抛出
        ``LookupError``。响应不会暴露 Harness、runner、capability lease 或异常正文。
        """

        with self._lock:
            current = self._records_by_name.get(record.task.task_title)
            if current is not record:
                raise LookupError("Task runtime is no longer registered")
            return self._record_view_locked(record)

    def get_runtime(self, task_name: str) -> Harness | None:
        """返回指定 Task 已构造的 Harness runtime，未找到或尚在排队则返回 ``None``。

        该接口主要供内部 Plan Application Service 根据 ``task_name`` 路由到
        ``PlanAndSolveHarness``；它不负责验证具体 Harness 类型。
        """

        record = self.get_record(task_name)
        return record.harness if record is not None else None

    def list_runtimes(self) -> tuple[Harness, ...]:
        """返回当前已经构造完成的 Harness runtime 快照。

        纯排队且尚未获得执行 slot 的 Task 不包含在结果中；终态保留期内的 Harness
        仍会返回，直到 record 被 clear、timer 清理或 backend shutdown。
        """

        with self._lock:
            return tuple(
                record.harness
                for record in self._records_by_id.values()
                if record.harness is not None
            )

    def subscribe_events(self) -> TaskEventSubscription:
        """创建 Task 状态事件订阅，并把当前原子快照作为第一条事件排队。

        后续事件由 ``TaskEventBroker`` 按 revision 有序投递。调用方必须最终关闭
        subscription；慢订阅者队列溢出时 broker 会主动结束该订阅。
        """

        with self._lock:
            return self._event_broker.subscribe(
                tasks=[
                    self._record_view_locked(record)
                    for record in self._records_by_id.values()
                ],
                running_count=len(self._running_names),
                queued_count=len(self._ready),
            )

    def peek_task(self, task_name: str) -> PeekTaskResult:
        """读取 Task 的轻量诊断信息，不修改状态也不延长保留时间。

        Task 不存在时不会抛错，而是返回 ``found=False`` 的 ``PeekTaskResult``。
        """

        record = self.get_record(task_name)
        if record is None:
            return PeekTaskResult(
                task_name=task_name,
                found=False,
                task_id=None,
                task_type=None,
                state=None,
                checkpoint=None,
            )
        task = record.task
        return PeekTaskResult(
            task_name=task_name,
            found=True,
            task_id=task.task_id,
            task_type=task.task_type,
            state=task.state,
            checkpoint=task.checkpoint,
        )

    def interrupt_task(self, task_name: str) -> InterruptTaskResult:
        """幂等打断 queued 或 active Task，并返回精确的打断结果。

        queued Task 会立即移出 ready queue、进入 ``Interrupted``，且永远不会构造
        capability 或 Harness。active Task 会先原子提交 ``Interrupted``，再取消其
        runner。已终态和不存在的 Task 通过 outcome 表达，不抛业务异常。
        """

        with self._lock:
            record = self._records_by_name.get(task_name)
            if record is None:
                return InterruptTaskResult(
                    task_name=task_name,
                    outcome=InterruptTaskOutcome.NotFound,
                    task_id=None,
                    previous_state=None,
                    state=None,
                )
            task = record.task
            was_queued = record in self._ready
            if was_queued:
                self._ready.remove(record)
                record.queue_position = None
                self._refresh_queue_positions_locked()

        previous_state, current_state, _ = task.interrupt()
        if previous_state is TaskState.Interrupted:
            outcome = InterruptTaskOutcome.AlreadyInterrupted
        elif current_state is TaskState.Interrupted:
            outcome = InterruptTaskOutcome.Interrupted
        else:
            outcome = InterruptTaskOutcome.AlreadySettled

        with self._lock:
            if was_queued and current_state is TaskState.Interrupted:
                record.settled_at = _utc_now()
                self._publish_locked("task.settled", record)
                self._publish_queue_locked()
                self._schedule_cleanup_locked(record)
                self._drain_ready_locked()
        return InterruptTaskResult(
            task_name=task_name,
            outcome=outcome,
            task_id=task.task_id,
            previous_state=previous_state,
            state=current_state,
        )

    def clear_task(self, task_name: str) -> bool:
        """立即删除一个 runner 已退出的终态 retained runtime。

        成功时取消 retention timer、注销状态 observer 并关闭 capability lease。
        Task 不存在、仍在运行或尚未进入终态时返回 ``False``。
        """

        with self._lock:
            record = self._records_by_name.get(task_name)
            if (
                record is None
                or record.task.state not in TERMINAL_STATES
                or record.runner is not None
            ):
                return False
            self._remove_record_locked(record, publish=True)
            return True

    def register_runtime(self, harness: Harness) -> Harness:
        """注册一个已经由调用方构造的 Harness，供低层测试和兼容路径使用。

        生产 Task 启动必须使用 :meth:`start_task`，否则不会获得标准的 capability
        装配、排队、runner 和 timeout 语义。本方法仍执行 Task ID 与名称唯一性检查，
        并让 Plan 内部接口能够按名称定位该 Harness。
        """

        if not isinstance(harness, Harness):
            raise TypeError("runtime must be a Harness")
        task = harness.task
        _validate_task_name(task.task_title)
        with self._lock:
            if task.task_id in self._records_by_id:
                raise ValueError(f"Task ID is already registered: {task.task_id}")
            if task.task_title in self._records_by_name:
                raise DuplicateTaskNameError(
                    f"Task name is already registered: {task.task_title!r}"
                )
            spec = TaskStartSpec(
                task_name=task.task_title,
                parent_session_id=task.parent_session_id,
                task_type=task.task_type,
                task_prompt=task.task_prompt,
                workdir=task.workdir,
                constraints=task.constraints,
                timeout_ms=task.timeout_ms,
            )
            record = TaskRuntimeRecord(
                task=task,
                start_spec=spec,
                harness=harness,
                created_at=_utc_now(),
            )
            record.unsubscribe_state = task.add_state_observer(
                self._on_state_change
            )
            self._records_by_id[task.task_id] = record
            self._records_by_name[task.task_title] = record
            self._next_task_id = max(self._next_task_id, task.task_id + 1)
        return harness

    @contextmanager
    def runtime(self, harness: Harness) -> Iterator[Harness]:
        """在 context 生命周期内临时注册一个外部构造的 Harness。

        context 退出时，终态 runtime 进入正常 retention；非终态 runtime 立即撤销，
        避免留下没有 runner、永远无法自动结束的活动对象。该接口仅用于低层测试和
        兼容代码，生产执行链应使用 :meth:`start_task`。
        """

        self.register_runtime(harness)
        try:
            yield harness
        except BaseException:
            with self._lock:
                record = self._records_by_name.get(
                    harness.task.task_title
                )
                if record is not None:
                    if record.task.state in TERMINAL_STATES:
                        self._schedule_cleanup_locked(record)
                    else:
                        self._remove_record_locked(record, publish=False)
            raise
        else:
            with self._lock:
                record = self._records_by_name.get(
                    harness.task.task_title
                )
                if record is not None:
                    if record.task.state in TERMINAL_STATES:
                        self._schedule_cleanup_locked(record)
                    else:
                        self._remove_record_locked(record, publish=False)

    async def aclose(self, *, grace_seconds: float = 5.0) -> None:
        """异步停止准入并完整关闭所有 Task runtime。

        关闭时发布 ``runtime.closing``、打断 queued/live Task、在
        ``grace_seconds`` 内等待 runner，随后取消仍未退出的 runner，释放 retained
        capability 并关闭事件流。多个调用方共享同一个 shielded close task，因此某个
        等待方被取消不会中断实际清理；重复调用是幂等的。
        """

        if grace_seconds < 0:
            raise ValueError("grace_seconds must be non-negative")
        with self._lock:
            close_task = self._close_task
            if close_task is None:
                loop = asyncio.get_running_loop()
                close_task = loop.create_task(
                    self._aclose_impl(grace_seconds),
                    name="aurabot-task-manager-close",
                )
                self._close_task = close_task
        await asyncio.shield(close_task)

    async def _aclose_impl(self, grace_seconds: float) -> None:
        with self._lock:
            self._closing = True
            try:
                self._event_broker.publish(
                    "runtime.closing",
                    {},
                    running_count=len(self._running_names),
                    queued_count=len(self._ready),
                )
            except RuntimeError:
                pass
            names = tuple(self._records_by_name)
        cleanup_error: BaseException | None = None
        try:
            for name in names:
                self.interrupt_task(name)
            with self._lock:
                runners = tuple(
                    record.runner
                    for record in self._records_by_id.values()
                    if record.runner is not None
                )
            if runners:
                _, pending = await asyncio.wait(
                    runners,
                    timeout=grace_seconds,
                )
                for runner in pending:
                    runner.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
        except BaseException as exc:
            cleanup_error = exc
        finally:
            with self._lock:
                for record in tuple(self._records_by_id.values()):
                    try:
                        self._remove_record_locked(record, publish=False)
                    except BaseException as exc:
                        if cleanup_error is None:
                            cleanup_error = exc
                self._ready.clear()
                self._running_names.clear()
                self._main_loop = None
            self._event_broker.close()
        if cleanup_error is not None:
            raise cleanup_error

    def shutdown(self) -> None:
        """立即执行同步清理，仅供没有活动事件循环的低层测试使用。

        该方法不会等待 runner 的 finally block，不应替代生产环境中的
        :meth:`aclose`。FastAPI/BackendRuntime shutdown 必须始终调用异步关闭接口。
        """

        with self._lock:
            self._closing = True
            for record in tuple(self._records_by_id.values()):
                if record.runner is not None:
                    record.runner.cancel()
                self._remove_record_locked(record, publish=False)
            self._ready.clear()
            self._running_names.clear()
            self._main_loop = None
        self._event_broker.close()

    def _drain_ready_locked(self) -> None:
        if self._closing or self._harness_factory is None:
            return
        while self._ready and len(self._running_names) < self._max_running:
            record = self._ready.popleft()
            record.queue_position = None
            if record.task.state is not TaskState.Initializing:
                continue
            self._activate_locked(record)
        self._refresh_queue_positions_locked()

    def _activate_locked(self, record: TaskRuntimeRecord) -> None:
        session = CapabilitySession(
            self._tool_group_manager,
            self._skill_group_manager,
        )
        record.capabilities = session
        try:
            tool_groups = session.acquire_tool_groups(
                record.start_spec.tool_group_names
            )
            skill_groups = session.acquire_skill_groups(
                record.start_spec.skill_group_names
            )
            record.task.update_tool_groups(tool_groups)
            record.task.skill_groups = list(skill_groups)
            harness = self._harness_factory.create(
                record.task,
                capability_session=session,
            )
        except Exception:
            try:
                session.close()
            except BaseException:
                # Initialization already failed. Cleanup is best-effort here:
                # the Task must still settle deterministically instead of
                # escaping from start_task() with a permanently reserved name.
                pass
            record.capabilities = None
            record.error = {
                "code": "task_initialization_failed",
                "message": "Task capabilities could not be initialized.",
            }
            record.task.compare_and_set_state(
                TaskState.Initializing,
                TaskState.Failed,
            )
            record.settled_at = _utc_now()
            self._publish_locked("task.settled", record)
            self._schedule_cleanup_locked(record)
            return

        record.harness = harness
        record.started_at = _utc_now()
        self._running_names.add(record.task.task_title)
        loop = self._main_loop
        if loop is None or loop.is_closed():
            loop = asyncio.get_running_loop()
            self._main_loop = loop
        record.runner = loop.create_task(
            self._execute_record(record),
            name=f"aurabot-task:{record.task.task_title}",
        )
        self._publish_locked("task.started", record)

    async def _execute_record(self, record: TaskRuntimeRecord) -> None:
        try:
            if record.harness is None:
                raise RuntimeError("Task runtime has no Harness")
            async with asyncio.timeout(record.task.timeout_ms / 1000):
                record.result = await record.harness.run()
        except asyncio.CancelledError:
            if record.task.state not in TERMINAL_STATES:
                record.task.interrupt()
        except Exception:
            record.task.compare_and_set_state(
                ACTIVE_STATES,
                TaskState.Failed,
            )
            record.error = {
                "code": "task_execution_failed",
                "message": "Task execution failed.",
            }
        finally:
            with self._lock:
                record.runner = None
                record.settled_at = record.settled_at or _utc_now()
                self._running_names.discard(record.task.task_title)
                self._publish_locked("task.settled", record)
                self._schedule_cleanup_locked(record)
                self._drain_ready_locked()
                self._publish_queue_locked()

    def _on_state_change(
        self,
        task: Task,
        previous_state: TaskState,
        current_state: TaskState,
    ) -> None:
        with self._lock:
            record = self._records_by_name.get(task.task_title)
            if record is None or record.task is not task:
                return
            self._publish_locked(
                "task.state_changed",
                record,
                extra={
                    "previous_state": previous_state.value.lower(),
                    "state": current_state.value.lower(),
                },
            )

    def _schedule_cleanup_locked(self, record: TaskRuntimeRecord) -> None:
        if record.task.state not in TERMINAL_STATES:
            return
        loop = self._main_loop
        if loop is None or loop.is_closed():
            return
        name = record.task.task_title
        previous = self._cleanup_handles.pop(name, None)
        if previous is not None:
            previous.cancel()
        self._cleanup_handles[name] = loop.call_later(
            self._retention_seconds,
            self._expire_record,
            name,
            record,
        )

    def _expire_record(
        self,
        task_name: str,
        expected: TaskRuntimeRecord,
    ) -> None:
        with self._lock:
            record = self._records_by_name.get(task_name)
            if (
                record is expected
                and record.task.state in TERMINAL_STATES
                and record.runner is None
            ):
                self._remove_record_locked(record, publish=True)

    def _remove_record_locked(
        self,
        record: TaskRuntimeRecord,
        *,
        publish: bool,
    ) -> None:
        name = record.task.task_title
        if self._records_by_name.get(name) is not record:
            return
        if record in self._ready:
            self._ready.remove(record)
        self._records_by_name.pop(name, None)
        if self._records_by_id.get(record.task.task_id) is record:
            self._records_by_id.pop(record.task.task_id, None)
        self._running_names.discard(name)
        handle = self._cleanup_handles.pop(name, None)
        if handle is not None:
            handle.cancel()
        if record.unsubscribe_state is not None:
            record.unsubscribe_state()
            record.unsubscribe_state = None
        if record.capabilities is not None:
            record.capabilities.close()
            record.capabilities = None
        self._refresh_queue_positions_locked()
        if publish and not self._closing:
            self._publish_locked(
                "task.removed",
                None,
                extra={"task_name": name, "task_id": record.task.task_id},
            )
            self._publish_queue_locked()

    def _refresh_queue_positions_locked(self) -> None:
        for position, record in enumerate(self._ready, start=1):
            record.queue_position = position

    def _publish_queue_locked(self) -> None:
        if self._closing:
            return
        self._publish_locked(
            "task.queue_changed",
            None,
            extra={
                "queue": [
                    {
                        "task_name": record.task.task_title,
                        "queue_position": record.queue_position,
                    }
                    for record in self._ready
                ]
            },
        )

    def _publish_locked(
        self,
        event: str,
        record: TaskRuntimeRecord | None,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if self._closing:
            return
        payload = dict(extra or {})
        if record is not None:
            payload["task"] = self._record_view_locked(record)
        try:
            self._event_broker.publish(
                event,
                payload,
                running_count=len(self._running_names),
                queued_count=len(self._ready),
            )
        except RuntimeError:
            pass

    @staticmethod
    def _record_view_locked(
        record: TaskRuntimeRecord,
    ) -> dict[str, Any]:
        return {
            "task_id": record.task.task_id,
            "task_name": record.task.task_title,
            "task_type": _task_type_wire(record.task.task_type),
            "state": record.task.state.value.lower(),
            "queue_position": record.queue_position,
            "created_at": _iso(record.created_at),
            "started_at": _iso(record.started_at),
            "settled_at": _iso(record.settled_at),
            "result": record.result,
            "error": record.error,
        }


def _validate_task_name(task_name: str) -> None:
    if (
        not isinstance(task_name, str)
        or not 1 <= len(task_name) <= 128
        or task_name != task_name.strip()
        or "/" in task_name
        or _TASK_NAME_CONTROL_PATTERN.search(task_name) is not None
    ):
        raise ValueError(
            "task_name must be 1-128 trimmed characters without '/', NUL or controls"
        )


def _validate_start_spec(spec: TaskStartSpec) -> None:
    if not isinstance(spec.task_type, TaskType):
        raise TypeError("task_type must be a TaskType")
    for field_name in ("parent_session_id", "task_prompt"):
        value = getattr(spec, field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")
    for names in (spec.tool_group_names, spec.skill_group_names):
        if not isinstance(names, tuple):
            raise TypeError("capability group names must be tuples")
        if len(set(names)) != len(names):
            raise ValueError("capability group names must be unique")
        if any(
            not isinstance(name, str)
            or not name
            or name != name.strip()
            for name in names
        ):
            raise ValueError(
                "capability group names must be non-empty trimmed strings"
            )


def _task_type_wire(task_type: TaskType) -> str:
    return {
        TaskType.ReAct: "react",
        TaskType.PlanAndSolve: "plan_and_solve",
        TaskType.Goal: "goal",
    }[task_type]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return (
        value.isoformat().replace("+00:00", "Z")
        if value is not None
        else None
    )


__all__ = [
    "ACTIVE_STATES",
    "DuplicateTaskNameError",
    "TERMINAL_STATES",
    "TaskManager",
    "TaskManagerClosedError",
]
