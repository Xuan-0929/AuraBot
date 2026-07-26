"""White-box helpers for tests that exercise an already-built Harness."""

from datetime import UTC, datetime

from backend.agent.task.manager import TaskManager
from backend.agent.task.harness.harness import Harness
from backend.agent.task.runtime import TaskStartSpec, _TaskRuntimeRecord


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
