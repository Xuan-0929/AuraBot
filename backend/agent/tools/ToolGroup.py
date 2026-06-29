import json

from .Tool import Tool




class ToolGroup:
    def __init__(self, name: str, tools: list[Tool], description: str):
        if not name or not tools or not description:
            raise ValueError("ToolGroup name, tools, and description cannot be empty.")

        self.name = name
        self.tools = tools
        self.description = description

    def get_info(self) -> str:
        """
        Author: SCPZ24

        获取工具组的字符串信息，OpenAI兼容格式
        """
        return json.dumps(
            [json.loads(tool.get_info()) for tool in self.tools],
            ensure_ascii=False,
        )
