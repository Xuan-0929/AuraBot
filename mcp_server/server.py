"""AuraBot project-native MCP aggregation server."""

from __future__ import annotations

import inspect
import json
import logging
from typing import Any, Literal

from jsonschema import ValidationError, validate
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, Tool

from FileSystem.errors import ToolFailure
from FileSystem.schemas import TOOL_DEFINITIONS as FILESYSTEM_DEFINITIONS
from FileSystem.tools import TOOL_HANDLERS as FILESYSTEM_HANDLERS
from Plan.errors import PlanToolFailure
from Plan.schemas import TOOL_DEFINITIONS as PLAN_DEFINITIONS
from Plan.schemas import output_invariants_hold
from Plan.tools import PlanToolHandlers


logger = logging.getLogger(__name__)

TOOL_DEFINITIONS = (*FILESYSTEM_DEFINITIONS, *PLAN_DEFINITIONS)
if len({tool.name for tool in TOOL_DEFINITIONS}) != len(TOOL_DEFINITIONS):
    raise RuntimeError("native MCP Tool names must be globally unique")
TOOL_BY_NAME = {tool.name: tool for tool in TOOL_DEFINITIONS}
PLAN_TOOL_NAMES = frozenset(tool.name for tool in PLAN_DEFINITIONS)


class AuraBotNativeMCP(FastMCP[None]):
    """Aggregate explicit FileSystem and Plan protocols in fixed order."""

    def __init__(self, *args, plan_handlers: PlanToolHandlers, **kwargs):
        super().__init__(*args, **kwargs)
        self._plan_handlers = plan_handlers

    async def list_tools(self) -> list[Tool]:
        return list(TOOL_DEFINITIONS)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> CallToolResult:
        tool = TOOL_BY_NAME.get(name)
        if tool is None:
            return _error_result("unknown_tool", "Requested tool is not available.")

        try:
            validate(instance=arguments, schema=tool.inputSchema)
        except ValidationError:
            return _error_result(
                "invalid_arguments",
                "Tool arguments do not match the declared input schema.",
            )

        is_plan_tool = name in PLAN_TOOL_NAMES
        try:
            if is_plan_tool:
                task_name = self._extract_task_name()
                value = await self._plan_handlers.call(
                    name,
                    arguments,
                    task_name,
                )
                if not output_invariants_hold(name, value):
                    raise PlanToolFailure("backend_protocol_error")
            else:
                handler = FILESYSTEM_HANDLERS[name]
                value = handler(arguments)
                if inspect.isawaitable(value):
                    value = await value
            validate(instance=value, schema=tool.outputSchema)
        except (ToolFailure, PlanToolFailure) as exc:
            return _error_result(exc.code, exc.message)
        except ValidationError:
            if is_plan_tool:
                return _error_result(
                    "backend_protocol_error",
                    "The backend Plan service returned an invalid response.",
                )
            logger.exception("Tool %s produced an invalid structured result", name)
            return _error_result(
                "invalid_result",
                "Tool produced an invalid structured result.",
            )
        except Exception:
            logger.exception("Unexpected native tool failure: %s", name)
            if is_plan_tool:
                return _error_result(
                    "internal_error",
                    "Plan tool execution failed.",
                )
            return _error_result("internal_error", "Tool execution failed.")

        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(value, ensure_ascii=False, sort_keys=True),
                )
            ],
            structuredContent=value,
            isError=False,
        )

    def _extract_task_name(self) -> str:
        try:
            meta = self.get_context().request_context.meta
            extras = meta.model_extra if meta is not None else None
        except Exception as exc:
            raise PlanToolFailure("invalid_task_context") from exc
        if not isinstance(extras, dict):
            raise PlanToolFailure("invalid_task_context")
        namespace = extras.get("aurabot")
        task_name = (
            namespace.get("task_name")
            if isinstance(namespace, dict)
            else None
        )
        if (
            not isinstance(task_name, str)
            or not task_name
            or task_name != task_name.strip()
        ):
            raise PlanToolFailure("invalid_task_context")
        return task_name


def _error_result(code: str, message: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        structuredContent={"error": {"code": code, "message": message}},
        isError=True,
    )


def create_server(
    *,
    host: str = "127.0.0.1",
    port: int = 6410,
    streamable_http_path: str = "/mcp",
    backend_port: int = 6401,
    plan_handlers: PlanToolHandlers | None = None,
    log_level: Literal[
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    ] = "INFO",
) -> AuraBotNativeMCP:
    return AuraBotNativeMCP(
        name="AuraBot Native Tools",
        instructions=(
            "AuraBot Task Agent native filesystem and planning tools."
        ),
        host=host,
        port=port,
        streamable_http_path=streamable_http_path,
        json_response=True,
        stateless_http=True,
        log_level=log_level,
        plan_handlers=(
            plan_handlers
            if plan_handlers is not None
            else PlanToolHandlers(backend_port)
        ),
    )


__all__ = [
    "AuraBotNativeMCP",
    "PLAN_TOOL_NAMES",
    "TOOL_BY_NAME",
    "TOOL_DEFINITIONS",
    "create_server",
]
