"""
负责定义 Task runtime context 实体、稳定生命周期枚举及原子状态转换。

Harness 与 runtime controller 通过 Task 保存授权能力、消息历史并执行 CAS 状态迁移；
本模块依赖 agent 的消息、SkillGroup 与 ToolGroup 抽象，但不负责调度、持久化或传输。
"""

from collections.abc import Callable, Collection, Sequence
from contextlib import contextmanager
from enum import Enum
from threading import RLock
from typing import Iterator

from ...llm_client import Message
from ...skills.skill_group import SkillGroup
from ...tools.tool_group import ToolGroup
from ...tools.core.resolver import ToolCallResolver


class TaskType(Enum):
    ReAct = "ReAct"
    PlanAndSolve = "PlanAndSolve"
    Goal = "Goal"


class TaskState(Enum):
    Initializing = "Initializing"
    Thinking = "Thinking"
    Pushing = "Pushing"
    Planning = "Planning"
    Concluding = "Concluding"
    Waiting = "Waiting"
    Completed = "Completed"
    Failed = "Failed"
    Interrupted = "Interrupted"


TaskStateObserver = Callable[["Task", TaskState, TaskState], None]


class Task:
    def __init__(
        self,
        task_id: int,
        task_title: str,
        parent_session_id: str,
        task_type: TaskType,
        task_prompt: str,
        workdir: str | None = None,
        constraints: str | None = None,
        skill_groups: list[SkillGroup] | None = None,
        tool_groups: list[ToolGroup] | None = None,
        timeout_ms: int = 60_000,
    ):
        self.task_id = task_id
        self.task_title = task_title
        self.parent_session_id = parent_session_id
        self.task_type = task_type
        self.task_prompt = task_prompt
        self.workdir = workdir
        self.constraints = constraints

        self._skill_groups: tuple[SkillGroup, ...] = ()
        self.update_skill_groups(skill_groups or ())
        self._tool_groups: tuple[ToolGroup, ...]
        self._tool_call_resolver: ToolCallResolver
        self.update_tool_groups(tool_groups or ())

        self._state_lock = RLock()
        self._state_observers: set[TaskStateObserver] = set()
        self._state = TaskState.Initializing

        self._messages: list[Message] = []
        self.timeout_ms = timeout_ms

    @property
    def state(self) -> TaskState:
        with self._state_lock:
            return self._state

    @property
    def messages(self) -> tuple[Message, ...]:
        """Return a stable copy of the retained model conversation."""

        return tuple(dict(message) for message in self._messages)

    @property
    def skill_groups(self) -> tuple[SkillGroup, ...]:
        return self._skill_groups

    @property
    def tool_groups(self) -> tuple[ToolGroup, ...]:
        """The immutable top-level snapshot of groups authorized for this task."""

        return self._tool_groups

    @property
    def tool_call_resolver(self) -> ToolCallResolver:
        """The resolver bound to this task's current tool authorization."""

        return self._tool_call_resolver

    def update_skill_groups(
        self,
        skill_groups: Sequence[SkillGroup],
    ) -> None:
        snapshot = tuple(skill_groups)
        if any(not isinstance(group, SkillGroup) for group in snapshot):
            raise TypeError("skill_groups must contain SkillGroup values")
        self._skill_groups = snapshot

    def update_tool_groups(self, tool_groups: Sequence[ToolGroup]) -> None:
        """Atomically replace the task's authorized groups and resolver."""

        snapshot = tuple(tool_groups)
        resolver = ToolCallResolver.from_tool_groups(snapshot)
        self._tool_groups = snapshot
        self._tool_call_resolver = resolver

    def initialize_messages(
        self,
        messages: Sequence[Message],
    ) -> None:
        if self._messages:
            raise RuntimeError("Task message history is already initialized")
        self.append_messages(messages)

    def append_message(self, message: Message) -> None:
        if not isinstance(message, dict):
            raise TypeError("message must be a mapping")
        self._messages.append(dict(message))

    def append_messages(self, messages: Sequence[Message]) -> None:
        for message in messages:
            self.append_message(message)

    def request_messages(
        self,
        extra: Sequence[Message] = (),
    ) -> list[Message]:
        return [
            *(dict(message) for message in self._messages),
            *(dict(message) for message in extra),
        ]

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
            if self._state not in expected_states:
                return False
            previous_state = self._state
            self._state = target
        self._notify_state_observers(previous_state, target)
        return True

    @contextmanager
    def active_state_guard(self) -> Iterator[TaskState]:
        """Hold the Task state lock while an owner mutates related runtime state."""

        with self._state_lock:
            if self._state in {
                TaskState.Completed,
                TaskState.Failed,
                TaskState.Interrupted,
            }:
                raise TaskInactiveError(
                    f"Task is not active: {self._state.value}"
                )
            yield self._state

    def interrupt(self) -> tuple[TaskState, TaskState]:
        """Atomically apply an external interrupt when the task is active."""

        changed = False
        with self._state_lock:
            previous_state = self._state
            if previous_state not in {
                TaskState.Completed,
                TaskState.Failed,
                TaskState.Interrupted,
            }:
                self._state = TaskState.Interrupted
                changed = True
            current_state = self._state

        if changed:
            self._notify_state_observers(previous_state, current_state)
        return previous_state, current_state

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


class TaskInactiveError(RuntimeError):
    """Raised by the generic state guard after a Task reaches a terminal state."""


__all__ = [
    "Task",
    "TaskInactiveError",
    "TaskState",
    "TaskStateObserver",
    "TaskType",
]
