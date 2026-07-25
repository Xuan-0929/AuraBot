import asyncio
from collections.abc import Collection, Sequence
from threading import RLock

from ..skills.SkillGroup import SkillGroup
from ..tools.ToolGroup import ToolGroup
from ..tools.core.ToolCallResolver import ToolCallResolver

from .TaskEntity import TaskState, TaskType

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

        self.messages = []
        self.checkpoint : str = None

        self.timeout_ms = timeout_ms

        if self.task_type == TaskType.PlanAndSolve:
            self.plan : str = None
        elif self.task_type == TaskType.Goal:
            self.goal : str = None


        self._state_lock = RLock()
        self._interrupt_requested = False
        self._runner: asyncio.Task[object] | None = None
        self.current_state : TaskState = TaskState.Initializing
    

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
        with self._state_lock:
            if self.current_state not in expected_states:
                return False
            if (
                self.current_state is TaskState.Initializing
                and self._interrupt_requested
            ):
                self.current_state = TaskState.Interrupted
                return False
            self.current_state = target
            return True

    def interrupt(self) -> tuple[TaskState, TaskState, bool]:
        """Atomically apply an external interrupt when the task is active."""

        runner: asyncio.Task[object] | None = None
        with self._state_lock:
            previous_state = self.current_state
            if previous_state is TaskState.Initializing:
                self._interrupt_requested = True
                return previous_state, self.current_state, True
            if previous_state not in {
                TaskState.Completed,
                TaskState.Failed,
                TaskState.Interrupted,
            }:
                self.current_state = TaskState.Interrupted
                runner = self._runner
            current_state = self.current_state

        if runner is not None and runner is not self._current_asyncio_task():
            runner.get_loop().call_soon_threadsafe(runner.cancel)
        return previous_state, current_state, False

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

    @staticmethod
    def _current_asyncio_task() -> asyncio.Task[object] | None:
        try:
            return asyncio.current_task()
        except RuntimeError:
            return None
