"""
定义 AuraBot MCP transport、会话与工具调用的稳定异常分类。

client/session/validator 在各自边界抛出具体子类，ToolCallGateway 再统一转换执行结果；
本模块只表达错误语义，不发送请求，也不决定重试或 Task 状态。
"""


class MCPClientError(RuntimeError):
    """Base class for MCP client failures."""


class MCPConnectionError(MCPClientError):
    """Raised when a Streamable HTTP transport or session cannot be opened."""


class MCPInitializationError(MCPClientError):
    """Raised when the MCP initialize handshake fails."""


class MCPProtocolError(MCPClientError):
    """Raised when an MCP protocol operation fails."""


class MCPToolCallError(MCPClientError):
    """Raised when a tools/call request cannot produce a protocol result."""


class MCPTimeoutError(MCPClientError):
    """Raised when an MCP operation exceeds its configured deadline."""
