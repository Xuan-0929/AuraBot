"""
定义单个 Skill 的领域模型及可供提示词使用的说明表示。

持久化 adapter 或配置层构造 Skill，SkillGroup 汇总其名称、描述与正文供 Harness 使用；
本模块不加载文件、不运行指令，也不管理共享实例生命周期。
"""

from html import escape
from pathlib import Path

from .utils.formatter import parse_frontmatter


"""
存储模型：
    内存：
        由SkillGroupManager存所有SkillGroup
        一个SkillGroup存所有Skill对象
        一个Skill对象只存储Skill名，Path。
    
    磁盘：
        Skill对象的具体介绍，比如Skill介绍，Skill具体内容，运行时懒加载，即用即丢。
"""


class Skill:
    def __init__(self, name: str, path: str | Path | None = None):
        """
        初始化函数

        Args:
            name (str): 技能名称
            path (str | Path, optional): 技能文件路径
        """
        self.name = name
        self.path = Path(path or name)

    def load_content(self) -> str:
        """
        加载技能内容

        Returns:
            str: 技能内容
        """
        _, content = self._load_parts()
        return content

    def load_intro(self) -> str:
        """
        加载技能简介

        Returns:
            str: 技能简介
        """
        metadata, _ = self._load_parts()
        name = metadata.get("name") or self.name
        description = metadata.get("description", "")

        descriptor_lines = [
            "<skill>",
            f"<name>{escape(name)}</name>",
        ]

        if description:
            descriptor_lines.append(f"<description>{escape(description)}</description>")

        descriptor_lines.append("</skill>")
        return "\n".join(descriptor_lines)

    def _load_parts(self) -> tuple[dict[str, str], str]:
        content = self.path.read_text(encoding="utf-8")

        if not content.startswith("---"):
            return {}, content

        lines = content.splitlines(keepends=True)

        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                metadata_text = "".join(lines[1:index])
                body = "".join(lines[index + 1 :]).lstrip("\r\n")
                return parse_frontmatter(metadata_text), body

        return {}, content
