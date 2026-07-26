"""
提供 agent 工具层访问 Streamable HTTP MCP server 的稳定客户端入口。

ToolCallGateway 把已授权的 resolved call 交给 MCPClient，并接收规范化前的远端结果；
本包不解析 OpenAI tool_call、不持有 Task 状态，也不管理 MCP server 进程。
"""

from .exceptions import (
    MCPClientError,
    MCPConnectionError,
    MCPInitializationError,
    MCPProtocolError,
    MCPTimeoutError,
    MCPToolCallError,
)
from .client import MCPClient

__all__ = [
    "MCPClient",
    "MCPClientError",
    "MCPConnectionError",
    "MCPInitializationError",
    "MCPProtocolError",
    "MCPTimeoutError",
    "MCPToolCallError",
]
