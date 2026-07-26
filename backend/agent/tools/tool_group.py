"""
定义 ToolGroup 聚合与面向 Harness 的稳定说明表示。

ToolGroupManager 加载共享聚合，CapabilitySession 把其只读引用交给 Task，resolver 再
以此建立授权快照；本模块不执行工具、不访问持久化，也不维护租约计数。
"""

from .tool import Tool
from .utils.formatter import format_tool_group_intro


class ToolGroup:
    def __init__(self, name: str, tools: list[Tool], description: str):
        if not name or tools is None or not description:
            raise ValueError(
                "ToolGroup name and description cannot be empty; tools cannot be None."
            )

        self.name = name
        self.tools = tools
        self.description = description

    def list_tools_descriptions(self) -> str:
        """Generate a prompt-ready group description and tool summary."""
        return format_tool_group_intro(
            name=self.name,
            description=self.description,
            tool_intros=[tool.load_intro() for tool in self.tools],
        )
