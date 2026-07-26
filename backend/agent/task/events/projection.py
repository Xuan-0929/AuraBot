"""
负责将只读 Task 快照结构转换为稳定的事件 wire payload。

runtime publisher 先将内部可变 record 复制为不可变快照，再把该快照传给本模块；事件
broker 原样广播返回字典，gateway 随后编码为 SSE。本模块只依赖局部结构化协议，不导入
runtime 或持有 record，且不管理订阅、revision 或 Task 生命周期。
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ..domain import TaskState, TaskType


class TaskFailureView(Protocol):
    """描述事件投影读取的失败信息字段，不绑定 runtime 的具体失败类型。"""

    @property
    def code(self) -> str: ...

    @property
    def message(self) -> str: ...


class TaskSnapshotView(Protocol):
    """描述 wire 投影所需且只读的 Task 快照字段。"""

    @property
    def task_id(self) -> int: ...

    @property
    def task_name(self) -> str: ...

    @property
    def task_type(self) -> TaskType: ...

    @property
    def state(self) -> TaskState: ...

    @property
    def queue_position(self) -> int | None: ...

    @property
    def created_at(self) -> datetime | None: ...

    @property
    def started_at(self) -> datetime | None: ...

    @property
    def settled_at(self) -> datetime | None: ...

    @property
    def result(self) -> object: ...

    @property
    def error(self) -> TaskFailureView | None: ...


def task_snapshot_payload(snapshot: TaskSnapshotView) -> dict[str, object]:
    """Project a read-only Task snapshot into the SSE wire representation."""

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


def _iso(value: datetime | None) -> str | None:
    return (
        value.isoformat().replace("+00:00", "Z")
        if value is not None
        else None
    )


__all__ = ["TaskSnapshotView", "task_snapshot_payload"]
