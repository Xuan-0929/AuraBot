"""Execute authorized tool calls through MCP and normalize their results."""

from collections.abc import Sequence
from typing import Any

from jsonschema import SchemaError
from jsonschema.validators import validator_for
from mcp.types import CallToolResult

from ...integration.mcp import (
    MCPClient,
    MCPClientError,
    MCPConnectionError,
    MCPInitializationError,
    MCPProtocolError,
    MCPTimeoutError,
    MCPToolCallError,
)
from .ToolCallEntity import (
    ResolvedToolCall,
    ToolExecutionErrorCode,
    ToolExecutionResult,
)


class ToolCallGateway:
    """Validate and execute pre-authorized tool calls."""

    def __init__(self, mcp_client: MCPClient):
        self._mcp_client = mcp_client

    @property
    def mcp_client(self) -> MCPClient:
        return self._mcp_client

    async def execute(self, call: ResolvedToolCall) -> ToolExecutionResult:
        """Execute one resolved call and return the project result protocol."""

        validation_error = _validate_arguments(call)
        if validation_error is not None:
            return validation_error

        try:
            result = await self._mcp_client.call_tool(
                call.tool.server_url,
                call.tool.name,
                call.arguments,
                meta=call.mcp_meta or None,
            )
        except MCPConnectionError:
            return _error_result(
                call,
                "mcp_connection",
                "MCP server connection failed.",
            )
        except MCPInitializationError:
            return _error_result(
                call,
                "mcp_initialization",
                "MCP session initialization failed.",
            )
        except MCPProtocolError:
            return _error_result(
                call,
                "mcp_protocol",
                "MCP protocol request failed.",
            )
        except MCPTimeoutError:
            return _error_result(
                call,
                "mcp_timeout",
                "MCP tool call timed out.",
            )
        except MCPToolCallError:
            return _error_result(
                call,
                "mcp_tool_call",
                "MCP tool call failed.",
            )
        except MCPClientError:
            return _error_result(
                call,
                "mcp_tool_call",
                "MCP client request failed.",
            )
        except ValueError:
            return _error_result(
                call,
                "invalid_tool_route",
                "Tool MCP server route is invalid.",
            )

        return _normalize_result(call, result)

    async def execute_many(
        self,
        calls: Sequence[ResolvedToolCall],
    ) -> list[ToolExecutionResult]:
        """Execute calls sequentially and preserve their original order."""

        results: list[ToolExecutionResult] = []
        for call in calls:
            results.append(await self.execute(call))
        return results


def _validate_arguments(
    call: ResolvedToolCall,
) -> ToolExecutionResult | None:
    schema = call.tool.input_schema
    try:
        validator_type = validator_for(schema)
        validator_type.check_schema(schema)
        validator = validator_type(schema)
    except SchemaError:
        return _error_result(
            call,
            "invalid_tool_schema",
            "Tool input schema is invalid.",
        )

    if next(validator.iter_errors(call.arguments), None) is not None:
        return _error_result(
            call,
            "invalid_arguments",
            "Tool arguments do not match the declared input schema.",
        )
    return None


def _normalize_result(
    call: ResolvedToolCall,
    result: CallToolResult,
) -> ToolExecutionResult:
    content = [
        block.model_dump(mode="json", by_alias=True, exclude_none=True)
        for block in result.content
    ]
    is_error = bool(result.isError)
    return ToolExecutionResult(
        tool_name=call.tool.name,
        call_id=call.call_id,
        content=content,
        structured_content=result.structuredContent,
        is_error=is_error,
        error_code="tool_error" if is_error else None,
    )


def _error_result(
    call: ResolvedToolCall,
    error_code: ToolExecutionErrorCode,
    message: str,
) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=call.tool.name,
        call_id=call.call_id,
        content=[{"type": "text", "text": message}],
        structured_content={"error": {"code": error_code}},
        is_error=True,
        error_code=error_code,
    )


__all__ = ["ToolCallGateway"]
