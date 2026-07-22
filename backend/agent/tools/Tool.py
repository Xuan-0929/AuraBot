from .utils.formatter import format_tool_intro


class Tool:
    def __init__(
        self,
        name: str,
        description: str,
        server_url: str | None = None,
        input_schema: dict | None = None,
        output_schema: dict | None = None,
    ):
        self.name = name
        self.description = description

        if input_schema is None:
            raise ValueError("input_schema must be provided")
        if not isinstance(input_schema, dict):
            raise TypeError("input_schema must be a dict")
        if output_schema is not None and not isinstance(output_schema, dict):
            raise TypeError("output_schema must be a dict or None")
        self.input_schema = input_schema
        self.output_schema = output_schema

        if server_url is not None:
            self.server_url = server_url
        else:
            from backend.config import config

            self.server_url = config["local_mcp_server"]

    def load_intro(self) -> str:
        """Generate a complete, prompt-ready XML description of this tool."""
        return format_tool_intro(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
        )
