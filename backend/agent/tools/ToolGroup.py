from .Tool import Tool
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
