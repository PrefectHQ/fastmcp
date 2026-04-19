"""Custom exceptions for FastMCP.

This module defines the complete exception hierarchy for FastMCP, including:
- Base exceptions for FastMCP-specific errors
- MCP protocol-specific errors with proper error codes
- Transport layer errors for different transport types
- Authorization errors with subtype classification
- Legacy exceptions with deprecation warnings

All exceptions follow the MCP protocol error code specification:
- -32700: Parse error
- -32600: Invalid Request
- -32601: Method not found
- -32602: Invalid params
- -32603: Internal error
- -32000 to -32099: Server error range
"""

from __future__ import annotations

import warnings
from typing import Any, Literal

from mcp import McpError  # noqa: F401
from mcp.types import ErrorData

from fastmcp import settings


class FastMCPDeprecationWarning(DeprecationWarning):
    """Deprecation warning for FastMCP APIs.

    Subclass of DeprecationWarning so that standard warning filters
    still apply, but FastMCP can selectively enable its own warnings
    without affecting other libraries in the process.
    """


def _emit_deprecation_warning(message: str) -> None:
    """Emit a deprecation warning if enabled in settings."""
    if settings.deprecation_warnings:
        warnings.warn(message, FastMCPDeprecationWarning, stacklevel=3)


class FastMCPError(Exception):
    """Base error for FastMCP.

    All FastMCP-specific exceptions inherit from this base class.
    Provides consistent error handling and conversion to MCP protocol errors.
    """

    default_code: int = -32603
    default_message: str = "FastMCP error"

    def __init__(
        self,
        message: str | None = None,
        code: int | None = None,
        data: dict[str, Any] | None = None,
    ):
        self._message = message or self.default_message
        self._code = code if code is not None else self.default_code
        self._data = data or {}
        super().__init__(self._message)

    @property
    def code(self) -> int:
        return self._code

    @property
    def message(self) -> str:
        return self._message

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    def to_mcp_error(self) -> McpError:
        """Convert this error to an MCP protocol error.

        Returns:
            McpError: The error in MCP protocol format.
        """
        return McpError(
            ErrorData(
                code=self._code,
                message=self._message,
                data=self._data if self._data else None,
            )
        )

    @classmethod
    def from_generic_exception(
        cls,
        exc: Exception,
        method: str | None = None,
    ) -> FastMCPError:
        """Convert a generic exception to the most appropriate FastMCP error.

        This factory method inspects the exception type and context to choose
        the most appropriate FastMCP error subclass, preserving the original
        exception as the cause.

        Args:
            exc: The original exception to convert.
            method: Optional MCP method name for context-aware classification.

        Returns:
            FastMCPError: An appropriate subclass of FastMCPError.
        """
        if isinstance(exc, FastMCPError):
            return exc

        exc_type = type(exc.__cause__) if exc.__cause__ else type(exc)

        if exc_type in (ValueError, TypeError):
            return InvalidParamsError(str(exc))

        elif exc_type in (FileNotFoundError, KeyError):
            if method and method.startswith("resources/"):
                return NotFoundError(str(exc), resource_type="resource")
            return NotFoundError(str(exc))

        elif exc_type is PermissionError:
            return MCPAuthorizationError(
                str(exc),
                subtype="insufficient_scope",
            )

        elif exc_type in (TimeoutError,):
            return MCPTimeoutError(str(exc))

        elif exc_type is ConnectionError:
            return MCPConnectionError(str(exc))

        elif isinstance(exc, MCPTransportError):
            return exc

        elif isinstance(exc, MCPAuthorizationError):
            return exc

        else:
            return InternalError(str(exc))


class ValidationError(FastMCPError):
    """Error in validating parameters or return values."""

    default_code: int = -32602
    default_message: str = "Validation error"


class ParseError(FastMCPError):
    """Error parsing a request or response."""

    default_code: int = -32700
    default_message: str = "Parse error"


class InvalidRequestError(FastMCPError):
    """The request is not a valid JSON-RPC request."""

    default_code: int = -32600
    default_message: str = "Invalid request"


class MethodNotFoundError(FastMCPError):
    """The requested method does not exist."""

    default_code: int = -32601
    default_message: str = "Method not found"


class InvalidParamsError(FastMCPError):
    """Invalid method parameters."""

    default_code: int = -32602
    default_message: str = "Invalid params"


class InternalError(FastMCPError):
    """Internal JSON-RPC error."""

    default_code: int = -32603
    default_message: str = "Internal error"


class ResourceError(FastMCPError):
    """Error in resource operations."""

    default_code: int = -32000
    default_message: str = "Resource error"


class ToolError(FastMCPError):
    """Error in tool operations."""

    default_code: int = -32602
    default_message: str = "Tool error"


class PromptError(FastMCPError):
    """Error in prompt operations."""

    default_code: int = -32000
    default_message: str = "Prompt error"


class InvalidSignature(Exception):
    """Invalid signature for use with FastMCP."""


class ClientError(Exception):
    """Error in client operations."""


class NotFoundError(FastMCPError):
    """Object not found.

    This error can represent both general "not found" situations (-32001)
    and resource-specific "not found" situations (-32002) based on the
    resource_type parameter.

    The MCP spec defines -32002 specifically for resource not found errors,
    while -32001 is used for general not found situations.
    """

    default_code: int = -32001
    default_message: str = "Not found"

    def __init__(
        self,
        message: str | None = None,
        code: int | None = None,
        data: dict[str, Any] | None = None,
        resource_type: Literal["generic", "resource"] = "generic",
    ):
        self.resource_type = resource_type

        if resource_type == "resource" and code is None:
            code = -32002

        if data is None:
            data = {}
        if "resource_type" not in data:
            data["resource_type"] = resource_type

        if message is None:
            if resource_type == "resource":
                message = "Resource not found"
            else:
                message = "Not found"

        super().__init__(message=message, code=code, data=data)


class DisabledError(Exception):
    """Object is disabled."""


class AuthorizationError(FastMCPError):
    """Error when authorization check fails."""

    default_code: int = -32000
    default_message: str = "Authorization error"


class MCPTransportError(FastMCPError):
    """Base error for transport layer issues.

    Transport errors occur at the communication layer between client and server.
    They include connection issues, timeouts, and protocol-specific errors.

    Attributes:
        transport: The transport type where the error occurred ("stdio", "sse", "http").
    """

    default_code: int = -32000
    default_message: str = "Transport error"

    def __init__(
        self,
        message: str | None = None,
        code: int | None = None,
        data: dict[str, Any] | None = None,
        transport: Literal["stdio", "sse", "http"] | None = None,
    ):
        self.transport: str | None = transport

        if transport is not None:
            if data is None:
                data = {}
            if "transport" not in data:
                data["transport"] = transport
            if message is None:
                message = f"{self.__class__.__name__} ({transport})"

        super().__init__(message=message, code=code, data=data)


class MCPConnectionError(MCPTransportError):
    """Error establishing or maintaining a connection.

    This error is raised when:
    - A connection cannot be established (process startup failure, DNS failure)
    - An existing connection is broken (stdin/stdout closed, connection reset)
    - Connection attempts timeout
    """

    default_code: int = -32000
    default_message: str = "Connection error"


class MCPTimeoutError(MCPTransportError):
    """Error when a transport operation times out.

    This error is raised when:
    - Network requests timeout
    - Connection attempts take too long
    - Read/write operations exceed timeout thresholds
    """

    default_code: int = -32000
    default_message: str = "Timeout error"


class MCPDisconnectedError(MCPTransportError):
    """Error when the transport is unexpectedly disconnected.

    This error is raised when:
    - The connection was active but then closed
    - The server or client terminated unexpectedly
    """

    default_code: int = -32000
    default_message: str = "Disconnected"


MCPAuthorizationSubtype = Literal[
    "invalid_token",
    "expired_token",
    "invalid_state",
    "insufficient_scope",
    "provider_error",
    "generic",
]


class MCPAuthorizationError(FastMCPError):
    """Error for authorization and authentication failures.

    This error class provides detailed classification of authorization failures
    through the `subtype` field, allowing for more granular error handling
    and better user feedback.

    Subtypes:
        - invalid_token: Token is malformed or has invalid signature
        - expired_token: Token has expired
        - invalid_state: OAuth state parameter does not match
        - insufficient_scope: Token lacks required scopes
        - provider_error: OAuth provider returned an error
        - generic: Catch-all for other authorization errors

    Attributes:
        subtype: The specific type of authorization error.
    """

    default_code: int = -32000
    default_message: str = "Authorization error"

    def __init__(
        self,
        message: str | None = None,
        code: int | None = None,
        data: dict[str, Any] | None = None,
        subtype: MCPAuthorizationSubtype = "generic",
    ):
        self.subtype: MCPAuthorizationSubtype = subtype

        if data is None:
            data = {}
        if "subtype" not in data:
            data["subtype"] = subtype

        if message is None:
            subtype_messages = {
                "invalid_token": "Invalid token",
                "expired_token": "Token expired",
                "invalid_state": "Invalid state parameter",
                "insufficient_scope": "Insufficient scope",
                "provider_error": "Provider error",
                "generic": "Authorization error",
            }
            message = subtype_messages.get(subtype, "Authorization error")

        super().__init__(message=message, code=code, data=data)
