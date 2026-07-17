from .Harness import *
from .prompts import system_prompt_ReAct
from .utils.formatter import _format_optional


""" Involving States

Initializing #任务正在初始化中
Pending #任务已经初始化，但是还没有执行
Running #正在调用工具
Concluding #整理这一阶段的产物中
Waiting = #等待主Agent的下一步指令或者用户输入
Completed = #完成了
Failed #失败了
Interrupted #被打断

"""


class ReActHarness(Harness):
    """Task-local ReAct loop.

    Prompt assembly, tool-call parsing, and tool execution will be implemented
    here. Construction already follows the common Harness contract.
    """

    async def run(self) -> object:
        
        raise NotImplementedError("ReAct agent loop is not implemented yet")

    async def _create_system_prompt(self) -> str:
        """Build the complete system prompt for this task-local agent."""

        task = self.task
        skill_groups = "\n".join(
            group.list_skills_descriptions() for group in task.skill_groups
        )
        tool_groups = "\n".join(
            group.list_tools_descriptions() for group in task.tool_groups
        )

        return system_prompt_ReAct.format(
            workdir=_format_optional(task.workdir),
            constraints=_format_optional(task.constraints),
            skill_groups=skill_groups or "None provided.",
            tool_groups=tool_groups or "None provided.",
        ).strip()