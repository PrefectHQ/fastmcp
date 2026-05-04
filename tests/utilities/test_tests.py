from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import fastmcp
from fastmcp import FastMCP
from fastmcp.utilities.tests import HeadlessOAuth, temporary_settings


class TestTemporarySettings:
    def test_temporary_settings(self):
        assert fastmcp.settings.log_level == "DEBUG"
        with temporary_settings(log_level="ERROR"):
            assert fastmcp.settings.log_level == "ERROR"
        assert fastmcp.settings.log_level == "DEBUG"


class TestTransportSetting:
    def test_transport_default_is_stdio(self):
        assert fastmcp.settings.transport == "stdio"

    def test_transport_setting_can_be_changed(self):
        with temporary_settings(transport="http"):
            assert fastmcp.settings.transport == "http"
        assert fastmcp.settings.transport == "stdio"

    async def test_run_async_uses_transport_setting(self):
        mcp = FastMCP("test")
        with temporary_settings(transport="http"):
            with patch.object(
                mcp, "run_http_async", new_callable=AsyncMock
            ) as mock_http:
                await mcp.run_async()
                mock_http.assert_called_once()

    async def test_run_async_explicit_transport_overrides_setting(self):
        mcp = FastMCP("test")
        with temporary_settings(transport="http"):
            with patch.object(
                mcp, "run_stdio_async", new_callable=AsyncMock
            ) as mock_stdio:
                await mcp.run_async(transport="stdio")
                mock_stdio.assert_called_once()


class TestHeadlessOAuthCallbackHandler:
    """Regression tests for #4056: blank query values must survive parse_qs.

    The OAuth callback handler in HeadlessOAuth parses the redirect Location
    header. parse_qs without keep_blank_values=True silently drops keys whose
    value is empty (e.g. `?state=`), which mis-models real OAuth callbacks
    where an empty `state` is distinct from a missing one.
    """

    def _make_oauth_with_redirect(self, location: str) -> HeadlessOAuth:
        """Build a HeadlessOAuth with a fake stored 302 response."""
        oauth = HeadlessOAuth.__new__(HeadlessOAuth)
        response = MagicMock()
        response.status_code = 302
        response.headers = {"location": location}
        oauth._stored_response = response
        return oauth

    async def test_callback_preserves_blank_state(self):
        """An explicitly-empty state must round-trip as "" rather than None."""
        oauth = self._make_oauth_with_redirect(
            "https://example.com/callback?code=abc&state="
        )
        auth_code, state = await oauth.callback_handler()
        assert auth_code == "abc"
        assert state == ""

    async def test_callback_returns_none_when_state_missing(self):
        """A truly missing state still returns None (default)."""
        oauth = self._make_oauth_with_redirect("https://example.com/callback?code=abc")
        auth_code, state = await oauth.callback_handler()
        assert auth_code == "abc"
        assert state is None

    async def test_callback_uses_blank_error_description_verbatim(self):
        """When the OAuth provider sends an empty error_description, surface
        it as "" rather than falling back to "Unknown error". The fallback is
        meant for the truly-absent case; with keep_blank_values=True the
        explicit empty value is preserved and used directly.
        """
        oauth = self._make_oauth_with_redirect(
            "https://example.com/callback?error=invalid_request&error_description="
        )
        with pytest.raises(RuntimeError, match=r"invalid_request - $"):
            await oauth.callback_handler()
