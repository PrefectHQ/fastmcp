"""
Test for issue #1863: get_access_token() returns stale token after OAuth refresh.

This test demonstrates the bug where auth_context_var holds a stale token,
but the current HTTP request (via request_ctx) has a fresh token.

The test should FAIL with the current implementation and PASS after the fix.
"""

from unittest.mock import MagicMock

from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext
from starlette.requests import Request

from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import get_access_token


class TestStaleAccessToken:
    """Test that get_access_token returns fresh token from request scope."""

    def test_get_access_token_prefers_request_scope_over_stale_context_var(self):
        """
        Regression test for issue #1863.

        Scenario:
        - auth_context_var has a STALE token (set at HTTP middleware level)
        - request_ctx.request.scope["user"] has a FRESH token (per MCP message)
        - get_access_token() should return the FRESH token

        This simulates the case where:
        1. A Streamable HTTP session was established with token A
        2. auth_context_var was set to token A during session setup
        3. Token expired, client refreshed, got token B
        4. New MCP message arrives with token B in the request
        5. get_access_token() should return token B, not stale token A
        """
        # Create STALE token (in auth_context_var)
        # Using FastMCP's AccessToken to avoid conversion issues
        stale_token = AccessToken(
            token="stale-token-from-initial-auth",
            client_id="test-client",
            scopes=["read"],
        )
        stale_user = AuthenticatedUser(stale_token)

        # Create FRESH token (in request.scope["user"])
        fresh_token = AccessToken(
            token="fresh-token-after-refresh",
            client_id="test-client",
            scopes=["read"],
        )
        fresh_user = AuthenticatedUser(fresh_token)

        # Create a mock request with fresh token in scope
        scope = {
            "type": "http",
            "user": fresh_user,
            "auth": MagicMock(),
        }
        mock_request = Request(scope)

        # Create a mock RequestContext with the request
        mock_request_context = MagicMock(spec=RequestContext)
        mock_request_context.request = mock_request

        # Set up the context vars:
        # - auth_context_var has STALE token
        # - request_ctx has request with FRESH token
        auth_token = auth_context_var.set(stale_user)
        request_token = request_ctx.set(mock_request_context)

        try:
            # Call get_access_token - should return FRESH token
            result = get_access_token()

            # Assert we get the FRESH token, not the stale one
            assert result is not None, "Expected an access token but got None"
            assert result.token == "fresh-token-after-refresh", (
                f"Expected fresh token 'fresh-token-after-refresh' but got '{result.token}'. "
                "get_access_token() is returning the stale token from auth_context_var "
                "instead of the fresh token from request.scope['user']."
            )
        finally:
            # Clean up context vars
            auth_context_var.reset(auth_token)
            request_ctx.reset(request_token)

    def test_get_access_token_falls_back_to_context_var_when_no_request(self):
        """
        Verify that get_access_token falls back to auth_context_var
        when there's no HTTP request available.
        """
        # Create token in auth_context_var using FastMCP's AccessToken
        token = AccessToken(
            token="context-var-token",
            client_id="test-client",
            scopes=["read"],
        )
        user = AuthenticatedUser(token)

        # Set up auth_context_var but NOT request_ctx
        auth_token = auth_context_var.set(user)

        try:
            result = get_access_token()

            assert result is not None
            assert result.token == "context-var-token"
        finally:
            auth_context_var.reset(auth_token)

    def test_get_access_token_returns_none_when_no_auth(self):
        """
        Verify that get_access_token returns None when there's no
        authenticated user anywhere.
        """
        result = get_access_token()
        assert result is None

    def test_get_access_token_falls_back_when_scope_user_is_not_authenticated(self):
        """
        Verify that get_access_token falls back to auth_context_var when
        scope["user"] exists but is not an AuthenticatedUser (e.g., UnauthenticatedUser).
        """
        from starlette.authentication import UnauthenticatedUser

        # Create token in auth_context_var
        token = AccessToken(
            token="context-var-token",
            client_id="test-client",
            scopes=["read"],
        )
        user = AuthenticatedUser(token)

        # Create request with UnauthenticatedUser in scope
        scope = {
            "type": "http",
            "user": UnauthenticatedUser(),
        }
        mock_request = Request(scope)
        mock_request_context = MagicMock(spec=RequestContext)
        mock_request_context.request = mock_request

        auth_token = auth_context_var.set(user)
        request_token = request_ctx.set(mock_request_context)

        try:
            result = get_access_token()

            # Should fall back to auth_context_var since scope user is unauthenticated
            assert result is not None
            assert result.token == "context-var-token"
        finally:
            auth_context_var.reset(auth_token)
            request_ctx.reset(request_token)
