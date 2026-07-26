"""
提供 runtime 测试在已构造 Harness 上安装和结束 record 的白盒辅助函数。

这些 helper 只用于建立确定性 fixture，不属于生产 API，也不替代 TaskManager 行为测试。
"""

from datetime import UTC, datetime

from backend.agent.task import TaskManager
from backend.agent.task.harness import Harness
from backend.agent.task.runtime.records import (
    TaskStartSpec,
    _TaskRuntimeRecord,
)


def retain_test_harness(
    manager: TaskManager,
    harness: Harness,
) -> _TaskRuntimeRecord:
    """Insert a prebuilt Harness without restoring the removed public API."""

    task = harness.task
    record = _TaskRuntimeRecord(
        task=task,
        start_spec=TaskStartSpec(
            task_name=task.task_title,
            parent_session_id=task.parent_session_id,
            task_type=task.task_type,
            task_prompt=task.task_prompt,
            workdir=task.workdir,
            constraints=task.constraints,
            timeout_ms=task.timeout_ms,
        ),
        harness=harness,
        created_at=datetime.now(UTC),
    )
    manager._registry.add(record)
    return record


__all__ = ["retain_test_harness"]
