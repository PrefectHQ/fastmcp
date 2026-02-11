"""Tests for OAuth static client registration (pre-registered client_id/client_secret)."""

from unittest.mock import patch

import httpx
import pytest
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from fastmcp.client import Client
from fastmcp.client.auth import OAuth
from fastmcp.client.auth.oauth import ClientNotFoundError
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.auth.auth import ClientRegistrationOptions
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from fastmcp.server.server import FastMCP
from fastmcp.utilities.http import find_available_port
from fastmcp.utilities.tests import HeadlessOAuth, run_server_async


class TestStaticClientInfoConstruction:
    """Static client info should include full metadata from client_metadata."""

    def test_static_client_info_includes_metadata(self):
        """Static client info should include redirect_uris, grant_types, etc."""
        oauth = OAuth(
            mcp_url="https://example.com/mcp",
            client_id="my-client-id",
            client_secret="my-secret",
            scopes=["read", "write"],
        )

        info = oauth._static_client_info
        assert info is not None
        assert info.client_id == "my-client-id"
        assert info.client_secret == "my-secret"
        # Metadata fields should be populated from client_metadata
        assert info.redirect_uris is not None
        assert len(info.redirect_uris) == 1
        assert info.grant_types is not None
        assert "authorization_code" in info.grant_types
        assert "refresh_token" in info.grant_types
        assert info.response_types is not None
        assert "code" in info.response_types
        assert info.scope == "read write"
        assert info.token_endpoint_auth_method == "client_secret_post"

    def test_static_client_info_without_secret(self):
        """Public clients can provide client_id without client_secret."""
        oauth = OAuth(
            mcp_url="https://example.com/mcp",
            client_id="public-client",
        )

        info = oauth._static_client_info
        assert info is not None
        assert info.client_id == "public-client"
        assert info.client_secret is None
        assert info.token_endpoint_auth_method == "none"
        # Metadata should still be present
        assert info.redirect_uris is not None
        assert info.grant_types is not None

    def test_no_static_client_info_without_client_id(self):
        """When no client_id is provided, _static_client_info should be None."""
        oauth = OAuth(mcp_url="https://example.com/mcp")
        assert oauth._static_client_info is None

    def test_static_client_info_includes_additional_metadata(self):
        """Additional client metadata should be included in static client info."""
        oauth = OAuth(
            mcp_url="https://example.com/mcp",
            client_id="my-client",
            additional_client_metadata={
                "token_endpoint_auth_method": "client_secret_post"
            },
        )

        info = oauth._static_client_info
        assert info is not None
        assert info.token_endpoint_auth_method == "client_secret_post"


class TestStaticClientInitialize:
    """_initialize should set context.client_info and persist to storage."""

    async def test_initialize_sets_context_client_info(self):
        """_initialize should inject static client info into the auth context."""
        oauth = OAuth(
            mcp_url="https://example.com/mcp",
            client_id="my-client",
            client_secret="my-secret",
        )

        # Mock the parent _initialize since it needs a real server
        with patch.object(OAuth.__bases__[0], "_initialize", return_value=None):
            await oauth._initialize()

        assert oauth.context.client_info is not None
        assert oauth.context.client_info.client_id == "my-client"
        assert oauth.context.client_info.client_secret == "my-secret"

    async def test_initialize_persists_static_client_to_storage(self):
        """Static client info should be persisted to token storage."""
        oauth = OAuth(
            mcp_url="https://example.com/mcp",
            client_id="my-client",
            client_secret="my-secret",
        )

        with patch.object(OAuth.__bases__[0], "_initialize", return_value=None):
            await oauth._initialize()

        # Verify it was persisted to storage
        stored = await oauth.token_storage_adapter.get_client_info()
        assert stored is not None
        assert stored.client_id == "my-client"

    async def test_initialize_without_static_creds_works(self):
        """_initialize should not error when no static credentials are provided."""
        oauth = OAuth(mcp_url="https://example.com/mcp")

        with patch.object(OAuth.__bases__[0], "_initialize", return_value=None):
            # This should not raise AttributeError
            await oauth._initialize()

        # context.client_info should be whatever the parent set (None by default)


class TestStaticClientRetryBehavior:
    """Retry-on-stale-credentials should short-circuit for static creds."""

    async def test_retry_skipped_with_static_creds(self):
        """When static creds are rejected, should raise immediately, not retry."""
        oauth = OAuth(
            mcp_url="https://example.com/mcp",
            client_id="bad-client-id",
            client_secret="bad-secret",
        )

        # Make the parent auth flow raise ClientNotFoundError
        async def failing_auth_flow(request):
            raise ClientNotFoundError("client not found")
            yield  # make it a generator  # noqa: E275

        with patch.object(
            OAuth.__bases__[0], "async_auth_flow", side_effect=failing_auth_flow
        ):
            flow = oauth.async_auth_flow(httpx.Request("GET", "https://example.com"))
            with pytest.raises(ClientNotFoundError, match="static client credentials"):
                await flow.__anext__()

    async def test_retry_still_works_without_static_creds(self):
        """Without static creds, the retry behavior should be preserved."""
        oauth = OAuth(mcp_url="https://example.com/mcp")

        call_count = 0

        async def auth_flow_with_retry(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ClientNotFoundError("client not found")
            # Second attempt succeeds
            yield httpx.Request("GET", "https://example.com")

        with patch.object(
            OAuth.__bases__[0], "async_auth_flow", side_effect=auth_flow_with_retry
        ):
            flow = oauth.async_auth_flow(httpx.Request("GET", "https://example.com"))
            request = await flow.__anext__()
            assert request is not None
            assert call_count == 2


class TestStaticClientE2E:
    """End-to-end tests with a real OAuth server using pre-registered clients."""

    async def test_static_client_with_dcr_disabled(self):
        """Static client_id should work when the server has DCR disabled."""
        port = find_available_port()
        callback_port = find_available_port()
        issuer_url = f"http://127.0.0.1:{port}"

        provider = InMemoryOAuthProvider(
            base_url=issuer_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=False,  # DCR disabled
                valid_scopes=["read", "write"],
            ),
        )

        server = FastMCP("TestServer", auth=provider)

        @server.tool
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        # Pre-register a client directly in the provider.
        # The redirect_uri must match what the OAuth client will use.
        pre_registered = OAuthClientInformationFull(
            client_id="pre-registered-client",
            client_secret="pre-registered-secret",
            redirect_uris=[AnyUrl(f"http://localhost:{callback_port}/callback")],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="client_secret_post",
            scope="read write",
        )
        await provider.register_client(pre_registered)

        async with run_server_async(server, port=port, transport="http") as url:
            oauth = HeadlessOAuth(
                mcp_url=url,
                client_id="pre-registered-client",
                client_secret="pre-registered-secret",
                scopes=["read", "write"],
                callback_port=callback_port,
            )

            async with Client(
                transport=StreamableHttpTransport(url),
                auth=oauth,
            ) as client:
                assert await client.ping()
                tools = await client.list_tools()
                assert any(t.name == "greet" for t in tools)

    async def test_static_client_with_dcr_enabled(self):
        """Static client_id should also work when DCR is enabled (skips DCR)."""
        port = find_available_port()
        callback_port = find_available_port()
        issuer_url = f"http://127.0.0.1:{port}"

        provider = InMemoryOAuthProvider(
            base_url=issuer_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=["read"],
            ),
        )

        server = FastMCP("TestServer", auth=provider)

        @server.tool
        def add(a: int, b: int) -> int:
            return a + b

        pre_registered = OAuthClientInformationFull(
            client_id="my-app",
            client_secret="my-secret",
            redirect_uris=[AnyUrl(f"http://localhost:{callback_port}/callback")],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="client_secret_post",
            scope="read",
        )
        await provider.register_client(pre_registered)

        async with run_server_async(server, port=port, transport="http") as url:
            oauth = HeadlessOAuth(
                mcp_url=url,
                client_id="my-app",
                client_secret="my-secret",
                scopes=["read"],
                callback_port=callback_port,
            )

            async with Client(
                transport=StreamableHttpTransport(url),
                auth=oauth,
            ) as client:
                result = await client.call_tool("add", {"a": 3, "b": 4})
                assert result.data == 7
