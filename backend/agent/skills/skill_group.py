"""
定义一组 Skill 的领域聚合与稳定提示词摘要。

SkillGroupManager 从持久化层加载聚合，Task capability snapshot 持有只读引用并由
prompt formatter 消费；本模块不执行技能、不访问数据库，也不处理租约计数。
"""

from html import escape

from .skill import Skill


class SkillGroup:
    def __init__(self, name: str, skills: list[Skill], description: str | None = None):
        self.name = name
        self.skills = skills
        self.description = description

    def add_skill(self, skill: Skill):
        """
        添加一个技能到技能组中

        Args:
            skill (Skill): 要添加的技能对象
        """
        self.skills.append(skill)

    def remove_skill(self, skill_name: str):
        """
        从技能组中移除一个技能

        Args:
            skill_name (str): 要移除的技能名称
        """
        self.skills = [skill for skill in self.skills if skill.name != skill_name]

    def list_skills(self) -> list[Skill]:
        """
        列出技能组中的所有技能

        Returns:
            list[Skill]: 技能组中的所有技能对象列表
        """
        return self.skills

    def list_skills_descriptions(self) -> str:
        """
        生成可直接放入系统提示词的技能组简介。

        Returns:
            str: 包含技能组信息和当前所有技能简介的 XML 文本。
        """

        descriptor_lines = [
            "<skill_group>",
            f"<name>{escape(self.name)}</name>",
        ]

        if self.description:
            descriptor_lines.append(
                f"<description>{escape(self.description)}</description>"
            )

        descriptor_lines.append("<skills>")
        descriptor_lines.extend(skill.load_intro() for skill in self.skills)
        descriptor_lines.extend(["</skills>", "</skill_group>"])

        return "\n".join(descriptor_lines)
