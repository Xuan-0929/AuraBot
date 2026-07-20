from html import escape


class Tool:
    def __init__(self, name: str, description: str, server_url: str | None = None):
        self.name = name
        self.description = description

        if server_url is not None:
            self.server_url = server_url
        else:
            from backend.config import config

            self.server_url = config["local_mcp_server"]

    def load_intro(self) -> str:
        """Generate a complete, prompt-ready XML description of this tool."""
        return "\n".join(
            [
                "<tool>",
                "<type>function</type>",
                "<function>",
                f"<name>{escape(self.name)}</name>",
                f"<description>{escape(self.description)}</description>",
                "<parameters>",
                "<type>object</type>",
                "<properties>",
                "<query>",
                "<type>string</type>",
                "<description>Input query for the tool.</description>",
                "</query>",
                "</properties>",
                "<required>",
                "<field>query</field>",
                "</required>",
                "</parameters>",
                "</function>",
                "</tool>",
            ]
        )

    def execute(self, query: str) -> str:
        pass
