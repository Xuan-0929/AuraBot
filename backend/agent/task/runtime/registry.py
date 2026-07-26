"""
负责按 Task ID 与公开名称保存排队、运行和保留期内的 runtime record。

TaskManager 与 lifecycle controller 通过本 registry 分配 ID、定位和移除同一 record；
本模块不调度 runner、不改变 Task 状态，也不向应用层暴露内部可变记录。
"""

from __future__ import annotations

from .records import _TaskRuntimeRecord


class TaskRuntimeRegistry:
    def __init__(self):
        self._by_id: dict[int, _TaskRuntimeRecord] = {}
        self._by_name: dict[str, _TaskRuntimeRecord] = {}
        self._next_id = 1

    def allocate_id(self) -> int:
        task_id = self._next_id
        self._next_id += 1
        return task_id

    def add(self, record: _TaskRuntimeRecord) -> None:
        task = record.task
        if task.task_id in self._by_id:
            raise ValueError(f"Task ID is already registered: {task.task_id}")
        if task.task_title in self._by_name:
            raise ValueError(
                f"Task name is already registered: {task.task_title!r}"
            )
        self._by_id[task.task_id] = record
        self._by_name[task.task_title] = record
        self._next_id = max(self._next_id, task.task_id + 1)

    def get(self, task_name: str) -> _TaskRuntimeRecord | None:
        return self._by_name.get(task_name)

    def list(self) -> tuple[_TaskRuntimeRecord, ...]:
        return tuple(self._by_id.values())

    def owns(self, record: _TaskRuntimeRecord) -> bool:
        task = record.task
        return (
            self._by_id.get(task.task_id) is record
            and self._by_name.get(task.task_title) is record
        )

    def remove(self, record: _TaskRuntimeRecord) -> bool:
        if not self.owns(record):
            return False
        self._by_id.pop(record.task.task_id, None)
        self._by_name.pop(record.task.task_title, None)
        return True

    def names(self) -> tuple[str, ...]:
        return tuple(self._by_name)


__all__ = ["TaskRuntimeRegistry"]
