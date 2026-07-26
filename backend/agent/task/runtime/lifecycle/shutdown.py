"""
负责 Task runtime 在异步关闭与同步兜底路径中的集中清理。

BackendRuntime 关闭时先广播 closing、幂等中断全部 Task、等待 runner，再移除 record；
异常兜底路径直接取消并释放资源。本模块不创建 Task，也不决定单个 Harness 的退出策略。
"""

from collections.abc import Callable

from ..records import InterruptTaskResult, _TaskRuntimeRecord
from ..registry import TaskRuntimeRegistry
from ..scheduler import TaskScheduler
from ..event_publisher import TaskEventPublisher


async def close_task_runtime(
    *,
    grace_seconds: float,
    registry: TaskRuntimeRegistry,
    scheduler: TaskScheduler,
    events: TaskEventPublisher,
    interrupt: Callable[[str], InterruptTaskResult],
    remove: Callable[[_TaskRuntimeRecord], None],
) -> None:
    events.publish_runtime_closing()
    cleanup_error: BaseException | None = None
    try:
        for name in registry.names():
            interrupt(name)
        await scheduler.wait_for_runners(grace_seconds)
    except BaseException as exc:
        cleanup_error = exc
    finally:
        for record in registry.list():
            try:
                remove(record)
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        scheduler.clear()
        events.close()
    if cleanup_error is not None:
        raise cleanup_error


def shutdown_task_runtime(
    *,
    registry: TaskRuntimeRegistry,
    scheduler: TaskScheduler,
    events: TaskEventPublisher,
    remove: Callable[[_TaskRuntimeRecord], None],
) -> None:
    for record in registry.list():
        scheduler.cancel(record)
        remove(record)
    scheduler.clear()
    events.close()


__all__ = ["close_task_runtime", "shutdown_task_runtime"]
