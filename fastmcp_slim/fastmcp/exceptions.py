"""Custom exceptions for FastMCP."""

import logging

try:
    from mcp import MCPError
except ImportError:

    class MCPError(Exception):  # type: ignore[no-redef]
        """Fallback used when MCP dependencies are not installed."""


# Catch-compatibility alias for the pre-v2 SDK name. `except McpError` must
# catch SDK-raised `MCPError`, so this is a plain alias (a subclass would not
# catch the base). Construction differs in v2 (`MCPError(code=, message=)`);
# see the migration notes.
McpError = MCPError


class FastMCPDeprecationWarning(DeprecationWarning):
    """Deprecation warning for FastMCP APIs.

    Subclass of DeprecationWarning so that standard warning filters
    still apply, but FastMCP can selectively enable its own warnings
    without affecting other libraries in the process.
    """


class FastMCPError(Exception):
    """Base error for FastMCP."""

    def __init__(self, *args: object, log_level: int = logging.ERROR) -> None:
        super().__init__(*args)
        self.log_level = log_level


class ValidationError(FastMCPError):
    """Error in validating parameters or return values."""


class ResourceError(FastMCPError):
    """Error in resource operations."""


class ToolError(FastMCPError):
    """Error in tool operations."""


class PromptError(FastMCPError):
    """Error in prompt operations."""


class InvalidSignature(Exception):
    """Invalid signature for use with FastMCP."""


class ClientError(Exception):
    """Error in client operations."""


class NotFoundError(Exception):
    """Object not found."""


class DisabledError(Exception):
    """Object is disabled."""


class AuthorizationError(FastMCPError):
    """Error when authorization check fails."""
