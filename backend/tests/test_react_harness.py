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


class FakeLLMClient:
    pass


class ReActPromptTemplateTests(unittest.TestCase):
    def test_template_declares_every_dynamic_task_placeholder(self):
        placeholders = {
            field_name
            for _, field_name, _, _ in Formatter().parse(system_prompt_ReAct)
            if field_name is not None
        }

        self.assertEqual(
            placeholders,
            {
                "workdir",
                "constraints",
                "skill_groups",
                "tool_groups",
            },
        )


class ReActHarnessPromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_system_prompt_contains_task_scope_and_all_capability_groups(self):
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
                        [Tool("read_file", "Read one file")],
                        "File tools",
                    ),
                    ToolGroup(
                        "search",
                        [Tool("web_search", "Search the web")],
                        "Search tools",
                    ),
                ],
            )
            harness = ReActHarness(task=task, llm_client=FakeLLMClient())

            prompt = await harness._create_system_prompt()
            user_prompt = await harness._create_user_prompt()

        self.assertIn("no more than 20 ReAct rounds", prompt)
        self.assertIn("Working directory:\nC:/workspace & data", prompt)
        self.assertIn("Constraints:\nDo not modify source files.", prompt)
        self.assertIn("<name>research</name>", prompt)
        self.assertIn("<name>writing</name>", prompt)
        self.assertIn("<name>summarize</name>", prompt)
        self.assertIn("<name>filesystem</name>", prompt)
        self.assertIn("<name>search</name>", prompt)
        self.assertIn("<name>read_file</name>", prompt)
        self.assertIn("<name>web_search</name>", prompt)
        self.assertNotIn("Research &lt;report&gt;", prompt)
        self.assertNotIn("Prepare an evidence-based report.", prompt)
        self.assertNotIn("<task_context>", prompt)
        self.assertNotIn("<workdir>", prompt)
        self.assertNotIn("{workdir}", prompt)
        self.assertNotIn("{skill_groups}", prompt)
        self.assertEqual(user_prompt, "Prepare an evidence-based report.")

    async def test_system_prompt_marks_optional_scope_and_groups_as_missing(self):
        task = Task(
            task_id=2,
            task_title="Answer",
            parent_session_id="main-session",
            task_type=TaskType.ReAct,
            task_prompt="Answer the question.",
        )
        harness = ReActHarness(task=task, llm_client=FakeLLMClient())

        prompt = await harness._create_system_prompt()

        self.assertIn("Working directory:\nNot specified.", prompt)
        self.assertIn("Constraints:\nNot specified.", prompt)
        self.assertEqual(prompt.count("None provided."), 2)


if __name__ == "__main__":
    unittest.main()
