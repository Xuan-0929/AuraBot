import tempfile
import unittest
from pathlib import Path

from agent.skills.Skill import Skill
from agent.skills.SkillGroup import SkillGroup


class SkillGroupTests(unittest.TestCase):
    def test_list_skills_descriptions_returns_prompt_ready_group_intro(self):
        with tempfile.TemporaryDirectory() as directory:
            described_path = Path(directory) / "described.md"
            described_path.write_text(
                "---\n"
                "name: search & summarize\n"
                "description: Find <relevant> sources.\n"
                "---\n"
                "Instructions.\n",
                encoding="utf-8",
            )
            unnamed_path = Path(directory) / "unnamed.md"
            unnamed_path.write_text("Instructions only.\n", encoding="utf-8")

            group = SkillGroup(
                "research & writing",
                [
                    Skill("search", path=described_path),
                    Skill("draft", path=unnamed_path),
                ],
                description="Research <and> write.",
            )

            self.assertEqual(
                group.list_skills_descriptions(),
                "<skill_group>\n"
                "<name>research &amp; writing</name>\n"
                "<description>Research &lt;and&gt; write.</description>\n"
                "<skills>\n"
                "<skill>\n"
                "<name>search &amp; summarize</name>\n"
                "<description>Find &lt;relevant&gt; sources.</description>\n"
                "</skill>\n"
                "<skill>\n"
                "<name>draft</name>\n"
                "</skill>\n"
                "</skills>\n"
                "</skill_group>",
            )

    def test_list_skills_descriptions_represents_empty_group(self):
        group = SkillGroup("empty", [], description=None)

        self.assertEqual(
            group.list_skills_descriptions(),
            "<skill_group>\n"
            "<name>empty</name>\n"
            "<skills>\n"
            "</skills>\n"
            "</skill_group>",
        )


if __name__ == "__main__":
    unittest.main()
