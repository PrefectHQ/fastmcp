"""Compatibility exports for FastMCP exceptions."""

from fastmcp_client.exceptions import (
    AuthorizationError,
    ClientError,
    DisabledError,
    FastMCPDeprecationWarning,
    FastMCPError,
    InvalidSignature,
    NotFoundError,
    PromptError,
    ResourceError,
    ToolError,
    ValidationError,
)
from mcp import McpError

__all__ = [
    "AuthorizationError",
    "ClientError",
    "DisabledError",
    "FastMCPDeprecationWarning",
    "FastMCPError",
    "InvalidSignature",
    "McpError",
    "NotFoundError",
    "PromptError",
    "ResourceError",
    "ToolError",
    "ValidationError",
]
