"""Domain model for one Tool."""

from collections.abc import Sequence

from .utils.formatter import format_tool_intro


class Tool:
    """MCP Tool metadata with an ordered immutable Adaptor pipeline."""

    def __init__(
        self,
        name: str,
        description: str,
        server_url: str | None = None,
        input_schema: dict | None = None,
        output_schema: dict | None = None,
        adaptor_names: Sequence[str] | None = None,
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

        if adaptor_names is not None and (
            isinstance(adaptor_names, (str, bytes))
            or not isinstance(adaptor_names, Sequence)
        ):
            raise TypeError(
                "adaptor_names must be a sequence of names or None"
            )
        snapshot = tuple(adaptor_names or ())
        if any(
            not isinstance(adaptor_name, str)
            or not adaptor_name
            or adaptor_name != adaptor_name.strip()
            for adaptor_name in snapshot
        ):
            raise ValueError(
                "adaptor_names must contain only non-empty trimmed strings"
            )
        if len(set(snapshot)) != len(snapshot):
            raise ValueError("adaptor_names must not contain duplicates")
        self.adaptor_names = snapshot

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
