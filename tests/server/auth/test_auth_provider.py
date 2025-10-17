import re

import httpx
import pytest
from pydantic import AnyHttpUrl

from fastmcp import FastMCP
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier


class TestAuthProviderBase:
    """Test suite for base AuthProvider behaviors that apply to all auth providers."""

    @pytest.fixture
    def basic_remote_provider(self):
        """Basic RemoteAuthProvider fixture for testing base AuthProvider behaviors."""
        # Create a static token verifier with a test token
        tokens = {
            "test_token": {
                "client_id": "test-client",
                "scopes": ["read", "write"],
            }
        }
        token_verifier = StaticTokenVerifier(tokens=tokens)
        return RemoteAuthProvider(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="https://my-server.com",
        )

    async def test_www_authenticate_header_points_to_base_url(
        self, basic_remote_provider
    ):
        """Test that WWW-Authenticate header points to RFC 9728-compliant metadata URL.

        The WWW-Authenticate header includes the resource path per RFC 9728,
        so clients can discover where the metadata is actually registered.
        """
        mcp = FastMCP("test-server", auth=basic_remote_provider)
        # Mount MCP at a non-root path
        mcp_http_app = mcp.http_app(path="/api/v1/mcp")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_http_app),
            base_url="https://my-server.com",
        ) as client:
            # Make unauthorized request to MCP endpoint
            response = await client.get("/api/v1/mcp")
            assert response.status_code == 401

            www_auth = response.headers.get("www-authenticate", "")
            assert "resource_metadata=" in www_auth

            # Extract the metadata URL from the header
            match = re.search(r'resource_metadata="([^"]+)"', www_auth)
            assert match is not None
            metadata_url = match.group(1)

            # The metadata URL includes the resource path per RFC 9728
            assert (
                metadata_url
                == "https://my-server.com/.well-known/oauth-protected-resource/api/v1/mcp"
            )

    async def test_automatic_resource_url_capture(self, basic_remote_provider):
        """Test that resource URL is automatically captured from MCP path.

        This test verifies PR #1682 functionality where the resource URL
        should be automatically set based on the MCP endpoint path.
        """
        mcp = FastMCP("test-server", auth=basic_remote_provider)
        # Mount MCP at a specific path
        mcp_http_app = mcp.http_app(path="/mcp")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_http_app),
            base_url="https://my-server.com",
        ) as client:
            # The .well-known metadata is at a path-aware location per RFC 9728
            response = await client.get("/.well-known/oauth-protected-resource/mcp")
            assert response.status_code == 200

            data = response.json()
            # The resource URL should be automatically set to the MCP path
            assert data.get("resource") == "https://my-server.com/mcp"

    async def test_automatic_resource_url_with_nested_path(self, basic_remote_provider):
        """Test automatic resource URL capture with deeply nested MCP path."""
        mcp = FastMCP("test-server", auth=basic_remote_provider)
        mcp_http_app = mcp.http_app(path="/api/v2/services/mcp")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_http_app),
            base_url="https://my-server.com",
        ) as client:
            # The .well-known metadata includes the resource path per RFC 9728
            response = await client.get(
                "/.well-known/oauth-protected-resource/api/v2/services/mcp"
            )
            assert response.status_code == 200

            data = response.json()
            # Should automatically capture the nested path
            assert data.get("resource") == "https://my-server.com/api/v2/services/mcp"
