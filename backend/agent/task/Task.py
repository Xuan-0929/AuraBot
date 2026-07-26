import asyncio
from collections.abc import Callable, Collection, Sequence
from contextlib import contextmanager
from threading import RLock
from typing import Iterator

from ..skills.SkillGroup import SkillGroup
from ..tools.ToolGroup import ToolGroup
from ..tools.core.ToolCallResolver import ToolCallResolver

from .TaskEntity import TaskState, TaskType

TaskStateObserver = Callable[["Task", TaskState, TaskState], None]

"""
定义任务本体
"""


class Task:
    def __init__(self, task_id: int, task_title: str, parent_session_id: str,
                 task_type: TaskType, task_prompt: str,
                 workdir: str | None = None, constraints: str | None = None,
                 skill_groups: list[SkillGroup] | None = None,
                 tool_groups: list[ToolGroup] | None = None,
                 timeout_ms: int = 60000
                 ):
        self.task_id = task_id
        self.task_title = task_title
        self.parent_session_id = parent_session_id
        self.task_type = task_type
        self.task_prompt = task_prompt
        self.workdir = workdir
        self.constraints = constraints

        self.skill_groups = skill_groups if skill_groups is not None else []
        self._tool_groups: tuple[ToolGroup, ...]
        self._tool_call_resolver: ToolCallResolver
        self.update_tool_groups(tool_groups or ())

        self._state_lock = RLock()
        self._state_observers: set[TaskStateObserver] = set()
        self._runner: asyncio.Task[object] | None = None
        self.current_state : TaskState = TaskState.Initializing

        self.messages = []
        self.checkpoint : str = None
        self.timeout_ms = timeout_ms

    @property
    def state(self) -> TaskState:
        with self._state_lock:
            return self.current_state

    @property
    def tool_groups(self) -> tuple[ToolGroup, ...]:
        """The immutable top-level snapshot of groups authorized for this task."""

        return self._tool_groups

    @property
    def tool_call_resolver(self) -> ToolCallResolver:
        """The resolver bound to this task's current tool authorization."""

        return self._tool_call_resolver

    def update_tool_groups(self, tool_groups: Sequence[ToolGroup]) -> None:
        """Atomically replace the task's authorized groups and resolver."""

        snapshot = tuple(tool_groups)
        resolver = ToolCallResolver.from_tool_groups(snapshot)
        self._tool_groups = snapshot
        self._tool_call_resolver = resolver

    def compare_and_set_state(
        self,
        expected: TaskState | Collection[TaskState],
        target: TaskState,
    ) -> bool:
        """Atomically set state when it still matches a Harness expectation."""

        expected_states = (
            {expected}
            if isinstance(expected, TaskState)
            else set(expected)
        )
        previous_state: TaskState
        with self._state_lock:
            if self.current_state not in expected_states:
                return False
            previous_state = self.current_state
            self.current_state = target
        self._notify_state_observers(previous_state, target)
        return True

    @contextmanager
    def active_state_guard(self) -> Iterator[TaskState]:
        """Hold the Task state lock while an owner mutates related runtime state."""

        with self._state_lock:
            if self.current_state in {
                TaskState.Completed,
                TaskState.Failed,
                TaskState.Interrupted,
            }:
                raise TaskInactiveError(
                    f"Task is not active: {self.current_state.value}"
                )
            yield self.current_state

    def interrupt(self) -> tuple[TaskState, TaskState, bool]:
        """Atomically apply an external interrupt when the task is active."""

        runner: asyncio.Task[object] | None = None
        changed = False
        with self._state_lock:
            previous_state = self.current_state
            if previous_state not in {
                TaskState.Completed,
                TaskState.Failed,
                TaskState.Interrupted,
            }:
                self.current_state = TaskState.Interrupted
                runner = self._runner
                changed = True
            current_state = self.current_state

        if changed:
            self._notify_state_observers(previous_state, current_state)
        if runner is not None and runner is not self._current_asyncio_task():
            runner.get_loop().call_soon_threadsafe(runner.cancel)
        return previous_state, current_state, False

    def add_state_observer(
        self,
        observer: TaskStateObserver,
    ) -> Callable[[], None]:
        """Subscribe to committed transitions and return an unsubscribe hook."""

        if not callable(observer):
            raise TypeError("observer must be callable")
        with self._state_lock:
            self._state_observers.add(observer)

        def unsubscribe() -> None:
            with self._state_lock:
                self._state_observers.discard(observer)

        return unsubscribe

    def bind_runner(self, runner: asyncio.Task[object]) -> bool:
        """Bind the one asyncio runner allowed to execute this Task."""

        with self._state_lock:
            if self._runner is not None:
                return False
            self._runner = runner
            return True

    def unbind_runner(self, runner: asyncio.Task[object]) -> None:
        with self._state_lock:
            if self._runner is runner:
                self._runner = None

    def _notify_state_observers(
        self,
        previous_state: TaskState,
        current_state: TaskState,
    ) -> None:
        if previous_state is current_state:
            return
        with self._state_lock:
            observers = tuple(self._state_observers)
        for observer in observers:
            try:
                observer(self, previous_state, current_state)
            except Exception:
                # State commits must not be rolled back by diagnostics.
                continue

    @staticmethod
    def _current_asyncio_task() -> asyncio.Task[object] | None:
        try:
            return asyncio.current_task()
        except RuntimeError:
            return None


class TaskInactiveError(RuntimeError):
    """Raised by the generic state guard after a Task reaches a terminal state."""


__all__ = ["Task", "TaskInactiveError", "TaskStateObserver"]
