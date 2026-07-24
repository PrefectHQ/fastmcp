"""Tests for authorization checks and AuthMiddleware."""

from unittest.mock import Mock

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.auth import (
    AccessToken,
    AuthContext,
    require_scopes,
    restrict_tag,
    run_auth_checks,
)
from fastmcp.server.middleware import AuthMiddleware
from fastmcp.server.transforms import ToolTransform
from fastmcp.tools.tool_transform import ToolTransformConfig, TransformedTool
from fastmcp.utilities.versions import VersionSpec

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


def make_restricted_tag_server() -> FastMCP:
    return FastMCP(
        middleware=[AuthMiddleware(auth=restrict_tag("admin", scopes=["admin"]))]
    )


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
    async def test_single_check_passes(self):
        ctx = AuthContext(token=make_token(scopes=["test"]), component=make_tool())
        assert await run_auth_checks(require_scopes("test"), ctx) is True

    async def test_single_check_fails(self):
        ctx = AuthContext(token=None, component=make_tool())
        assert await run_auth_checks(require_scopes("test"), ctx) is False

    async def test_multiple_checks_all_pass(self):
        token = make_token(scopes=["test", "admin"])
        ctx = AuthContext(token=token, component=make_tool())
        checks = [require_scopes("test"), require_scopes("admin")]
        assert await run_auth_checks(checks, ctx) is True

    async def test_multiple_checks_one_fails(self):
        token = make_token(scopes=["read"])
        ctx = AuthContext(token=token, component=make_tool())
        checks = [require_scopes("read"), require_scopes("admin")]
        assert await run_auth_checks(checks, ctx) is False

    async def test_empty_list_passes(self):
        ctx = AuthContext(token=None, component=make_tool())
        assert await run_auth_checks([], ctx) is True

    async def test_custom_lambda_check(self):
        token = make_token()
        token.claims = {"level": 5}
        ctx = AuthContext(token=token, component=make_tool())

        def check(ctx: AuthContext) -> bool:
            return ctx.token is not None and ctx.token.claims.get("level", 0) >= 3

        assert await run_auth_checks(check, ctx) is True

    async def test_authorization_error_propagates(self):
        """AuthorizationError from auth check should propagate with custom message."""

        def custom_auth_check(ctx: AuthContext) -> bool:
            raise AuthorizationError("Custom denial reason")

        ctx = AuthContext(token=make_token(), component=make_tool())
        with pytest.raises(AuthorizationError, match="Custom denial reason"):
            await run_auth_checks(custom_auth_check, ctx)

    async def test_generic_exception_is_masked(self):
        """Generic exceptions from auth checks should be masked (return False)."""

        def buggy_auth_check(ctx: AuthContext) -> bool:
            raise ValueError("Unexpected internal error")

        ctx = AuthContext(token=make_token(), component=make_tool())
        # Should return False, not raise the ValueError
        assert await run_auth_checks(buggy_auth_check, ctx) is False

    async def test_authorization_error_stops_chain(self):
        """AuthorizationError should stop the check chain and propagate."""
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
            await run_auth_checks([check_1, check_2, check_3], ctx)

        # Check 3 should not be called
        assert call_order == [1, 2]

    async def test_async_check_passes(self):
        """Async auth check functions should be awaited."""

        async def async_check(ctx: AuthContext) -> bool:
            return ctx.token is not None

        ctx = AuthContext(token=make_token(), component=make_tool())
        assert await run_auth_checks(async_check, ctx) is True

    async def test_async_check_fails(self):
        """Async auth check that returns False should deny access."""

        async def async_check(ctx: AuthContext) -> bool:
            return False

        ctx = AuthContext(token=make_token(), component=make_tool())
        assert await run_auth_checks(async_check, ctx) is False

    async def test_mixed_sync_and_async_checks(self):
        """A mix of sync and async checks should all be evaluated."""

        def sync_check(ctx: AuthContext) -> bool:
            return True

        async def async_check(ctx: AuthContext) -> bool:
            return ctx.token is not None

        ctx = AuthContext(token=make_token(scopes=["test"]), component=make_tool())
        checks = [sync_check, async_check, require_scopes("test")]
        assert await run_auth_checks(checks, ctx) is True

    async def test_async_check_exception_is_masked(self):
        """Async checks that raise non-AuthorizationError should be masked."""

        async def buggy_async_check(ctx: AuthContext) -> bool:
            raise ValueError("async error")

        ctx = AuthContext(token=make_token(), component=make_tool())
        assert await run_auth_checks(buggy_async_check, ctx) is False

    async def test_async_check_authorization_error_propagates(self):
        """Async checks that raise AuthorizationError should propagate."""

        async def async_denial(ctx: AuthContext) -> bool:
            raise AuthorizationError("Async denial")

        ctx = AuthContext(token=make_token(), component=make_tool())
        with pytest.raises(AuthorizationError, match="Async denial"):
            await run_auth_checks(async_denial, ctx)


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

        @mcp.tool(auth=require_scopes("test"))
        def protected_tool() -> str:
            return "protected"

        # No token set - tool should be hidden
        tools = await mcp.list_tools()
        assert len(tools) == 0

    async def test_tool_with_auth_visible_with_token(self):
        mcp = FastMCP()

        @mcp.tool(auth=require_scopes("test"))
        def protected_tool() -> str:
            return "protected"

        # Set token in context
        token = make_token(scopes=["test"])
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

        @mcp.tool(auth=require_scopes("test"))
        def protected_tool() -> str:
            return "protected"

        # get_tool() returns None for unauthorized tools
        tool = await mcp.get_tool("protected_tool")
        assert tool is None

    async def test_get_tool_returns_tool_with_auth(self):
        mcp = FastMCP()

        @mcp.tool(auth=require_scopes("test"))
        def protected_tool() -> str:
            return "protected"

        token = make_token(scopes=["test"])
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
    """Tests for middleware filtering via the MCP handler layer.

    These tests drive an in-memory client so the middleware runs in the dispatch
    chain, exactly as it does when a real client calls list_tools over MCP.
    """

    async def test_middleware_filters_tools_without_token(self):
        mcp = FastMCP(middleware=[AuthMiddleware(auth=require_scopes("test"))])

        @mcp.tool
        def public_tool() -> str:
            return "public"

        # No token - all tools filtered by middleware
        async with Client(mcp) as client:
            tools = await client.list_tools()
        assert len(tools) == 0

    async def test_middleware_allows_tools_with_token(self):
        mcp = FastMCP(middleware=[AuthMiddleware(auth=require_scopes("test"))])

        @mcp.tool
        def public_tool() -> str:
            return "public"

        token = make_token(scopes=["test"])
        tok = set_token(token)
        try:
            async with Client(mcp) as client:
                tools = await client.list_tools()
            assert len(tools) == 1
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
            async with Client(mcp) as client:
                tools = await client.list_tools()
            assert len(tools) == 0
        finally:
            auth_context_var.reset(tok)

        # Token with api scope
        token = make_token(scopes=["api"])
        tok = set_token(token)
        try:
            async with Client(mcp) as client:
                tools = await client.list_tools()
            assert len(tools) == 1
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
        async with Client(mcp) as client:
            tools = await client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "public_tool"

        # Token with admin scope - both allowed
        token = make_token(scopes=["admin"])
        tok = set_token(token)
        try:
            async with Client(mcp) as client:
                tools = await client.list_tools()
            assert len(tools) == 2
        finally:
            auth_context_var.reset(tok)

    async def test_middleware_skips_tool_on_authorization_error(self):
        def deny_blocked_tool(ctx: AuthContext) -> bool:
            if ctx.component.name == "blocked_tool":
                raise AuthorizationError(f"deny {ctx.component.name}")
            return True

        mcp = FastMCP(middleware=[AuthMiddleware(auth=deny_blocked_tool)])

        @mcp.tool
        def blocked_tool() -> str:
            return "blocked"

        @mcp.tool
        def allowed_tool() -> str:
            return "allowed"

        async with Client(mcp) as client:
            tools = await client.list_tools()
        assert [tool.name for tool in tools] == ["allowed_tool"]

    async def test_middleware_skips_resource_on_authorization_error(self):
        def deny_blocked_resource(ctx: AuthContext) -> bool:
            if ctx.component.name == "blocked_resource":
                raise AuthorizationError(f"deny {ctx.component.name}")
            return True

        mcp = FastMCP(middleware=[AuthMiddleware(auth=deny_blocked_resource)])

        @mcp.resource("resource://blocked")
        def blocked_resource() -> str:
            return "blocked"

        @mcp.resource("resource://allowed")
        def allowed_resource() -> str:
            return "allowed"

        async with Client(mcp) as client:
            resources = await client.list_resources()
        assert [str(resource.uri) for resource in resources] == ["resource://allowed"]

    async def test_middleware_skips_resource_template_on_authorization_error(self):
        def deny_blocked_resource_template(ctx: AuthContext) -> bool:
            if ctx.component.name == "blocked_resource_template":
                raise AuthorizationError(f"deny {ctx.component.name}")
            return True

        mcp = FastMCP(middleware=[AuthMiddleware(auth=deny_blocked_resource_template)])

        @mcp.resource("resource://blocked/{item}")
        def blocked_resource_template(item: str) -> str:
            return item

        @mcp.resource("resource://allowed/{item}")
        def allowed_resource_template(item: str) -> str:
            return item

        async with Client(mcp) as client:
            templates = await client.list_resource_templates()
        assert [template.uri_template for template in templates] == [
            "resource://allowed/{item}"
        ]

    async def test_middleware_skips_prompt_on_authorization_error(self):
        def deny_blocked_prompt(ctx: AuthContext) -> bool:
            if ctx.component.name == "blocked_prompt":
                raise AuthorizationError(f"deny {ctx.component.name}")
            return True

        mcp = FastMCP(middleware=[AuthMiddleware(auth=deny_blocked_prompt)])

        @mcp.prompt
        def blocked_prompt() -> str:
            return "blocked"

        @mcp.prompt
        def allowed_prompt() -> str:
            return "allowed"

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
        assert [prompt.name for prompt in prompts] == ["allowed_prompt"]


# =============================================================================
# Integration tests with Client
# =============================================================================


class TestAuthIntegration:
    async def test_client_only_sees_authorized_tools(self):
        mcp = FastMCP()

        @mcp.tool
        def public_tool() -> str:
            return "public"

        @mcp.tool(auth=require_scopes("test"))
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

        @mcp.tool(auth=require_scopes("test"))
        def protected_tool() -> str:
            return "protected"

        # Set token before creating client
        token = make_token(scopes=["test"])
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
# Integration tests with async auth checks
# =============================================================================


class TestAsyncAuthIntegration:
    async def test_async_auth_check_filters_tool_listing(self):
        """Async auth checks should work for filtering tool lists."""
        mcp = FastMCP()

        async def check_claims(ctx: AuthContext) -> bool:
            return ctx.token is not None and ctx.token.claims.get("role") == "admin"

        @mcp.tool(auth=check_claims)
        def admin_tool() -> str:
            return "admin"

        @mcp.tool
        def public_tool() -> str:
            return "public"

        # Without token, only public tool visible
        tools = await mcp.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "public_tool"

        # With correct claims, both visible
        token = make_token()
        token.claims = {"role": "admin"}
        tok = set_token(token)
        try:
            tools = await mcp.list_tools()
            assert len(tools) == 2
        finally:
            auth_context_var.reset(tok)

    async def test_async_auth_check_on_tool_call(self):
        """Async auth checks should work for tool execution via client."""
        mcp = FastMCP()

        async def check_claims(ctx: AuthContext) -> bool:
            return ctx.token is not None and ctx.token.claims.get("role") == "admin"

        @mcp.tool(auth=check_claims)
        def admin_tool() -> str:
            return "secret"

        token = make_token()
        token.claims = {"role": "admin"}
        tok = set_token(token)
        try:
            async with Client(mcp) as client:
                result = await client.call_tool("admin_tool", {})
                assert result.content[0].text == "secret"
        finally:
            auth_context_var.reset(tok)

    async def test_async_auth_middleware(self):
        """Async auth checks should work with AuthMiddleware."""

        async def async_scope_check(ctx: AuthContext) -> bool:
            return ctx.token is not None and "api" in ctx.token.scopes

        mcp = FastMCP(middleware=[AuthMiddleware(auth=async_scope_check)])

        @mcp.tool
        def api_tool() -> str:
            return "api"

        # Without token, tool is hidden
        async with Client(mcp) as client:
            tools = await client.list_tools()
        assert len(tools) == 0

        # With token containing "api" scope, tool is visible
        token = make_token(scopes=["api"])
        tok = set_token(token)
        try:
            async with Client(mcp) as client:
                tools = await client.list_tools()
            assert len(tools) == 1
        finally:
            auth_context_var.reset(tok)


# =============================================================================
# Tests for transformed tools preserving auth
# =============================================================================


class TestTransformedToolAuth:
    async def test_transformed_tool_preserves_auth(self):
        """Transformed tools should inherit auth from parent."""
        mcp = FastMCP()

        @mcp.tool(auth=require_scopes("test"))
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
        mcp = FastMCP()

        @mcp.tool(auth=require_scopes("test"))
        def protected_tool(x: int) -> str:
            return str(x)

        # Add transformation
        mcp.add_transform(
            ToolTransform(
                {"protected_tool": ToolTransformConfig(name="renamed_protected")}
            )
        )

        # Without token, transformed tool should not be visible
        tools = await mcp.list_tools()
        assert len(tools) == 0

    async def test_transformed_tool_visible_with_token(self):
        """Transformed tools with auth should be visible with token."""
        mcp = FastMCP()

        @mcp.tool(auth=require_scopes("test"))
        def protected_tool(x: int) -> str:
            return str(x)

        # Add transformation
        mcp.add_transform(
            ToolTransform(
                {"protected_tool": ToolTransformConfig(name="renamed_protected")}
            )
        )

        # With token, transformed tool should be visible
        token = make_token(scopes=["test"])
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

        mcp = FastMCP(middleware=[AuthMiddleware(auth=require_scopes("test"))])

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
        mcp = FastMCP(middleware=[AuthMiddleware(auth=require_scopes("test"))])

        @mcp.tool
        def my_tool() -> str:
            return "result"

        # With token, calling the tool should succeed
        token = make_token(scopes=["test"])
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


class TestAuthMiddlewareVersionedRequests:
    # The resource/template/prompt denial cases are pinned to legacy: the
    # authorization error message is surfaced to the client on the handshake
    # era, but the modern server runner masks the raised denial as a generic
    # "Internal server error". The tool case surfaces via an isError result and
    # stays era-neutral.
    async def test_middleware_blocks_explicit_restricted_tool_version(self):
        """AuthMiddleware should check the requested tool version."""
        mcp = make_restricted_tag_server()

        @mcp.tool(name="calc", version="1.0", tags={"admin"})
        def calc_v1() -> str:
            return "restricted"

        @mcp.tool(name="calc", version="2.0")
        def calc_v2() -> str:
            return "public"

        tok = set_token(make_token(scopes=["read"]))
        try:
            async with Client(mcp) as client:
                with pytest.raises(Exception, match="authorization|insufficient"):
                    await client.call_tool("calc", {}, version="1.0")
        finally:
            auth_context_var.reset(tok)

    async def test_middleware_blocks_restricted_tool_version_selected_by_range(self):
        """AuthMiddleware should check non-exact direct server version specs."""
        mcp = make_restricted_tag_server()

        @mcp.tool(name="calc", version="1.0", tags={"admin"})
        def calc_v1() -> str:
            return "restricted"

        @mcp.tool(name="calc", version="2.0")
        def calc_v2() -> str:
            return "public"

        tok = set_token(make_token(scopes=["read"]))
        try:
            with pytest.raises(AuthorizationError):
                await mcp.call_tool("calc", {}, version=VersionSpec(lt="2.0"))
        finally:
            auth_context_var.reset(tok)

    async def test_middleware_blocks_explicit_restricted_resource_version(self):
        """AuthMiddleware should check the requested resource version."""
        mcp = make_restricted_tag_server()

        @mcp.resource("data://info", version="1.0", tags={"admin"})
        def info_v1() -> str:
            return "restricted"

        @mcp.resource("data://info", version="2.0")
        def info_v2() -> str:
            return "public"

        tok = set_token(make_token(scopes=["read"]))
        try:
            async with Client(mcp, mode="legacy") as client:
                with pytest.raises(Exception, match="authorization|insufficient"):
                    await client.read_resource("data://info", version="1.0")
        finally:
            auth_context_var.reset(tok)

    async def test_middleware_blocks_explicit_restricted_template_version(self):
        """AuthMiddleware should check the requested resource template version."""
        mcp = make_restricted_tag_server()

        @mcp.resource("data://items/{item_id}", version="1.0", tags={"admin"})
        def item_v1(item_id: str) -> str:
            return f"restricted {item_id}"

        @mcp.resource("data://items/{item_id}", version="2.0")
        def item_v2(item_id: str) -> str:
            return f"public {item_id}"

        tok = set_token(make_token(scopes=["read"]))
        try:
            async with Client(mcp, mode="legacy") as client:
                with pytest.raises(Exception, match="authorization|insufficient"):
                    await client.read_resource("data://items/123", version="1.0")
        finally:
            auth_context_var.reset(tok)

    async def test_middleware_blocks_explicit_restricted_prompt_version(self):
        """AuthMiddleware should check the requested prompt version."""
        mcp = make_restricted_tag_server()

        @mcp.prompt(name="greet", version="1.0", tags={"admin"})
        def greet_v1() -> str:
            return "restricted"

        @mcp.prompt(name="greet", version="2.0")
        def greet_v2() -> str:
            return "public"

        tok = set_token(make_token(scopes=["read"]))
        try:
            async with Client(mcp, mode="legacy") as client:
                with pytest.raises(Exception, match="authorization|insufficient"):
                    await client.get_prompt("greet", version="1.0")
        finally:
            auth_context_var.reset(tok)


# =============================================================================
# Tests for component-level auth denial messaging (issue #4054 bug 1)
# =============================================================================


def _allow_all(ctx: AuthContext) -> bool:
    """Global auth check that always passes (component-level auth still applies)."""
    return True


class TestComponentAuthDenialMessage:
    """When component-level auth denies access, get_tool/get_resource/get_prompt
    return None, so the middleware cannot distinguish "missing" from "denied".

    The message must stay ambiguous ("not found or not authorized") rather than
    asserting the component does not exist (misleading) or that it exists but is
    forbidden (leaks existence to unauthorized callers).

    The resource/prompt cases are pinned to legacy: their denial message is
    surfaced only on the handshake era, where the read/get path converts the
    error to a client-visible message; the modern server runner masks it as a
    generic "Internal server error". The tool case surfaces via an isError
    result and stays era-neutral.
    """

    async def test_call_tool_denied_by_component_auth(self):
        mcp = FastMCP(middleware=[AuthMiddleware(auth=_allow_all)])

        @mcp.tool(auth=require_scopes("admin"))
        def secret_tool() -> str:
            return "secret"

        token = make_token(scopes=["read"])
        tok = set_token(token)
        try:
            async with Client(mcp) as client:
                with pytest.raises(Exception) as exc_info:
                    await client.call_tool("secret_tool", {})
            message = str(exc_info.value)
            assert "not found or not authorized" in message
        finally:
            auth_context_var.reset(tok)

    async def test_read_resource_denied_by_component_auth(self):
        mcp = FastMCP(middleware=[AuthMiddleware(auth=_allow_all)])

        @mcp.resource("data://secret", auth=require_scopes("admin"))
        def secret_resource() -> str:
            return "secret"

        token = make_token(scopes=["read"])
        tok = set_token(token)
        try:
            async with Client(mcp, mode="legacy") as client:
                with pytest.raises(Exception) as exc_info:
                    await client.read_resource("data://secret")
            message = str(exc_info.value)
            assert "not found or not authorized" in message
        finally:
            auth_context_var.reset(tok)

    async def test_get_prompt_denied_by_component_auth(self):
        mcp = FastMCP(middleware=[AuthMiddleware(auth=_allow_all)])

        @mcp.prompt(auth=require_scopes("admin"))
        def secret_prompt() -> str:
            return "secret"

        token = make_token(scopes=["read"])
        tok = set_token(token)
        try:
            async with Client(mcp, mode="legacy") as client:
                with pytest.raises(Exception) as exc_info:
                    await client.get_prompt("secret_prompt")
            message = str(exc_info.value)
            assert "not found or not authorized" in message
        finally:
            auth_context_var.reset(tok)
