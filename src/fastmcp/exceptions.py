"""Custom exceptions for FastMCP."""

from mcp import McpError  # noqa: F401


class FastMCPDeprecationWarning(DeprecationWarning):
    """Deprecation warning for FastMCP APIs.

    Subclass of DeprecationWarning so that standard warning filters
    still apply, but FastMCP can selectively enable its own warnings
    without affecting other libraries in the process.
    """


class FastMCPError(Exception):
    """Base error for FastMCP."""

    def __init__(self, *args: object, suppress_log: bool = False) -> None:
        super().__init__(*args)
        self.suppress_log = suppress_log


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
