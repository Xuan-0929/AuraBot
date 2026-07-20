import unittest

from backend.agent.skills.SkillGroup import SkillGroup
from backend.agent.task.Task import Task, TaskType
from backend.agent.tools.Tool import Tool
from backend.agent.tools.ToolGroup import ToolGroup


class TaskTypeTests(unittest.TestCase):
    def test_task_type_defines_supported_agent_modes(self):
        self.assertEqual(TaskType.ReAct.value, "ReAct")
        self.assertEqual(TaskType.PlanAndSolve.value, "PlanAndSolve")


class TaskTests(unittest.TestCase):
    def test_constructor_accepts_multiple_skill_and_tool_groups(self):
        skill_groups = [
            SkillGroup("research", []),
            SkillGroup("writing", []),
        ]
        tool_groups = [
            ToolGroup(
                "web",
                [Tool("search", "Search the web", "localhost:6410")],
                "Web tools",
            ),
            ToolGroup(
                "files",
                [Tool("read", "Read a file", "localhost:6410")],
                "File tools",
            ),
        ]

        task = Task(
            task_id=1,
            task_title="Prepare report",
            parent_session_id="session-1",
            task_type=TaskType.ReAct,
            task_prompt="Research and write a report.",
            skill_groups=skill_groups,
            tool_groups=tool_groups,
        )

        self.assertEqual(task.skill_groups, skill_groups)
        self.assertEqual(task.tool_groups, tool_groups)

    def test_constructor_creates_independent_empty_group_lists(self):
        first = Task(1, "First", "session-1", TaskType.ReAct, "First task")
        second = Task(2, "Second", "session-1", TaskType.ReAct, "Second task")

        first.skill_groups.append(SkillGroup("research", []))
        first.tool_groups.append(
            ToolGroup(
                "web",
                [Tool("search", "Search the web", "localhost:6410")],
                "Web tools",
            )
        )

        self.assertEqual(second.skill_groups, [])
        self.assertEqual(second.tool_groups, [])


if __name__ == "__main__":
    unittest.main()
