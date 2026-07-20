"""Formatting and parsing helpers shared by Tool runtime and persistence code."""

import json
from html import escape


def serialize_schema(schema: dict) -> str:
    """Serialize a Tool JSON Schema object to compact JSON."""
    if not isinstance(schema, dict):
        raise TypeError("Tool JSON Schema must be a dict")

    return json.dumps(
        schema,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def parse_schema_json(schema_json: str) -> dict:
    """Parse JSON text and require a Tool JSON Schema object."""
    if not isinstance(schema_json, str):
        raise TypeError("Tool JSON Schema JSON must be a string")

    schema = json.loads(schema_json)
    if not isinstance(schema, dict):
        raise ValueError("Tool JSON Schema JSON must decode to an object")
    return schema


def parse_optional_schema_json(schema_json: str | None) -> dict | None:
    """Parse an optional Tool JSON Schema stored in SQLite."""
    if schema_json is None:
        return None
    return parse_schema_json(schema_json)


def format_tool_intro(
    *,
    name: str,
    description: str,
    input_schema: dict,
    output_schema: dict | None,
) -> str:
    """Generate a prompt-ready XML description for one Tool."""
    descriptor_lines = [
        "<tool>",
        "<type>function</type>",
        "<function>",
        f"<name>{escape(name)}</name>",
        f"<description>{escape(description)}</description>",
        f"<parameters>{escape(serialize_schema(input_schema))}</parameters>",
    ]
    if output_schema is not None:
        descriptor_lines.append(
            f"<returns>{escape(serialize_schema(output_schema))}</returns>"
        )
    descriptor_lines.extend(["</function>", "</tool>"])
    return "\n".join(descriptor_lines)


def format_tool_group_intro(
    *,
    name: str,
    description: str,
    tool_intros: list[str],
) -> str:
    """Generate a prompt-ready XML description for one ToolGroup."""
    descriptor_lines = [
        "<tool_group>",
        f"<name>{escape(name)}</name>",
        f"<description>{escape(description)}</description>",
        "<tools>",
    ]
    descriptor_lines.extend(tool_intros)
    descriptor_lines.extend(["</tools>", "</tool_group>"])
    return "\n".join(descriptor_lines)
