"""Custom exceptions for FastMCP."""

import logging
from typing import Any

from mcp_types import INTERNAL_ERROR, INVALID_PARAMS, ErrorData

try:
    from mcp import MCPError
except ImportError:

    class MCPError(Exception):  # type: ignore[no-redef]
        """Fallback used when MCP dependencies are not installed.

        Mirrors the real ``mcp.MCPError`` interface — the ``(code, message,
        data)`` constructor and the ``.error`` ``ErrorData`` payload — so static
        analysis of both construction and read sites (e.g. ``to_mcp_error`` and
        callers reading ``err.error.code``) is valid regardless of which branch
        is in effect.
        """

        def __init__(self, code: int, message: str, data: Any = None) -> None:
            super().__init__(message)
            self.error = ErrorData(code=code, message=message, data=data)


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


class ResourceSecurityError(NotFoundError):
    """A templated resource parameter failed path-security screening.

    Subclasses ``NotFoundError`` so the read handler surfaces a
    non-leaky ``INVALID_PARAMS`` (-32602) "resource not found" error to
    the client — a traversal attempt is indistinguishable from a request
    for a resource that does not exist, and never reveals which parameter
    or policy tripped.
    """


class AuthorizationError(FastMCPError):
    """Error when authorization check fails."""


def to_mcp_error(exc: Exception, *, default_code: int = INTERNAL_ERROR) -> MCPError:
    """Translate a FastMCP exception into a wire-format ``MCPError``.

    Central mapping from FastMCP's public exception types to the JSON-RPC error
    codes defined by the MCP spec (imported from ``mcp_types``). Request-handler
    adapters call this instead of hand-rolling ``MCPError(code=..., ...)`` per
    call site, so the wire codes stay spec-correct and consistent across
    resources, prompts, and tools.

    ``NotFoundError`` and ``DisabledError`` map to ``INVALID_PARAMS`` (-32602):
    per SEP-2164 a request naming a component that does not exist (or is
    disabled) is an invalid-params error, which matches the SDK's own
    ``ResourceNotFoundError -> INVALID_PARAMS`` mapping in ``mcp.server.mcpserver``.
    ``ValidationError`` is also an invalid-params error. Everything else falls
    back to ``default_code`` (``INTERNAL_ERROR`` by default).

    If ``exc`` is already an ``MCPError``, it is returned unchanged so an
    explicit code chosen upstream survives translation.
    """
    if isinstance(exc, MCPError):
        return exc

    message = str(exc)
    if isinstance(exc, (NotFoundError, DisabledError)):
        return MCPError(code=INVALID_PARAMS, message=message)
    if isinstance(exc, ValidationError):
        return MCPError(code=INVALID_PARAMS, message=message)
    return MCPError(code=default_code, message=message)
