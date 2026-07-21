"""Stable exception taxonomy for AuraBot MCP calls."""


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
