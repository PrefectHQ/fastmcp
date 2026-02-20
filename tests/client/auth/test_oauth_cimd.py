"""Tests for CIMD (Client ID Metadata Document) support in the OAuth client."""

from __future__ import annotations

import warnings

import httpx
import pytest

from fastmcp.client.auth import OAuth
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.client.transports.sse import SSETransport

VALID_CIMD_URL = "https://myapp.example.com/oauth/client.json"
MCP_SERVER_URL = "https://mcp-server.example.com/mcp"


class TestOAuthClientMetadataURL:
    """Tests for the client_metadata_url parameter on OAuth."""

    def test_stored_on_instance(self):
        oauth = OAuth(client_metadata_url=VALID_CIMD_URL)
        assert oauth._client_metadata_url == VALID_CIMD_URL

    def test_none_by_default(self):
        oauth = OAuth()
        assert oauth._client_metadata_url is None

    def test_passed_to_parent_on_bind(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            oauth = OAuth(client_metadata_url=VALID_CIMD_URL)
            oauth._bind(MCP_SERVER_URL)
        assert oauth.context.client_metadata_url == VALID_CIMD_URL

    def test_none_metadata_url_on_parent(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            oauth = OAuth(mcp_url=MCP_SERVER_URL)
        assert oauth.context.client_metadata_url is None

    def test_unbound_when_no_mcp_url(self):
        oauth = OAuth(client_metadata_url=VALID_CIMD_URL)
        assert oauth._bound is False

    def test_bound_when_mcp_url_provided(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            oauth = OAuth(
                mcp_url=MCP_SERVER_URL,
                client_metadata_url=VALID_CIMD_URL,
            )
        assert oauth._bound is True

    def test_invalid_cimd_url_rejected(self):
        """CIMD URLs must be HTTPS with a non-root path."""
        with pytest.raises(ValueError, match="valid HTTPS URL"):
            OAuth(
                mcp_url=MCP_SERVER_URL,
                client_metadata_url="http://insecure.com/client.json",
            )

    def test_root_path_cimd_url_rejected(self):
        with pytest.raises(ValueError, match="valid HTTPS URL"):
            OAuth(
                mcp_url=MCP_SERVER_URL,
                client_metadata_url="https://example.com/",
            )


class TestOAuthBind:
    """Tests for the _bind() deferred initialization."""

    def test_bind_sets_bound_true(self):
        oauth = OAuth(client_metadata_url=VALID_CIMD_URL)
        assert oauth._bound is False
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            oauth._bind(MCP_SERVER_URL)
        assert oauth._bound is True

    def test_bind_idempotent(self):
        """Second call to _bind is a no-op."""
        oauth = OAuth(client_metadata_url=VALID_CIMD_URL)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            oauth._bind(MCP_SERVER_URL)
            oauth._bind("https://other-server.example.com/mcp")
        # First binding wins
        assert oauth.mcp_url == MCP_SERVER_URL

    def test_bind_sets_mcp_url(self):
        oauth = OAuth(client_metadata_url=VALID_CIMD_URL)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            oauth._bind(MCP_SERVER_URL + "/")
        # Trailing slash stripped
        assert oauth.mcp_url == MCP_SERVER_URL

    def test_bind_creates_token_storage(self):
        oauth = OAuth(client_metadata_url=VALID_CIMD_URL)
        assert not hasattr(oauth, "token_storage_adapter")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            oauth._bind(MCP_SERVER_URL)
        assert hasattr(oauth, "token_storage_adapter")

    async def test_unbound_raises_runtime_error(self):
        """async_auth_flow should fail clearly when OAuth is not bound."""
        oauth = OAuth(client_metadata_url=VALID_CIMD_URL)
        request = httpx.Request("GET", MCP_SERVER_URL)
        with pytest.raises(RuntimeError, match="no server URL"):
            async for _ in oauth.async_auth_flow(request):
                pass

    def test_scopes_forwarded_as_list(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            oauth = OAuth(
                client_metadata_url=VALID_CIMD_URL,
                scopes=["read", "write"],
            )
            oauth._bind(MCP_SERVER_URL)
        assert oauth.context.client_metadata.scope == "read write"

    def test_scopes_forwarded_as_string(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            oauth = OAuth(
                client_metadata_url=VALID_CIMD_URL,
                scopes="read write",
            )
            oauth._bind(MCP_SERVER_URL)
        assert oauth.context.client_metadata.scope == "read write"


class TestOAuthBindFromTransport:
    """Tests that transports call _bind() on OAuth instances."""

    def test_http_transport_binds_oauth(self):
        oauth = OAuth(client_metadata_url=VALID_CIMD_URL)
        assert oauth._bound is False
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            StreamableHttpTransport(MCP_SERVER_URL, auth=oauth)
        assert oauth._bound is True
        assert oauth.mcp_url == MCP_SERVER_URL

    def test_sse_transport_binds_oauth(self):
        oauth = OAuth(client_metadata_url=VALID_CIMD_URL)
        assert oauth._bound is False
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            SSETransport(MCP_SERVER_URL, auth=oauth)
        assert oauth._bound is True
        assert oauth.mcp_url == MCP_SERVER_URL

    def test_http_transport_oauth_string_still_works(self):
        """auth="oauth" should still create a new OAuth instance."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            transport = StreamableHttpTransport(MCP_SERVER_URL, auth="oauth")
        assert isinstance(transport.auth, OAuth)
        assert transport.auth._bound is True
