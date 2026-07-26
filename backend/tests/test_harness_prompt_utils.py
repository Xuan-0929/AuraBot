import unittest

from backend.agent.task.model import Task, TaskType
from backend.agent.task.harness.utils.prompt import (
    build_common_prompt_values,
    format_optional,
    format_tool_group_summary,
)
from backend.agent.tools.tool import Tool
from backend.agent.tools.tool_group import ToolGroup


EMPTY_INPUT_SCHEMA = {"type": "object", "properties": {}}


def make_task(*, tool_groups=None) -> Task:
    return Task(
        task_id=1,
        task_title="Test",
        parent_session_id="main-session",
        task_type=TaskType.ReAct,
        task_prompt="Do work.",
        tool_groups=tool_groups,
    )


class HarnessPromptUtilsTests(unittest.TestCase):
    def test_formats_optional_values_consistently(self):
        self.assertEqual(format_optional(None), "Not specified.")
        self.assertEqual(format_optional(""), "Not specified.")
        self.assertEqual(format_optional(0), "0")

    def test_formats_tool_group_without_duplicating_schema(self):
        tool = Tool(
            "ping",
            "Return pong",
            "http://localhost:6410/mcp",
            input_schema=EMPTY_INPUT_SCHEMA,
        )
        group = ToolGroup("utilities", [tool], "Utility tools")

        summary = format_tool_group_summary(group)

        self.assertEqual(
            summary,
            "- utilities: Utility tools (tools: ping)",
        )
        self.assertNotIn("properties", summary)

    def test_builds_common_values_for_missing_groups(self):
        values = build_common_prompt_values(make_task())

        self.assertEqual(
            values,
            {
                "workdir": "Not specified.",
                "constraints": "Not specified.",
                "skill_groups": "None provided.",
                "tool_groups": "None provided.",
            },
        )

if __name__ == "__main__":
    unittest.main()
