"""
负责提供 Task 事件层的稳定公共入口，并隔离经纪与传输投影的内部实现。

调用方经由本包订阅 TaskEventBroker 或将不可变任务快照投影为 wire payload；broker.py
维护事件流状态，projection.py 定义结构化只读视图。本包不拥有 runtime record、不执行
Task 调度，也不处理 SSE HTTP 响应。
"""

from .broker import TaskEventBroker, TaskEventSubscription
from .projection import task_snapshot_payload

__all__ = [
    "TaskEventBroker",
    "TaskEventSubscription",
    "task_snapshot_payload",
]
