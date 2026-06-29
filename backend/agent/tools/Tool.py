

import json


class Tool:
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
    

    """
    Author: SCPZ24

    获取工具的字符串信息，OpenAI兼容格式
    """
    def get_info(self) -> str:
        return json.dumps(
            {
                "type": "function",
                "function": {
                    "name": self.name,
                    "description": self.description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Input query for the tool.",
                            }
                        },
                        "required": ["query"],
                    },
                },
            },
            ensure_ascii=False,
        )

    def execute(self, query: str) -> str:
        pass
