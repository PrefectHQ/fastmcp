"""Tests for authorization checks and AuthMiddleware."""

from unittest.mock import Mock

import mcp.types as mcp_types
import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.auth import (
    AccessToken,
    AuthContext,
    require_auth,
    require_scopes,
    restrict_tag,
    run_auth_checks,
)
from fastmcp.server.middleware import AuthMiddleware

# =============================================================================
# Test helpers
# =============================================================================


def make_token(scopes: list[str] | None = None) -> AccessToken:
    """Create a test access token."""
    return AccessToken(
        token="test-token",
        client_id="test-client",
        scopes=scopes or [],
        expires_at=None,
        claims={},
    )


def make_tool() -> Mock:
    """Create a mock tool for testing."""
    tool = Mock()
    tool.tags = set()
    return tool


# =============================================================================
# Tests for require_auth
# =============================================================================


class TestRequireAuth:
    def test_returns_true_with_token(self):
        ctx = AuthContext(token=make_token(), component=make_tool())
        assert require_auth(ctx) is True

    def test_returns_false_without_token(self):
        ctx = AuthContext(token=None, component=make_tool())
        assert require_auth(ctx) is False


# =============================================================================
# Tests for require_scopes
# =============================================================================


class TestRequireScopes:
    def test_returns_true_with_matching_scope(self):
        token = make_token(scopes=["admin"])
        ctx = AuthContext(token=token, component=make_tool())
        check = require_scopes("admin")
        assert check(ctx) is True

    def test_returns_true_with_all_required_scopes(self):
        token = make_token(scopes=["read", "write", "admin"])
        ctx = AuthContext(token=token, component=make_tool())
        check = require_scopes("read", "write")
        assert check(ctx) is True

    def test_returns_false_with_missing_scope(self):
        token = make_token(scopes=["read"])
        ctx = AuthContext(token=token, component=make_tool())
        check = require_scopes("admin")
        assert check(ctx) is False

    def test_returns_false_with_partial_scopes(self):
        token = make_token(scopes=["read"])
        ctx = AuthContext(token=token, component=make_tool())
        check = require_scopes("read", "write")
        assert check(ctx) is False

    def test_returns_false_without_token(self):
        ctx = AuthContext(token=None, component=make_tool())
        check = require_scopes("admin")
        assert check(ctx) is False


# =============================================================================
# Tests for restrict_tag
# =============================================================================


class TestRestrictTag:
    def test_allows_access_when_tag_not_present(self):
        tool = make_tool()
        tool.tags = {"other"}
        ctx = AuthContext(token=None, component=tool)
        check = restrict_tag("admin", scopes=["admin"])
        assert check(ctx) is True

    def test_blocks_access_when_tag_present_without_token(self):
        tool = make_tool()
        tool.tags = {"admin"}
        ctx = AuthContext(token=None, component=tool)
        check = restrict_tag("admin", scopes=["admin"])
        assert check(ctx) is False

    def test_blocks_access_when_tag_present_without_scope(self):
        tool = make_tool()
        tool.tags = {"admin"}
        token = make_token(scopes=["read"])
        ctx = AuthContext(token=token, component=tool)
        check = restrict_tag("admin", scopes=["admin"])
        assert check(ctx) is False

    def test_allows_access_when_tag_present_with_scope(self):
        tool = make_tool()
        tool.tags = {"admin"}
        token = make_token(scopes=["admin"])
        ctx = AuthContext(token=token, component=tool)
        check = restrict_tag("admin", scopes=["admin"])
        assert check(ctx) is True


# =============================================================================
# Tests for run_auth_checks
# =============================================================================


class TestRunAuthChecks:
    def test_single_check_passes(self):
        ctx = AuthContext(token=make_token(), component=make_tool())
        assert run_auth_checks(require_auth, ctx) is True

    def test_single_check_fails(self):
        ctx = AuthContext(token=None, component=make_tool())
        assert run_auth_checks(require_auth, ctx) is False

    def test_multiple_checks_all_pass(self):
        token = make_token(scopes=["admin"])
        ctx = AuthContext(token=token, component=make_tool())
        checks = [require_auth, require_scopes("admin")]
        assert run_auth_checks(checks, ctx) is True

    def test_multiple_checks_one_fails(self):
        token = make_token(scopes=["read"])
        ctx = AuthContext(token=token, component=make_tool())
        checks = [require_auth, require_scopes("admin")]
        assert run_auth_checks(checks, ctx) is False

    def test_empty_list_passes(self):
        ctx = AuthContext(token=None, component=make_tool())
        assert run_auth_checks([], ctx) is True

    def test_custom_lambda_check(self):
        token = make_token()
        token.claims = {"level": 5}
        ctx = AuthContext(token=token, component=make_tool())

        def check(ctx: AuthContext) -> bool:
            return ctx.token is not None and ctx.token.claims.get("level", 0) >= 3

        assert run_auth_checks(check, ctx) is True

    def test_authorization_error_propagates(self):
        """AuthorizationError from auth check should propagate with custom message."""
        from fastmcp.exceptions import AuthorizationError

        def custom_auth_check(ctx: AuthContext) -> bool:
            raise AuthorizationError("Custom denial reason")

        ctx = AuthContext(token=make_token(), component=make_tool())
        with pytest.raises(AuthorizationError, match="Custom denial reason"):
            run_auth_checks(custom_auth_check, ctx)

    def test_generic_exception_is_masked(self):
        """Generic exceptions from auth checks should be masked (return False)."""

        def buggy_auth_check(ctx: AuthContext) -> bool:
            raise ValueError("Unexpected internal error")

        ctx = AuthContext(token=make_token(), component=make_tool())
        # Should return False, not raise the ValueError
        assert run_auth_checks(buggy_auth_check, ctx) is False

    def test_authorization_error_stops_chain(self):
        """AuthorizationError should stop the check chain and propagate."""
        from fastmcp.exceptions import AuthorizationError

        call_order = []

        def check_1(ctx: AuthContext) -> bool:
            call_order.append(1)
            return True

        def check_2(ctx: AuthContext) -> bool:
            call_order.append(2)
            raise AuthorizationError("Explicit denial")

        def check_3(ctx: AuthContext) -> bool:
            call_order.append(3)
            return True

        ctx = AuthContext(token=make_token(), component=make_tool())
        with pytest.raises(AuthorizationError, match="Explicit denial"):
            run_auth_checks([check_1, check_2, check_3], ctx)

        # Check 3 should not be called
        assert call_order == [1, 2]


# =============================================================================
# Tests for tool-level auth with FastMCP
# =============================================================================


def set_token(token: AccessToken | None):
    """Set the access token in the auth context var."""
    if token is None:
        return auth_context_var.set(None)
    return auth_context_var.set(AuthenticatedUser(token))


class TestToolLevelAuth:
    async def test_tool_without_auth_is_visible(self):
        mcp = FastMCP()

        @mcp.tool
        def public_tool() -> str:
            return "public"

        tools = await mcp.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "public_tool"

    async def test_tool_with_auth_hidden_without_token(self):
        mcp = FastMCP()

        @mcp.tool(auth=require_auth)
        def protected_tool() -> str:
            return "protected"

        # No token set - tool should be hidden
        tools = await mcp.list_tools()
        assert len(tools) == 0

    async def test_tool_with_auth_visible_with_token(self):
        mcp = FastMCP()

        @mcp.tool(auth=require_auth)
        def protected_tool() -> str:
            return "protected"

        # Set token in context
        token = make_token()
        tok = set_token(token)
        try:
            tools = await mcp.list_tools()
            assert len(tools) == 1
            assert tools[0].name == "protected_tool"
        finally:
            auth_context_var.reset(tok)

    async def test_tool_with_scope_auth_hidden_without_scope(self):
        mcp = FastMCP()

        @mcp.tool(auth=require_scopes("admin"))
        def admin_tool() -> str:
            return "admin"

        # Token without admin scope
        token = make_token(scopes=["read"])
        tok = set_token(token)
        try:
            tools = await mcp.list_tools()
            assert len(tools) == 0
        finally:
            auth_context_var.reset(tok)

    async def test_tool_with_scope_auth_visible_with_scope(self):
        mcp = FastMCP()

        @mcp.tool(auth=require_scopes("admin"))
        def admin_tool() -> str:
            return "admin"

        # Token with admin scope
        token = make_token(scopes=["admin"])
        tok = set_token(token)
        try:
            tools = await mcp.list_tools()
            assert len(tools) == 1
            assert tools[0].name == "admin_tool"
        finally:
            auth_context_var.reset(tok)

    async def test_get_tool_returns_none_without_auth(self):
        """get_tool() returns None for unauthorized tools (consistent with list filtering)."""
        mcp = FastMCP()

        @mcp.tool(auth=require_auth)
        def protected_tool() -> str:
            return "protected"

        # get_tool() returns None for unauthorized tools
        tool = await mcp.get_tool("protected_tool")
        assert tool is None

    async def test_get_tool_returns_tool_with_auth(self):
        mcp = FastMCP()

        @mcp.tool(auth=require_auth)
        def protected_tool() -> str:
            return "protected"

        token = make_token()
        tok = set_token(token)
        try:
            tool = await mcp.get_tool("protected_tool")
            assert tool is not None
            assert tool.name == "protected_tool"
        finally:
            auth_context_var.reset(tok)


# =============================================================================
# Tests for AuthMiddleware
# =============================================================================


class TestAuthMiddleware:
    """Tests for middleware filtering via MCP handler layer.

    These tests call _list_tools_mcp() which applies middleware during list,
    simulating what happens when a client calls list_tools over MCP.
    """

    async def test_middleware_filters_tools_without_token(self):
        mcp = FastMCP(middleware=[AuthMiddleware(auth=require_auth)])

        @mcp.tool
        def public_tool() -> str:
            return "public"

        # No token - all tools filtered by middleware
        result = await mcp._list_tools_mcp(mcp_types.ListToolsRequest())
        assert len(result.tools) == 0

    async def test_middleware_allows_tools_with_token(self):
        mcp = FastMCP(middleware=[AuthMiddleware(auth=require_auth)])

        @mcp.tool
        def public_tool() -> str:
            return "public"

        token = make_token()
        tok = set_token(token)
        try:
            result = await mcp._list_tools_mcp(mcp_types.ListToolsRequest())
            assert len(result.tools) == 1
        finally:
            auth_context_var.reset(tok)

    async def test_middleware_with_scope_check(self):
        mcp = FastMCP(middleware=[AuthMiddleware(auth=require_scopes("api"))])

        @mcp.tool
        def api_tool() -> str:
            return "api"

        # Token without api scope
        token = make_token(scopes=["read"])
        tok = set_token(token)
        try:
            result = await mcp._list_tools_mcp(mcp_types.ListToolsRequest())
            assert len(result.tools) == 0
        finally:
            auth_context_var.reset(tok)

        # Token with api scope
        token = make_token(scopes=["api"])
        tok = set_token(token)
        try:
            result = await mcp._list_tools_mcp(mcp_types.ListToolsRequest())
            assert len(result.tools) == 1
        finally:
            auth_context_var.reset(tok)

    async def test_middleware_with_restrict_tag(self):
        mcp = FastMCP(
            middleware=[AuthMiddleware(auth=restrict_tag("admin", scopes=["admin"]))]
        )

        @mcp.tool
        def public_tool() -> str:
            return "public"

        @mcp.tool(tags={"admin"})
        def admin_tool() -> str:
            return "admin"

        # No token - public tool allowed, admin tool blocked
        result = await mcp._list_tools_mcp(mcp_types.ListToolsRequest())
        assert len(result.tools) == 1
        assert result.tools[0].name == "public_tool"

        # Token with admin scope - both allowed
        token = make_token(scopes=["admin"])
        tok = set_token(token)
        try:
            result = await mcp._list_tools_mcp(mcp_types.ListToolsRequest())
            assert len(result.tools) == 2
        finally:
            auth_context_var.reset(tok)


# =============================================================================
# Integration tests with Client
# =============================================================================


class TestAuthIntegration:
    async def test_client_only_sees_authorized_tools(self):
        mcp = FastMCP()

        @mcp.tool
        def public_tool() -> str:
            return "public"

        @mcp.tool(auth=require_auth)
        def protected_tool() -> str:
            return "protected"

        async with Client(mcp) as client:
            # No token - only public tool visible
            tools = await client.list_tools()
            assert len(tools) == 1
            assert tools[0].name == "public_tool"

    async def test_client_with_token_sees_all_authorized_tools(self):
        mcp = FastMCP()

        @mcp.tool
        def public_tool() -> str:
            return "public"

        @mcp.tool(auth=require_auth)
        def protected_tool() -> str:
            return "protected"

        # Set token before creating client
        token = make_token()
        tok = set_token(token)
        try:
            async with Client(mcp) as client:
                tools = await client.list_tools()
                tool_names = [t.name for t in tools]
                # With token, both tools should be visible
                assert "public_tool" in tool_names
                assert "protected_tool" in tool_names
        finally:
            auth_context_var.reset(tok)


# =============================================================================
# Tests for transformed tools preserving auth
# =============================================================================


class TestTransformedToolAuth:
    async def test_transformed_tool_preserves_auth(self):
        """Transformed tools should inherit auth from parent."""
        from fastmcp.tools.tool_transform import TransformedTool

        mcp = FastMCP()

        @mcp.tool(auth=require_auth)
        def protected_tool(x: int) -> str:
            return str(x)

        # Get the tool and transform it
        tools = await mcp._local_provider.list_tools()
        original_tool = tools[0]
        assert original_tool.auth is not None

        # Transform the tool
        transformed = TransformedTool.from_tool(
            original_tool,
            name="transformed_protected",
        )

        # Auth should be preserved
        assert transformed.auth is not None
        assert transformed.auth == original_tool.auth

    async def test_transformed_tool_filtered_without_token(self):
        """Transformed tools with auth should be filtered without token."""
        from fastmcp.tools.tool_transform import ToolTransformConfig

        mcp = FastMCP()

        @mcp.tool(auth=require_auth)
        def protected_tool(x: int) -> str:
            return str(x)

        # Add transformation
        mcp.add_tool_transformation(
            "protected_tool", ToolTransformConfig(name="renamed_protected")
        )

        # Without token, transformed tool should not be visible
        tools = await mcp.list_tools()
        assert len(tools) == 0

    async def test_transformed_tool_visible_with_token(self):
        """Transformed tools with auth should be visible with token."""
        from fastmcp.tools.tool_transform import ToolTransformConfig

        mcp = FastMCP()

        @mcp.tool(auth=require_auth)
        def protected_tool(x: int) -> str:
            return str(x)

        # Add transformation
        mcp.add_tool_transformation(
            "protected_tool", ToolTransformConfig(name="renamed_protected")
        )

        # With token, transformed tool should be visible
        token = make_token()
        tok = set_token(token)
        try:
            tools = await mcp.list_tools()
            assert len(tools) == 1
            assert tools[0].name == "renamed_protected"
        finally:
            auth_context_var.reset(tok)


# =============================================================================
# Tests for AuthMiddleware on_call_tool enforcement
# =============================================================================


class TestAuthMiddlewareCallTool:
    async def test_middleware_blocks_call_without_auth(self):
        """AuthMiddleware should raise AuthorizationError on unauthorized call."""

        mcp = FastMCP(middleware=[AuthMiddleware(auth=require_auth)])

        @mcp.tool
        def my_tool() -> str:
            return "result"

        # Without token, calling the tool should raise AuthorizationError
        async with Client(mcp) as client:
            with pytest.raises(Exception) as exc_info:
                await client.call_tool("my_tool", {})
            # The error message should indicate authorization failure
            assert (
                "authorization" in str(exc_info.value).lower()
                or "insufficient" in str(exc_info.value).lower()
            )

    async def test_middleware_allows_call_with_auth(self):
        """AuthMiddleware should allow tool call with valid token."""
        mcp = FastMCP(middleware=[AuthMiddleware(auth=require_auth)])

        @mcp.tool
        def my_tool() -> str:
            return "result"

        # With token, calling the tool should succeed
        token = make_token()
        tok = set_token(token)
        try:
            async with Client(mcp) as client:
                result = await client.call_tool("my_tool", {})
                assert result.content[0].text == "result"
        finally:
            auth_context_var.reset(tok)

    async def test_middleware_blocks_call_with_wrong_scope(self):
        """AuthMiddleware should block calls when scope requirements aren't met."""

        mcp = FastMCP(middleware=[AuthMiddleware(auth=require_scopes("admin"))])

        @mcp.tool
        def admin_tool() -> str:
            return "admin result"

        # With token that lacks admin scope
        token = make_token(scopes=["read"])
        tok = set_token(token)
        try:
            async with Client(mcp) as client:
                with pytest.raises(Exception) as exc_info:
                    await client.call_tool("admin_tool", {})
                assert (
                    "authorization" in str(exc_info.value).lower()
                    or "insufficient" in str(exc_info.value).lower()
                )
        finally:
            auth_context_var.reset(tok)
