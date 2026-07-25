from threading import RLock

from .Task import Task
from .TaskEntity import (
    InterruptTaskOutcome,
    InterruptTaskResult,
    PeekTaskResult,
    TaskState,
)


class TaskManager:
    _instance: "TaskManager | None" = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._tasks = {}
        self._tasks_lock = RLock()
        self._initialized = True

    def create_task(self, task_id: str, task_type: str, task_data: dict):
        with self._tasks_lock:
            self._tasks[task_id] = {
                "id": task_id,
                "type": task_type,
                "data": task_data,
            }

    def list_tasks(self):
        with self._tasks_lock:
            return list(self._tasks.values())

    def interrupt_task(self, task_name: str) -> InterruptTaskResult:
        task = self._find_task_by_name(task_name)
        if task is None:
            return InterruptTaskResult(
                task_name=task_name,
                outcome=InterruptTaskOutcome.NotFound,
                task_id=None,
                previous_state=None,
                state=None,
            )

        previous_state, current_state, deferred = task.interrupt()
        if deferred:
            outcome = InterruptTaskOutcome.Deferred
        elif previous_state is TaskState.Interrupted:
            outcome = InterruptTaskOutcome.AlreadyInterrupted
        elif current_state is TaskState.Interrupted:
            outcome = InterruptTaskOutcome.Interrupted
        else:
            outcome = InterruptTaskOutcome.AlreadySettled

        return InterruptTaskResult(
            task_name=task_name,
            outcome=outcome,
            task_id=task.task_id,
            previous_state=previous_state,
            state=current_state,
        )

    def peek_task(self, task_name: str) -> PeekTaskResult:
        task = self._find_task_by_name(task_name)
        if task is None:
            return PeekTaskResult(
                task_name=task_name,
                found=False,
                task_id=None,
                task_type=None,
                state=None,
                checkpoint=None,
            )

        return PeekTaskResult(
            task_name=task_name,
            found=True,
            task_id=task.task_id,
            task_type=task.task_type,
            state=task.state,
            checkpoint=task.checkpoint,
        )

    def _find_task_by_name(self, task_name: str) -> Task | None:
        with self._tasks_lock:
            for task in self._tasks.values():
                if isinstance(task, Task) and task.task_title == task_name:
                    return task
        return None
