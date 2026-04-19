"""Custom exceptions for FastMCP.

This module defines a comprehensive exception hierarchy for MCP-related errors,
following JSON-RPC error code conventions (-32xxx range).

Inheritance hierarchy (backward compatible):
- FastMCPError (base for all MCP-related errors)
  - MCPProtocolError (protocol layer: handshake failures, message format errors)
    - MCPParseError
    - MCPInvalidRequestError
    - MCPMethodNotFoundError
  - MCPTransportError (transport layer: connection errors, timeouts)
    - MCPConnectionError
    - MCPTimeoutError
    - MCPDisconnectedError
  - MCPToolError (tool operation base)
    - ToolError (legacy, kept for backward compatibility)
    - ToolNotFoundError
    - ToolArgumentError (parameter validation failures)
    - ToolExecutionError (tool internal execution exceptions)
  - MCPResourceError (resource operation base)
    - ResourceError (legacy, kept for backward compatibility)
    - ResourceNotFoundError
    - ResourceAccessDeniedError
  - MCPValidationError (schema validation failures)
    - ValidationError (legacy, kept for backward compatibility)
  - MCPPromptError (prompt operation base)
    - PromptError (legacy, kept for backward compatibility)
    - PromptNotFoundError
  - MCPAuthorizationError (authorization failures)
    - AuthorizationError (legacy, kept for backward compatibility)
  - NotFoundError (generic not found)
  - MCPInternalError (internal server errors)

Legacy exceptions (kept for backward compatibility):
- ClientError
- InvalidSignature
- DisabledError

JSON-RPC Error Codes (per spec):
- -32700: Parse error
- -32600: Invalid Request
- -32601: Method not found
- -32602: Invalid params
- -32603: Internal error
- -32000 to -32099: Server error (implementation-defined)
"""

from __future__ import annotations

import warnings
from typing import Any

from mcp import McpError  # noqa: F401
from mcp.types import ErrorData

from fastmcp import settings


class FastMCPDeprecationWarning(DeprecationWarning):
    """Deprecation warning for FastMCP APIs.

    Subclass of DeprecationWarning so that standard warning filters
    still apply, but FastMCP can selectively enable its own warnings
    without affecting other libraries in the process.
    """


class FastMCPError(Exception):
    """Base error for all MCP-related errors in FastMCP.

    All MCP-specific exceptions inherit from this class, providing
    a consistent interface for error handling. Users can catch
    ``FastMCPError`` to handle all MCP-related errors uniformly.

    Attributes:
        code: JSON-RPC error code (-32xxx range)
        message: Human-readable error message
        data: Optional additional context data
    """

    code: int = -32603
    message: str = "Internal error"

    def __init__(
        self,
        message: str | None = None,
        code: int | None = None,
        data: dict[str, Any] | None = None,
    ):
        """Initialize a FastMCPError.

        Args:
            message: Human-readable error message. If not provided,
                uses the class-level default.
            code: JSON-RPC error code. If not provided, uses the
                class-level default.
            data: Optional dictionary of additional context data.
        """
        self.message = message if message is not None else self.message
        self.code = code if code is not None else self.code
        self.data = data or {}
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-RPC error response format.

        Returns:
            Dictionary with 'code', 'message', and optionally 'data' keys
            following JSON-RPC 2.0 specification.
        """
        result: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.data:
            result["data"] = self.data
        return result

    def to_error_data(self) -> ErrorData:
        """Convert to MCP ErrorData type.

        Returns:
            mcp.types.ErrorData instance for use with MCP SDK.
        """
        return ErrorData(
            code=self.code,
            message=self.message,
            data=self.data if self.data else None,
        )

    def to_mcp_error(self) -> McpError:
        """Convert to MCP SDK McpError.

        Returns:
            mcp.McpError instance for raising to MCP SDK.
        """
        return McpError(self.to_error_data())

    @classmethod
    def from_generic_exception(
        cls,
        exc: Exception,
        message: str | None = None,
        method: str | None = None,
    ) -> FastMCPError:
        """Create the most appropriate FastMCPError from a generic Exception.

        This factory method inspects the exception type and returns the
        most appropriate FastMCPError subclass, preserving the original
        exception as the ``__cause__``.

        Mapping:
        - ValueError/TypeError -> ToolArgumentError (code=-32602, message="Invalid params: ...")
        - PermissionError -> MCPAuthorizationError (code=-32000, message="Permission denied: ...")
        - TimeoutError/asyncio.TimeoutError -> MCPTimeoutError (code=-32000, message="Request timeout: ...")
        - ConnectionError -> MCPConnectionError (code=-32000, message="Connection error: ...")
        - FileNotFoundError/KeyError -> NotFoundError (code depends on method)
        - Other -> FastMCPError (code=-32603, message="Internal error: ...")

        Args:
            exc: The original exception to wrap
            message: Optional custom message. If not provided, uses str(exc).
            method: Optional MCP method name (e.g., "resources/list"). Used to
                determine NotFoundError code (-32002 for resources/* methods).

        Returns:
            Appropriate FastMCPError subclass with ``exc`` as ``__cause__``.
        """
        import asyncio

        error_msg = message if message is not None else str(exc)
        exc_type = type(exc)

        new_exc: FastMCPError

        if exc_type in (ValueError, TypeError):
            new_exc = ToolArgumentError(f"Invalid params: {error_msg}")
        elif exc_type is PermissionError:
            new_exc = MCPAuthorizationError(f"Permission denied: {error_msg}")
        elif exc_type in (TimeoutError, asyncio.TimeoutError):
            new_exc = MCPTimeoutError(f"Request timeout: {error_msg}")
        elif exc_type is ConnectionError:
            new_exc = MCPConnectionError(f"Connection error: {error_msg}")
        elif exc_type in (FileNotFoundError, KeyError):
            if method and method.startswith("resources/"):
                new_exc = NotFoundError(
                    f"Resource not found: {error_msg}", resource_type="resource"
                )
            else:
                new_exc = NotFoundError(f"Not found: {error_msg}")
        else:
            new_exc = cls(f"Internal error: {error_msg}")

        new_exc.__cause__ = exc
        return new_exc


MCPError = FastMCPError


class MCPProtocolError(FastMCPError):
    """Protocol layer errors.

    Raised when there are issues with the MCP protocol itself,
    such as handshake failures, invalid message formats, or
    protocol version mismatches.

    JSON-RPC code: -32600 (Invalid Request)
    """

    code: int = -32600
    message: str = "Protocol error"


class MCPParseError(MCPProtocolError):
    """Message parsing errors.

    Raised when a JSON message cannot be parsed.

    JSON-RPC code: -32700 (Parse error)
    """

    code: int = -32700
    message: str = "Parse error"


class MCPInvalidRequestError(MCPProtocolError):
    """Invalid request format errors.

    Raised when a request is valid JSON but not a valid JSON-RPC request.

    JSON-RPC code: -32600 (Invalid Request)
    """

    code: int = -32600
    message: str = "Invalid request"


class MCPMethodNotFoundError(MCPProtocolError):
    """Method not found errors.

    Raised when the requested method does not exist.

    JSON-RPC code: -32601 (Method not found)
    """

    code: int = -32601
    message: str = "Method not found"


class MCPTransportError(FastMCPError):
    """Transport layer errors.

    Raised when there are issues with the underlying transport,
    such as connection failures, timeouts, or disconnections.

    JSON-RPC code: -32000 (Server error)
    """

    code: int = -32000
    message: str = "Transport error"


class MCPConnectionError(MCPTransportError):
    """Connection errors.

    Raised when a connection cannot be established or is lost.
    """

    message: str = "Connection error"


class MCPTimeoutError(MCPTransportError):
    """Timeout errors.

    Raised when an operation exceeds the allowed time.
    """

    message: str = "Timeout error"


class MCPDisconnectedError(MCPTransportError):
    """Disconnection errors.

    Raised when the connection is unexpectedly closed.
    """

    message: str = "Disconnected"


class MCPToolError(FastMCPError):
    """Tool operation errors.

    Base class for all tool-related errors.

    JSON-RPC code: -32603 (Internal error)
    """

    code: int = -32603
    message: str = "Tool error"


class ToolError(MCPToolError):
    """Tool operation errors (legacy).

    Legacy exception kept for backward compatibility.
    Prefer using more specific exceptions like:
    - ToolNotFoundError
    - ToolArgumentError
    - ToolExecutionError
    """

    code: int = -32603
    message: str = "Tool error"

    def __init__(
        self,
        message: str | None = None,
        code: int | None = None,
        data: dict[str, Any] | None = None,
    ):
        if type(self) is ToolError and settings.deprecation_warnings:
            warnings.warn(
                "ToolError is deprecated and will be removed in a future version. "
                "Use more specific exceptions instead: "
                "ToolNotFoundError, ToolArgumentError, or ToolExecutionError.",
                FastMCPDeprecationWarning,
                stacklevel=2,
            )
        super().__init__(message=message, code=code, data=data)


class ToolNotFoundError(ToolError):
    """Tool not found errors.

    Raised when a requested tool does not exist.

    JSON-RPC code: -32001
    """

    code: int = -32001
    message: str = "Tool not found"


class ToolArgumentError(ToolError):
    """Tool argument validation errors.

    Raised when tool arguments fail validation.

    JSON-RPC code: -32602 (Invalid params)
    """

    code: int = -32602
    message: str = "Invalid tool arguments"


class ToolExecutionError(ToolError):
    """Tool execution errors.

    Raised when a tool fails during execution. The original exception
    should be preserved as ``__cause__`` using ``raise ... from original``.

    JSON-RPC code: -32603 (Internal error)
    """

    code: int = -32603
    message: str = "Tool execution failed"


class MCPResourceError(FastMCPError):
    """Resource operation errors.

    Base class for all resource-related errors.

    JSON-RPC code: -32603 (Internal error)
    """

    code: int = -32603
    message: str = "Resource error"


class ResourceError(MCPResourceError):
    """Resource operation errors (legacy).

    Legacy exception kept for backward compatibility.
    Prefer using more specific exceptions like:
    - ResourceNotFoundError
    - ResourceAccessDeniedError
    """

    code: int = -32603
    message: str = "Resource error"

    def __init__(
        self,
        message: str | None = None,
        code: int | None = None,
        data: dict[str, Any] | None = None,
    ):
        if type(self) is ResourceError and settings.deprecation_warnings:
            warnings.warn(
                "ResourceError is deprecated and will be removed in a future version. "
                "Use more specific exceptions instead: "
                "ResourceNotFoundError or ResourceAccessDeniedError.",
                FastMCPDeprecationWarning,
                stacklevel=2,
            )
        super().__init__(message=message, code=code, data=data)


class ResourceNotFoundError(ResourceError):
    """Resource not found errors.

    Raised when a requested resource does not exist.

    JSON-RPC code: -32002 (MCP spec-defined for resource not found)
    """

    code: int = -32002
    message: str = "Resource not found"


class ResourceAccessDeniedError(ResourceError):
    """Resource access denied errors.

    Raised when access to a resource is denied.

    JSON-RPC code: -32000
    """

    code: int = -32000
    message: str = "Resource access denied"


class MCPValidationError(FastMCPError):
    """Schema validation errors.

    Raised when data fails schema validation.

    JSON-RPC code: -32602 (Invalid params)
    """

    code: int = -32602
    message: str = "Validation error"


class ValidationError(MCPValidationError):
    """Validation errors (legacy).

    Legacy exception kept for backward compatibility.
    Error in validating parameters or return values.
    """

    code: int = -32602
    message: str = "Validation error"

    def __init__(
        self,
        message: str | None = None,
        code: int | None = None,
        data: dict[str, Any] | None = None,
    ):
        if type(self) is ValidationError and settings.deprecation_warnings:
            warnings.warn(
                "ValidationError is deprecated and will be removed in a future version. "
                "Use MCPValidationError or more specific exceptions instead.",
                FastMCPDeprecationWarning,
                stacklevel=2,
            )
        super().__init__(message=message, code=code, data=data)


class MCPPromptError(FastMCPError):
    """Prompt operation errors.

    Base class for all prompt-related errors.

    JSON-RPC code: -32603 (Internal error)
    """

    code: int = -32603
    message: str = "Prompt error"


class PromptError(MCPPromptError):
    """Prompt operation errors (legacy).

    Legacy exception kept for backward compatibility.
    Error in prompt operations.
    """

    code: int = -32603
    message: str = "Prompt error"

    def __init__(
        self,
        message: str | None = None,
        code: int | None = None,
        data: dict[str, Any] | None = None,
    ):
        if type(self) is PromptError and settings.deprecation_warnings:
            warnings.warn(
                "PromptError is deprecated and will be removed in a future version. "
                "Use MCPPromptError or PromptNotFoundError instead.",
                FastMCPDeprecationWarning,
                stacklevel=2,
            )
        super().__init__(message=message, code=code, data=data)


class PromptNotFoundError(PromptError):
    """Prompt not found errors.

    Raised when a requested prompt does not exist.
    """

    code: int = -32001
    message: str = "Prompt not found"


class MCPAuthorizationError(FastMCPError):
    """Authorization errors.

    Raised when an authorization check fails.

    JSON-RPC code: -32000

    Attributes:
        subtype: Optional subtype for categorizing authorization errors.
            Common values:
            - "invalid_token": Token is invalid (malformed, signature invalid)
            - "expired_token": Token has expired
            - "invalid_state": OAuth state parameter mismatch
            - "insufficient_scope": Token lacks required scope
            - "provider_error": OAuth provider returned an error
            - "generic": Generic authorization failure (default)
    """

    code: int = -32000
    message: str = "Authorization failed"

    def __init__(
        self,
        message: str | None = None,
        code: int | None = None,
        data: dict[str, Any] | None = None,
        subtype: (
            Literal[
                "invalid_token",
                "expired_token",
                "invalid_state",
                "insufficient_scope",
                "provider_error",
                "generic",
            ]
            | None
        ) = None,
    ):
        """Initialize a MCPAuthorizationError.

        Args:
            message: Human-readable error message.
            code: JSON-RPC error code (defaults to -32000).
            data: Optional additional context data.
            subtype: Optional subtype for categorizing the error.
        """
        self.subtype: str = subtype if subtype is not None else "generic"
        if data is None:
            data = {}
        if "subtype" not in data:
            data["subtype"] = self.subtype
        super().__init__(message=message, code=code, data=data)


class AuthorizationError(MCPAuthorizationError):
    """Authorization errors (legacy).

    Legacy exception kept for backward compatibility.
    Error when authorization check fails.
    """

    code: int = -32000
    message: str = "Authorization failed"

    def __init__(
        self,
        message: str | None = None,
        code: int | None = None,
        data: dict[str, Any] | None = None,
        subtype: (
            Literal[
                "invalid_token",
                "expired_token",
                "invalid_state",
                "insufficient_scope",
                "provider_error",
                "generic",
            ]
            | None
        ) = None,
    ):
        if type(self) is AuthorizationError and settings.deprecation_warnings:
            warnings.warn(
                "AuthorizationError is deprecated and will be removed in a future version. "
                "Use MCPAuthorizationError instead.",
                FastMCPDeprecationWarning,
                stacklevel=2,
            )
        super().__init__(message=message, code=code, data=data, subtype=subtype)


class NotFoundError(FastMCPError):
    """Generic not found error.

    Legacy exception kept for backward compatibility.
    Prefer more specific exceptions like:
    - ToolNotFoundError
    - ResourceNotFoundError
    - PromptNotFoundError

    JSON-RPC code: -32001 (or -32002 for resource-related errors)
    """

    code: int = -32001
    message: str = "Not found"

    def __init__(
        self,
        message: str | None = None,
        code: int | None = None,
        data: dict[str, Any] | None = None,
        resource_type: Literal["tool", "resource", "prompt", "generic"] | None = None,
    ):
        """Initialize a NotFoundError.

        Args:
            message: Human-readable error message.
            code: JSON-RPC error code.
            data: Optional additional context data.
            resource_type: Optional type of resource that was not found.
                If set to "resource", the error code will be -32002
                (per MCP spec for resource not found) and the message
                will be prefixed with "Resource not found: ".
        """
        self.resource_type: Literal["tool", "resource", "prompt", "generic"] | None = (
            resource_type
        )

        if resource_type == "resource":
            default_code = -32002
            if message is None:
                message = "Resource not found"
        else:
            default_code = -32001
            if message is None:
                message = "Not found"

        final_code = code if code is not None else default_code
        super().__init__(message=message, code=final_code, data=data)

    def to_error_data(self) -> ErrorData:
        """Convert to MCP ErrorData type.

        For resource-related NotFoundError (resource_type="resource"),
        returns code=-32002 per MCP spec.
        """
        return ErrorData(
            code=self.code,
            message=self.message,
            data=self.data if self.data else None,
        )


class InvalidSignature(Exception):
    """Invalid signature for use with FastMCP.

    Raised when cryptographic signature verification fails.
    """


class ClientError(Exception):
    """Error in client operations.

    Raised for client-side operation failures.
    """


class DisabledError(Exception):
    """Object is disabled.

    Raised when attempting to use a disabled tool, resource, or prompt.
    """


class MCPInternalError(FastMCPError):
    """Internal server errors.

    Raised for unexpected internal errors.

    JSON-RPC code: -32603 (Internal error)
    """

    code: int = -32603
    message: str = "Internal error"
