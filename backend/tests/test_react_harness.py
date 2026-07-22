import tempfile
import unittest
from pathlib import Path
from string import Formatter

from backend.agent.skills.Skill import Skill
from backend.agent.skills.SkillGroup import SkillGroup
from backend.agent.task.Task import Task, TaskType
from backend.agent.task.harness.ReActHarness import ReActHarness
from backend.agent.task.harness.prompts import system_prompt_ReAct
from backend.agent.tools.Tool import Tool
from backend.agent.tools.ToolGroup import ToolGroup


EMPTY_INPUT_SCHEMA = {"type": "object", "properties": {}}


class FakeLLMClient:
    pass


class ReActPromptTemplateTests(unittest.TestCase):
    def test_template_declares_only_common_task_placeholders(self):
        placeholders = {
            field_name
            for _, field_name, _, _ in Formatter().parse(system_prompt_ReAct)
            if field_name is not None
        }

        self.assertEqual(
            placeholders,
            {"workdir", "constraints", "skill_groups", "tool_groups"},
        )


class ReActHarnessTests(unittest.TestCase):
    def test_system_prompt_contains_react_scope_and_group_summaries(self):
        with tempfile.TemporaryDirectory() as directory:
            skill_path = Path(directory) / "SKILL.md"
            skill_path.write_text(
                "---\n"
                "name: summarize\n"
                "description: Summarize source material.\n"
                "---\n"
                "Skill instructions.\n",
                encoding="utf-8",
            )
            task = Task(
                task_id=1,
                task_title="Research <report>",
                parent_session_id="main-session",
                task_type=TaskType.ReAct,
                task_prompt="Prepare an evidence-based report.",
                workdir="C:/workspace & data",
                constraints="Do not modify source files.",
                skill_groups=[
                    SkillGroup(
                        "research",
                        [Skill("summarize", path=skill_path)],
                        "Research skills",
                    ),
                    SkillGroup("writing", [], "Writing skills"),
                ],
                tool_groups=[
                    ToolGroup(
                        "filesystem",
                        [
                            Tool(
                                "read_file",
                                "Read one file",
                                "http://localhost:6410/mcp",
                                input_schema=EMPTY_INPUT_SCHEMA,
                            )
                        ],
                        "File tools",
                    )
                ],
            )
            harness = ReActHarness(task=task, llm_client=FakeLLMClient())

            prompt = harness.build_system_prompt()

        self.assertIn("no more than 20 ReAct rounds", prompt)
        self.assertIn("Working directory:\nC:/workspace & data", prompt)
        self.assertIn("Constraints:\nDo not modify source files.", prompt)
        self.assertIn("<name>research</name>", prompt)
        self.assertIn("<name>writing</name>", prompt)
        self.assertIn("<name>summarize</name>", prompt)
        self.assertIn(
            "- filesystem: File tools (tools: read_file)",
            prompt,
        )
        self.assertNotIn("<parameters>", prompt)
        self.assertNotIn("Create a finite phase plan", prompt)
        self.assertEqual(harness.build_user_prompt(), task.task_prompt)

    def test_system_prompt_marks_optional_scope_and_groups_as_missing(self):
        task = Task(
            task_id=2,
            task_title="Answer",
            parent_session_id="main-session",
            task_type=TaskType.ReAct,
            task_prompt="Answer the question.",
        )
        harness = ReActHarness(task=task, llm_client=FakeLLMClient())

        prompt = harness.build_system_prompt()

        self.assertIn("Working directory:\nNot specified.", prompt)
        self.assertIn("Constraints:\nNot specified.", prompt)
        self.assertEqual(prompt.count("None provided."), 2)

    def test_inherits_all_common_tool_and_prompt_methods(self):
        common_methods = {
            "build_openai_tools",
            "parse_completion",
            "build_tool_result_messages",
            "build_user_prompt",
        }

        self.assertTrue(common_methods.isdisjoint(ReActHarness.__dict__))
        self.assertIn("build_system_prompt", ReActHarness.__dict__)


if __name__ == "__main__":
    unittest.main()
