from unittest.mock import patch
from urllib.parse import urlparse

import httpx
import pytest
from mcp.types import TextResourceContents

from fastmcp.client import Client
from fastmcp.client.auth import OAuth
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.auth.auth import ClientRegistrationOptions
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from fastmcp.server.server import FastMCP
from fastmcp.utilities.http import find_available_port
from fastmcp.utilities.tests import HeadlessOAuth, run_server_async


def fastmcp_server(issuer_url: str):
    """Create a FastMCP server with OAuth authentication."""
    server = FastMCP(
        "TestServer",
        auth=InMemoryOAuthProvider(
            base_url=issuer_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=["read", "write"]
            ),
        ),
    )

    @server.tool
    def add(a: int, b: int) -> int:
        """Add two numbers together."""
        return a + b

    @server.resource("resource://test")
    def get_test_resource() -> str:
        """Get a test resource."""
        return "Hello from authenticated resource!"

    return server


@pytest.fixture
async def streamable_http_server():
    """Start OAuth-enabled server."""
    port = find_available_port()
    server = fastmcp_server(f"http://127.0.0.1:{port}")
    async with run_server_async(server, port=port, transport="http") as url:
        yield url


@pytest.fixture
def client_unauthorized(streamable_http_server: str) -> Client:
    return Client(transport=StreamableHttpTransport(streamable_http_server))


@pytest.fixture
def client_with_headless_oauth(streamable_http_server: str) -> Client:
    """Client with headless OAuth that bypasses browser interaction."""
    return Client(
        transport=StreamableHttpTransport(streamable_http_server),
        auth=HeadlessOAuth(mcp_url=streamable_http_server, scopes=["read", "write"]),
    )


async def test_unauthorized(client_unauthorized: Client):
    """Test that unauthenticated requests are rejected."""
    with pytest.raises(httpx.HTTPStatusError, match="401 Unauthorized"):
        async with client_unauthorized:
            pass


async def test_ping(client_with_headless_oauth: Client):
    """Test that we can ping the server."""
    async with client_with_headless_oauth:
        assert await client_with_headless_oauth.ping()


async def test_list_tools(client_with_headless_oauth: Client):
    """Test that we can list tools."""
    async with client_with_headless_oauth:
        tools = await client_with_headless_oauth.list_tools()
        tool_names = [tool.name for tool in tools]
        assert "add" in tool_names


async def test_call_tool(client_with_headless_oauth: Client):
    """Test that we can call a tool."""
    async with client_with_headless_oauth:
        result = await client_with_headless_oauth.call_tool("add", {"a": 5, "b": 3})
        # The add tool returns int which gets wrapped as structured output
        # Client unwraps it and puts the actual int in the data field
        assert result.data == 8


async def test_list_resources(client_with_headless_oauth: Client):
    """Test that we can list resources."""
    async with client_with_headless_oauth:
        resources = await client_with_headless_oauth.list_resources()
        resource_uris = [str(resource.uri) for resource in resources]
        assert "resource://test" in resource_uris


async def test_read_resource(client_with_headless_oauth: Client):
    """Test that we can read a resource."""
    async with client_with_headless_oauth:
        resource = await client_with_headless_oauth.read_resource("resource://test")
        assert isinstance(resource[0], TextResourceContents)
        assert resource[0].text == "Hello from authenticated resource!"


async def test_oauth_server_metadata_discovery(streamable_http_server: str):
    """Test that we can discover OAuth metadata from the running server."""
    parsed_url = urlparse(streamable_http_server)
    server_base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

    async with httpx.AsyncClient() as client:
        # Test OAuth discovery endpoint
        metadata_url = f"{server_base_url}/.well-known/oauth-authorization-server"
        response = await client.get(metadata_url)
        assert response.status_code == 200

        metadata = response.json()
        assert "authorization_endpoint" in metadata
        assert "token_endpoint" in metadata
        assert "registration_endpoint" in metadata

        # The endpoints should be properly formed URLs
        assert metadata["authorization_endpoint"].startswith(server_base_url)
        assert metadata["token_endpoint"].startswith(server_base_url)


class TestOAuthClientUrlHandling:
    """Tests for OAuth client URL handling (issue #2573)."""

    def test_oauth_preserves_full_url_with_path(self):
        """OAuth client should preserve the full MCP URL including path components.

        This is critical for servers hosted under path-based endpoints like
        mcp.example.com/server1/v1.0/mcp where OAuth metadata discovery needs
        the full path to find the correct .well-known endpoints.
        """
        mcp_url = "https://mcp.example.com/server1/v1.0/mcp"
        oauth = OAuth(mcp_url=mcp_url)

        # The full URL should be preserved for OAuth discovery
        assert oauth.context.server_url == mcp_url

        # The stored mcp_url should match
        assert oauth.mcp_url == mcp_url

    def test_oauth_preserves_root_url(self):
        """OAuth client should work correctly with root-level URLs."""
        mcp_url = "https://mcp.example.com"
        oauth = OAuth(mcp_url=mcp_url)

        assert oauth.context.server_url == mcp_url
        assert oauth.mcp_url == mcp_url

    def test_oauth_normalizes_trailing_slash(self):
        """OAuth client should normalize trailing slashes for consistency."""
        mcp_url_with_slash = "https://mcp.example.com/api/mcp/"
        oauth = OAuth(mcp_url=mcp_url_with_slash)

        # Trailing slash should be stripped
        expected = "https://mcp.example.com/api/mcp"
        assert oauth.context.server_url == expected
        assert oauth.mcp_url == expected

    def test_oauth_token_storage_uses_full_url(self):
        """Token storage should use the full URL to separate tokens per endpoint."""
        mcp_url = "https://mcp.example.com/server1/v1.0/mcp"
        oauth = OAuth(mcp_url=mcp_url)

        # Token storage should key by the full URL, not just the host
        assert oauth.token_storage_adapter._server_url == mcp_url


class TestOAuthGeneratorCleanup:
    """Tests for OAuth async generator cleanup (issue #2643).

    The MCP SDK's OAuthClientProvider.async_auth_flow() holds a lock via
    `async with self.context.lock`. If the generator is not explicitly closed,
    GC may clean it up from a different task, causing:
    RuntimeError: The current task is not holding this lock
    """

    async def test_generator_closed_on_successful_flow(self):
        """Verify aclose() is called on the parent generator after successful flow."""
        oauth = OAuth(mcp_url="https://example.com")

        # Track generator lifecycle using a wrapper class
        class TrackedGenerator:
            def __init__(self):
                self.aclose_called = False
                self._exhausted = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._exhausted:
                    raise StopAsyncIteration
                self._exhausted = True
                return httpx.Request("GET", "https://example.com")

            async def asend(self, value):
                if self._exhausted:
                    raise StopAsyncIteration
                self._exhausted = True
                return httpx.Request("GET", "https://example.com")

            async def athrow(self, exc_type, exc_val=None, exc_tb=None):
                raise StopAsyncIteration

            async def aclose(self):
                self.aclose_called = True

        tracked_gen = TrackedGenerator()

        # Patch the parent class to return our tracked generator
        with patch.object(
            OAuth.__bases__[0], "async_auth_flow", return_value=tracked_gen
        ):
            # Drive the OAuth flow
            flow = oauth.async_auth_flow(httpx.Request("GET", "https://example.com"))
            try:
                # First asend(None) starts the generator per async generator protocol
                await flow.asend(None)  # ty: ignore[invalid-argument-type]
                try:
                    await flow.asend(httpx.Response(200))
                except StopAsyncIteration:
                    pass
            except StopAsyncIteration:
                pass

        assert tracked_gen.aclose_called, (
            "Generator aclose() was not called after flow completion"
        )

    async def test_generator_closed_on_exception(self):
        """Verify aclose() is called even when an exception occurs mid-flow."""
        oauth = OAuth(mcp_url="https://example.com")

        class FailingGenerator:
            def __init__(self):
                self.aclose_called = False
                self._first_call = True

            def __aiter__(self):
                return self

            async def __anext__(self):
                return await self.asend(None)

            async def asend(self, value):
                if self._first_call:
                    self._first_call = False
                    return httpx.Request("GET", "https://example.com")
                raise ValueError("Simulated failure")

            async def athrow(self, exc_type, exc_val=None, exc_tb=None):
                raise StopAsyncIteration

            async def aclose(self):
                self.aclose_called = True

        tracked_gen = FailingGenerator()

        with patch.object(
            OAuth.__bases__[0], "async_auth_flow", return_value=tracked_gen
        ):
            flow = oauth.async_auth_flow(httpx.Request("GET", "https://example.com"))
            with pytest.raises(ValueError, match="Simulated failure"):
                await flow.asend(None)  # ty: ignore[invalid-argument-type]
                await flow.asend(httpx.Response(200))

        assert tracked_gen.aclose_called, (
            "Generator aclose() was not called after exception"
        )


class TestTokenStorageTTL:
    """Tests for client token storage TTL behavior (issue #2670).

    The token storage TTL should NOT be based on access token expiry, because
    the refresh token may be valid much longer. Using access token expiry would
    cause both tokens to be deleted when the access token expires, preventing
    refresh.
    """

    async def test_token_storage_uses_long_ttl(self):
        """Token storage should use a long TTL, not access token expiry.

        This is the ianw case: IdP returns expires_in=300 (5 min access token)
        but the refresh token is valid for much longer. The entire token entry
        should NOT be deleted after 5 minutes.
        """
        from key_value.aio.stores.memory import MemoryStore
        from mcp.shared.auth import OAuthToken

        from fastmcp.client.auth.oauth import TokenStorageAdapter

        # Create storage adapter
        storage = MemoryStore()
        adapter = TokenStorageAdapter(
            async_key_value=storage, server_url="https://test"
        )

        # Create a token with short access expiry (5 minutes)
        token = OAuthToken(
            access_token="test-access-token",
            token_type="Bearer",
            expires_in=300,  # 5 minutes - but we should NOT use this as storage TTL!
            refresh_token="test-refresh-token",
            scope="read write",
        )

        # Store the token
        await adapter.set_tokens(token)

        # Verify token is stored
        stored = await adapter.get_tokens()
        assert stored is not None
        assert stored.access_token == "test-access-token"
        assert stored.refresh_token == "test-refresh-token"

        # The key assertion: the TTL should be 1 year (365 days), not 300 seconds
        # We verify this by checking the raw storage entry
        raw = await storage.get(collection="mcp-oauth-token", key="https://test/tokens")
        assert raw is not None

    async def test_token_storage_preserves_refresh_token(self):
        """Refresh token should not be lost when access token would expire."""
        from key_value.aio.stores.memory import MemoryStore
        from mcp.shared.auth import OAuthToken

        from fastmcp.client.auth.oauth import TokenStorageAdapter

        storage = MemoryStore()
        adapter = TokenStorageAdapter(
            async_key_value=storage, server_url="https://test"
        )

        # Store token with short access expiry
        token = OAuthToken(
            access_token="access",
            token_type="Bearer",
            expires_in=300,
            refresh_token="refresh-token-should-survive",
            scope="read",
        )
        await adapter.set_tokens(token)

        # Retrieve and verify refresh token is present
        stored = await adapter.get_tokens()
        assert stored is not None
        assert stored.refresh_token == "refresh-token-should-survive"
