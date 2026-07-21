from collections.abc import Sequence

from ..skills.SkillGroup import SkillGroup
from ..tools.Tool import Tool
from ..tools.ToolGroup import ToolGroup
from ..tools.core.ToolCallResolver import ToolCallResolver
from ..tools.core.ToolExceptions import DuplicateToolNameError


"""
定义任务本体
"""

from enum import Enum


class TaskType(Enum):
    ReAct = "ReAct"
    PlanAndSolve = "PlanAndSolve"
    Goal = "Goal" #这一版不做它

class TaskState(Enum):
    Initializing = "Initializing" #任务正在初始化中
    Pending = "Pending" #任务已经初始化，但是还没有执行
    Planning = "Planning" #仅P&S任务
    Running = "Running" #正在调用工具
    Concluding = "Concluding" #整理这一阶段的产物中
    Waiting = "Waiting" #等待主Agent的下一步指令或者用户输入
    Completed = "Completed" #完成了
    Failed = "Failed" #失败了
    Interrupted = "Interrupted" #被打断



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


        self.current_state : TaskState = TaskState.Initializing
    

    @property
    def state(self) -> TaskState:
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
        tools_by_name: dict[str, Tool] = {}
        group_by_tool_name: dict[str, str] = {}

        for group in snapshot:
            for tool in group.tools:
                existing = tools_by_name.get(tool.name)
                if existing is not None:
                    raise DuplicateToolNameError(
                        f"tool {tool.name!r} is exposed by both "
                        f"ToolGroup {group_by_tool_name[tool.name]!r} "
                        f"({existing.server_url!r}) and ToolGroup "
                        f"{group.name!r} ({tool.server_url!r})"
                    )
                tools_by_name[tool.name] = tool
                group_by_tool_name[tool.name] = group.name

        resolver = ToolCallResolver(tools_by_name)
        self._tool_groups = snapshot
        self._tool_call_resolver = resolver


    def set_completed(self):
        self.current_state = TaskState.Completed
        pass


    def set_failed(self):
        self.current_state = TaskState.Failed
        pass


    def set_interrupted(self):
        self.current_state = TaskState.Interrupted
        pass
