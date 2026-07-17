from html import escape

from .Tool import Tool


class ToolGroup:
    def __init__(self, name: str, tools: list[Tool], description: str):
        if not name or not tools or not description:
            raise ValueError("ToolGroup name, tools, and description cannot be empty.")

        self.name = name
        self.tools = tools
        self.description = description

    def list_tools_descriptions(self) -> str:
        """Generate a prompt-ready group description and tool summary."""
        descriptor_lines = [
            "<tool_group>",
            f"<name>{escape(self.name)}</name>",
            f"<description>{escape(self.description)}</description>",
            "<tools>",
        ]
        descriptor_lines.extend(tool.load_intro() for tool in self.tools)
        descriptor_lines.extend(["</tools>", "</tool_group>"])
        return "\n".join(descriptor_lines)
