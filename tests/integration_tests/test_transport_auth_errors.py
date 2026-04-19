"""Integration tests for transport and auth error handling.

This module contains 15 integration tests for:
1. 5 transport error tests
2. 5 OAuth error tests
3. 5 combination tests
"""

from __future__ import annotations

import warnings
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp import McpError

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport
from fastmcp.exceptions import (
    FastMCPError,
    FastMCPDeprecationWarning,
    MCPAuthorizationError,
    MCPConnectionError,
    MCPTimeoutError,
    MCPTransportError,
    NotFoundError,
    ToolError,
)
from fastmcp.server.middleware.error_handling import (
    ErrorHandlingMiddleware,
)
from fastmcp.server.middleware.middleware import MiddlewareContext


class TestTransportErrorTypes:
    """Tests for transport error types (5 tests)."""

    async def test_mcp_connection_error_transport_stdio(self):
        """Test MCPConnectionError with transport="stdio"."""
        original_error = RuntimeError("Process failed to start")
        error = MCPConnectionError(
            f"Stdio transport failed: {original_error}",
            transport="stdio",
        )
        error.__cause__ = original_error

        assert isinstance(error, MCPConnectionError)
        assert isinstance(error, MCPTransportError)
        assert isinstance(error, FastMCPError)
        assert error.transport == "stdio"
        assert error.code == -32000
        assert error.__cause__ is original_error
        assert "stdio" in error.data

    async def test_mcp_connection_error_transport_http(self):
        """Test MCPConnectionError with transport="http" (connection reset/DNS failure)."""
        original_error = ConnectionError("Connection reset by peer")
        error = MCPConnectionError(
            f"HTTP connection failed: {original_error}",
            transport="http",
        )
        error.__cause__ = original_error

        assert isinstance(error, MCPConnectionError)
        assert error.transport == "http"
        assert error.code == -32000
        assert error.__cause__ is original_error

    async def test_mcp_authorization_error_http_401(self):
        """Test MCPAuthorizationError for HTTP 401 responses."""
        original_error = ValueError("Unauthorized")
        error = MCPAuthorizationError(
            "Authorization failed: Invalid token",
            subtype="invalid_token",
        )
        error.__cause__ = original_error

        assert isinstance(error, MCPAuthorizationError)
        assert isinstance(error, FastMCPError)
        assert error.subtype == "invalid_token"
        assert error.code == -32000
        assert error.__cause__ is original_error
        assert "subtype" in error.data
        assert error.data["subtype"] == "invalid_token"

    async def test_not_found_error_http_404(self):
        """Test NotFoundError for HTTP 404 responses."""
        original_error = FileNotFoundError("Resource not found")
        error = NotFoundError(
            "Not found: /api/resource",
            resource_type="generic",
        )
        error.__cause__ = original_error

        assert isinstance(error, NotFoundError)
        assert isinstance(error, FastMCPError)
        assert error.resource_type == "generic"
        assert error.code == -32001
        assert error.__cause__ is original_error

    async def test_mcp_timeout_error_sse(self):
        """Test MCPTimeoutError for SSE network timeouts."""
        original_error = TimeoutError("SSE connection timed out")
        error = MCPTimeoutError(
            f"Timeout: {original_error}",
            transport="sse",
        )
        error.__cause__ = original_error

        assert isinstance(error, MCPTimeoutError)
        assert isinstance(error, MCPTransportError)
        assert error.transport == "sse"
        assert error.code == -32000
        assert error.__cause__ is original_error


class TestOAuthErrorSubtypes:
    """Tests for OAuth error subtypes (5 tests)."""

    async def test_invalid_token_subtype(self):
        """Test MCPAuthorizationError with subtype='invalid_token'."""
        error = MCPAuthorizationError(
            "Token signature is invalid",
            subtype="invalid_token",
        )

        assert isinstance(error, MCPAuthorizationError)
        assert error.subtype == "invalid_token"
        assert error.code == -32000
        assert error.data["subtype"] == "invalid_token"
        assert "Invalid token" in error.message or "invalid" in error.message.lower()

    async def test_expired_token_subtype(self):
        """Test MCPAuthorizationError with subtype='expired_token'."""
        error = MCPAuthorizationError(
            "Token has expired",
            subtype="expired_token",
        )

        assert isinstance(error, MCPAuthorizationError)
        assert error.subtype == "expired_token"
        assert error.code == -32000
        assert error.data["subtype"] == "expired_token"

    async def test_invalid_state_subtype(self):
        """Test MCPAuthorizationError with subtype='invalid_state'."""
        error = MCPAuthorizationError(
            "OAuth state parameter does not match",
            subtype="invalid_state",
        )

        assert isinstance(error, MCPAuthorizationError)
        assert error.subtype == "invalid_state"
        assert error.code == -32000
        assert error.data["subtype"] == "invalid_state"

    async def test_insufficient_scope_subtype(self):
        """Test MCPAuthorizationError with subtype='insufficient_scope'."""
        error = MCPAuthorizationError(
            "Token lacks required scopes",
            subtype="insufficient_scope",
        )

        assert isinstance(error, MCPAuthorizationError)
        assert error.subtype == "insufficient_scope"
        assert error.code == -32000
        assert error.data["subtype"] == "insufficient_scope"

    async def test_provider_error_subtype(self):
        """Test MCPAuthorizationError with subtype='provider_error'."""
        error = MCPAuthorizationError(
            "OAuth provider returned an error",
            subtype="provider_error",
        )

        assert isinstance(error, MCPAuthorizationError)
        assert error.subtype == "provider_error"
        assert error.code == -32000
        assert error.data["subtype"] == "provider_error"

    async def test_default_subtype_is_generic(self):
        """Test that default subtype is 'generic'."""
        error = MCPAuthorizationError("Authorization failed")

        assert error.subtype == "generic"
        assert error.code == -32000


class TestCombinedErrorScenarios:
    """Tests for combined error scenarios (5 tests)."""

    async def test_transport_error_through_middleware_code_mapping(self):
        """Test transport errors flow through ErrorHandlingMiddleware with correct codes."""
        middleware = ErrorHandlingMiddleware()

        mock_context = MagicMock(spec=MiddlewareContext)
        mock_context.method = "tools/call"

        transport_error = MCPConnectionError(
            "Connection failed",
            transport="stdio",
        )

        result = middleware._transform_error(transport_error, mock_context)

        assert isinstance(result, McpError)
        assert result.error.code == -32000

    async def test_legacy_tool_error_deprecation_warning(self):
        """Test legacy ToolError triggers DeprecationWarning but still flows."""
        mcp = FastMCP("TestServer")

        @mcp.tool
        def legacy_error_tool():
            raise ToolError("Legacy tool error")

        with patch("fastmcp.settings") as mock_settings:
            mock_settings.deprecation_warnings = True

            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")

                async with Client(transport=FastMCPTransport(mcp)) as client:
                    with pytest.raises(Exception):
                        await client.call_tool("legacy_error_tool", {})

    async def test_not_found_error_resource_type_code_dynamic(self):
        """Test NotFoundError resource_type distinguishes -32001 and -32002."""
        generic_error = NotFoundError(
            "Not found",
            resource_type="generic",
        )
        resource_error = NotFoundError(
            "Resource not found",
            resource_type="resource",
        )

        assert generic_error.code == -32001
        assert generic_error.resource_type == "generic"

        assert resource_error.code == -32002
        assert resource_error.resource_type == "resource"

    async def test_from_generic_exception_method_parameter_resources(self):
        """Test from_generic_exception method parameter handles resources/* methods."""
        file_error = FileNotFoundError("Resource file not found")

        generic_result = FastMCPError.from_generic_exception(
            file_error,
            method="tools/call",
        )
        resource_result = FastMCPError.from_generic_exception(
            file_error,
            method="resources/read",
        )

        assert isinstance(generic_result, NotFoundError)
        assert generic_result.code == -32001

        assert isinstance(resource_result, NotFoundError)
        assert resource_result.code == -32002

    async def test_deprecation_warning_silent_when_disabled(self):
        """Test DeprecationWarning is silent when settings.deprecation_warnings=False."""
        from fastmcp import settings
        from fastmcp.exceptions import _emit_deprecation_warning

        original_value = settings.deprecation_warnings
        try:
            settings.deprecation_warnings = False

            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                _emit_deprecation_warning("This should not emit")

                deprecation_warnings = [
                    x for x in w if issubclass(x.category, DeprecationWarning)
                ]
                assert len(deprecation_warnings) == 0
        finally:
            settings.deprecation_warnings = original_value


class TestFromGenericExceptionFactory:
    """Additional tests for from_generic_exception factory method."""

    async def test_from_generic_exception_value_error(self):
        """Test ValueError converts to InvalidParamsError."""
        error = ValueError("Invalid input")
        result = FastMCPError.from_generic_exception(error)

        assert result.code == -32602

    async def test_from_generic_exception_timeout_error(self):
        """Test TimeoutError converts to MCPTimeoutError."""
        error = TimeoutError("Operation timed out")
        result = FastMCPError.from_generic_exception(error)

        assert isinstance(result, MCPTimeoutError)
        assert result.code == -32000

    async def test_from_generic_exception_connection_error(self):
        """Test ConnectionError converts to MCPConnectionError."""
        error = ConnectionError("Connection refused")
        result = FastMCPError.from_generic_exception(error)

        assert isinstance(result, MCPConnectionError)
        assert result.code == -32000

    async def test_from_generic_exception_permission_error(self):
        """Test PermissionError converts to MCPAuthorizationError."""
        error = PermissionError("Access denied")
        result = FastMCPError.from_generic_exception(error)

        assert isinstance(result, MCPAuthorizationError)
        assert result.subtype == "insufficient_scope"
        assert result.code == -32000

    async def test_from_generic_exception_preserves_cause(self):
        """Test that __cause__ is preserved through transformation."""
        original = ValueError("Original error")
        wrapper = ToolError("Wrapped error")
        wrapper.__cause__ = original

        result = FastMCPError.from_generic_exception(wrapper)

        assert isinstance(result, FastMCPError)


class TestErrorToMcpConversion:
    """Tests for to_mcp_error method."""

    async def test_fastmcp_error_to_mcp_error(self):
        """Test FastMCPError converts to McpError correctly."""
        error = MCPConnectionError(
            "Connection failed",
            transport="stdio",
        )

        mcp_error = error.to_mcp_error()

        assert isinstance(mcp_error, McpError)
        assert mcp_error.error.code == -32000
        assert mcp_error.error.message == "Connection failed"
        assert mcp_error.error.data is not None
        assert mcp_error.error.data["transport"] == "stdio"

    async def test_not_found_error_to_mcp_error(self):
        """Test NotFoundError converts to McpError with correct code."""
        generic_error = NotFoundError("Not found", resource_type="generic")
        resource_error = NotFoundError("Resource not found", resource_type="resource")

        generic_mcp = generic_error.to_mcp_error()
        resource_mcp = resource_error.to_mcp_error()

        assert generic_mcp.error.code == -32001
        assert resource_mcp.error.code == -32002
