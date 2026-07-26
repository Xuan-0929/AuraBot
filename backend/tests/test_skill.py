"""
验证 Skill 领域模型的输入约束、文件加载与提示词表示。

测试使用临时 SKILL.md fixture 读取真实内容，不覆盖 SkillGroup 聚合或 manager 租约。
"""

import tempfile
import unittest
from pathlib import Path

from backend.agent.skills.skill import Skill


class SkillTests(unittest.TestCase):
    def test_load_returns_content_below_yaml_header(self):
        with tempfile.TemporaryDirectory() as directory:
            skill_path = Path(directory) / "SKILL.md"
            skill_path.write_text(
                "---\n"
                "name: example\n"
                "description: demo skill\n"
                "---\n"
                "\n"
                "Use this skill body.\n"
                "Keep all body lines.\n",
                encoding="utf-8",
            )

            skill = Skill("example", path=skill_path)

            self.assertEqual(
                skill.load_content(),
                "Use this skill body.\nKeep all body lines.\n",
            )


if __name__ == "__main__":
    unittest.main()
