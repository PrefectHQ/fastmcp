import httpx
import pytest
from pydantic import AnyHttpUrl

from fastmcp import FastMCP
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier


@pytest.fixture
def test_tokens():
    """Standard test tokens fixture for all auth tests."""
    return {
        "test_token": {
            "client_id": "test-client",
            "scopes": ["read", "write"],
        }
    }


class TestRemoteAuthProvider:
    """Test suite for RemoteAuthProvider."""

    def test_init(self, test_tokens):
        """Test RemoteAuthProvider initialization."""
        token_verifier = StaticTokenVerifier(tokens=test_tokens)
        auth_servers = [AnyHttpUrl("https://auth.example.com")]

        provider = RemoteAuthProvider(
            token_verifier=token_verifier,
            authorization_servers=auth_servers,
            base_url="https://api.example.com",
        )

        assert provider.token_verifier is token_verifier
        assert provider.authorization_servers == auth_servers
        assert provider.base_url == AnyHttpUrl("https://api.example.com/")

    async def test_verify_token_delegates_to_verifier(self, test_tokens):
        """Test that verify_token delegates to the token verifier."""
        # Use a different token for this specific test
        tokens = {
            "valid_token": {
                "client_id": "test-client",
                "scopes": [],
            }
        }
        token_verifier = StaticTokenVerifier(tokens=tokens)

        provider = RemoteAuthProvider(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="https://api.example.com",
        )

        # Valid token
        result = await provider.verify_token("valid_token")
        assert result is not None
        assert result.token == "valid_token"
        assert result.client_id == "test-client"

        # Invalid token
        result = await provider.verify_token("invalid_token")
        assert result is None

    def test_get_routes_creates_protected_resource_routes(self, test_tokens):
        """Test that get_routes creates protected resource routes."""
        token_verifier = StaticTokenVerifier(tokens=test_tokens)
        auth_servers = [AnyHttpUrl("https://auth.example.com")]

        provider = RemoteAuthProvider(
            token_verifier=token_verifier,
            authorization_servers=auth_servers,
            base_url="https://api.example.com",
        )

        routes = provider.get_routes()
        assert len(routes) == 1

        # Check that the route is the OAuth protected resource metadata endpoint
        # When called without mcp_path, it creates route at /.well-known/oauth-protected-resource
        route = routes[0]
        assert route.path == "/.well-known/oauth-protected-resource"
        assert route.methods is not None
        assert "GET" in route.methods

    def test_get_resource_url_with_well_known_path(self):
        """Test _get_resource_url returns correct URL for .well-known path."""
        tokens = {
            "test_token": {
                "client_id": "test-client",
                "scopes": ["read"],
            }
        }
        token_verifier = StaticTokenVerifier(tokens=tokens)
        provider = RemoteAuthProvider(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="https://api.example.com",
        )

        metadata_url = provider._get_resource_url(
            "/.well-known/oauth-protected-resource/mcp"
        )
        assert metadata_url == AnyHttpUrl(
            "https://api.example.com/.well-known/oauth-protected-resource/mcp"
        )

    def test_get_resource_url_with_nested_base_url(self):
        """Test _get_resource_url returns correct URL for .well-known path with nested base_url."""
        tokens = {
            "test_token": {
                "client_id": "test-client",
                "scopes": ["read"],
            }
        }
        token_verifier = StaticTokenVerifier(tokens=tokens)
        provider = RemoteAuthProvider(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="https://api.example.com/v1/",
        )

        metadata_url = provider._get_resource_url(
            "/.well-known/oauth-protected-resource/mcp"
        )
        assert metadata_url == AnyHttpUrl(
            "https://api.example.com/v1/.well-known/oauth-protected-resource/mcp"
        )

    def test_get_resource_url_handles_trailing_slash(self):
        """Test _get_resource_url handles trailing slash correctly."""
        tokens = {
            "test_token": {
                "client_id": "test-client",
                "scopes": ["read"],
            }
        }
        token_verifier = StaticTokenVerifier(tokens=tokens)
        provider = RemoteAuthProvider(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="https://api.example.com/",
        )

        metadata_url = provider._get_resource_url(
            "/.well-known/oauth-protected-resource/mcp"
        )
        assert metadata_url == AnyHttpUrl(
            "https://api.example.com/.well-known/oauth-protected-resource/mcp"
        )


class TestRemoteAuthProviderIntegration:
    """Integration tests for RemoteAuthProvider with FastMCP server."""

    @pytest.fixture
    def basic_auth_provider(self, test_tokens):
        """Basic RemoteAuthProvider fixture for testing."""
        token_verifier = StaticTokenVerifier(tokens=test_tokens)
        return RemoteAuthProvider(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="https://api.example.com",
        )

    def _create_test_auth_provider(
        self, base_url="https://api.example.com", test_tokens=None, **kwargs
    ):
        """Helper to create a test RemoteAuthProvider with StaticTokenVerifier."""
        tokens = kwargs.get(
            "tokens",
            test_tokens
            or {
                "test_token": {
                    "client_id": "test-client",
                    "scopes": ["read", "write"],
                }
            },
        )
        token_verifier = StaticTokenVerifier(tokens=tokens)
        return RemoteAuthProvider(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url=base_url,
        )

    async def test_protected_resource_metadata_endpoint_status_code(
        self, basic_auth_provider
    ):
        """Test that the protected resource metadata endpoint returns 200."""
        mcp = FastMCP("test-server", auth=basic_auth_provider)
        mcp_http_app = mcp.http_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_http_app),
            base_url="https://api.example.com",
        ) as client:
            # The metadata URL is path-aware per RFC 9728
            response = await client.get("/.well-known/oauth-protected-resource/mcp")
            assert response.status_code == 200

    async def test_protected_resource_metadata_endpoint_resource_field(self):
        """Test that the protected resource metadata endpoint returns correct resource field."""
        auth_provider = self._create_test_auth_provider()

        mcp = FastMCP("test-server", auth=auth_provider)
        mcp_http_app = mcp.http_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_http_app),
            base_url="https://api.example.com",
        ) as client:
            # The metadata URL is path-aware per RFC 9728
            response = await client.get("/.well-known/oauth-protected-resource/mcp")
            data = response.json()

            # This is the key test - ensure resource field contains the full MCP URL
            assert data["resource"] == "https://api.example.com/mcp"

    async def test_protected_resource_metadata_endpoint_authorization_servers_field(
        self,
    ):
        """Test that the protected resource metadata endpoint returns correct authorization_servers field."""
        auth_provider = self._create_test_auth_provider()

        mcp = FastMCP("test-server", auth=auth_provider)
        mcp_http_app = mcp.http_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_http_app),
            base_url="https://api.example.com",
        ) as client:
            # The metadata URL is path-aware per RFC 9728
            response = await client.get("/.well-known/oauth-protected-resource/mcp")
            data = response.json()

            assert data["authorization_servers"] == ["https://auth.example.com/"]

    @pytest.mark.parametrize(
        "base_url,expected_resource",
        [
            ("https://api.example.com", "https://api.example.com/mcp"),
            ("https://api.example.com/", "https://api.example.com/mcp"),
            ("https://api.example.com/v1/", "https://api.example.com/v1/mcp"),
        ],
    )
    async def test_base_url_configurations(self, base_url: str, expected_resource: str):
        """Test different base_url configurations."""
        from urllib.parse import urlparse

        auth_provider = self._create_test_auth_provider(base_url=base_url)
        mcp = FastMCP("test-server", auth=auth_provider)
        mcp_http_app = mcp.http_app()

        # Extract the path from the expected resource to construct metadata URL
        resource_parsed = urlparse(expected_resource)
        # Remove leading slash if present to avoid double slashes
        resource_path = resource_parsed.path.lstrip("/")
        metadata_path = f"/.well-known/oauth-protected-resource/{resource_path}"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_http_app),
            base_url="https://test.example.com",
        ) as client:
            # The metadata URL is path-aware per RFC 9728
            response = await client.get(metadata_path)

            assert response.status_code == 200
            data = response.json()
            assert data["resource"] == expected_resource

    async def test_multiple_authorization_servers_resource_field(self):
        """Test resource field with multiple authorization servers."""
        auth_servers = [
            AnyHttpUrl("https://auth1.example.com"),
            AnyHttpUrl("https://auth2.example.com"),
        ]

        auth_provider = self._create_test_auth_provider()
        # Override the authorization servers
        auth_provider.authorization_servers = auth_servers

        mcp = FastMCP("test-server", auth=auth_provider)
        mcp_http_app = mcp.http_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_http_app),
            base_url="https://api.example.com",
        ) as client:
            # The metadata URL is path-aware per RFC 9728
            response = await client.get("/.well-known/oauth-protected-resource/mcp")

            data = response.json()
            assert data["resource"] == "https://api.example.com/mcp"

    async def test_multiple_authorization_servers_list(self):
        """Test authorization_servers field with multiple authorization servers."""
        auth_servers = [
            AnyHttpUrl("https://auth1.example.com"),
            AnyHttpUrl("https://auth2.example.com"),
        ]

        auth_provider = self._create_test_auth_provider()
        # Override the authorization servers
        auth_provider.authorization_servers = auth_servers

        mcp = FastMCP("test-server", auth=auth_provider)
        mcp_http_app = mcp.http_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_http_app),
            base_url="https://api.example.com",
        ) as client:
            # The metadata URL is path-aware per RFC 9728
            response = await client.get("/.well-known/oauth-protected-resource/mcp")

            data = response.json()
            assert set(data["authorization_servers"]) == {
                "https://auth1.example.com/",
                "https://auth2.example.com/",
            }

    async def test_token_verification_with_valid_auth_succeeds(self):
        """Test that requests with valid auth token succeed."""
        # Note: This test focuses on HTTP-level authentication behavior
        # For the RemoteAuthProvider, the key test is that the OAuth discovery
        # endpoint correctly reports the resource server URL, which is tested above

        # This is primarily testing that the token verifier integration works
        tokens = {
            "valid_token": {
                "client_id": "test-client",
                "scopes": [],
            }
        }
        token_verifier = StaticTokenVerifier(tokens=tokens)

        provider = RemoteAuthProvider(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="https://api.example.com",
        )

        # Test that the provider correctly delegates to the token verifier
        result = await provider.verify_token("valid_token")
        assert result is not None
        assert result.token == "valid_token"
        assert result.client_id == "test-client"

        result = await provider.verify_token("invalid_token")
        assert result is None

    async def test_token_verification_with_invalid_auth_fails(self):
        """Test that the provider correctly rejects invalid tokens."""
        tokens = {
            "valid_token": {
                "client_id": "test-client",
                "scopes": [],
            }
        }
        token_verifier = StaticTokenVerifier(tokens=tokens)

        provider = RemoteAuthProvider(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="https://api.example.com",
        )

        # Test that invalid tokens are rejected
        result = await provider.verify_token("invalid_token")
        assert result is None

    async def test_issue_1348_oauth_discovery_returns_correct_url(self):
        """Test that RemoteAuthProvider correctly returns the full MCP endpoint URL.

        This test confirms that RemoteAuthProvider works correctly and returns
        the resource URL with the MCP path appended to the base URL.
        """
        tokens = {
            "test_token": {
                "client_id": "test-client",
                "scopes": ["read"],
            }
        }
        token_verifier = StaticTokenVerifier(tokens=tokens)
        auth_provider = RemoteAuthProvider(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl("https://accounts.google.com")],
            base_url="https://my-server.com",
        )

        mcp = FastMCP("test-server", auth=auth_provider)
        mcp_http_app = mcp.http_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_http_app),
            base_url="https://my-server.com",
        ) as client:
            # The metadata URL is path-aware per RFC 9728
            response = await client.get("/.well-known/oauth-protected-resource/mcp")

            assert response.status_code == 200
            data = response.json()

            # The RemoteAuthProvider correctly returns the full MCP endpoint URL
            assert data["resource"] == "https://my-server.com/mcp"
            assert data["authorization_servers"] == ["https://accounts.google.com/"]

    async def test_resource_name_field(self):
        """Test that RemoteAuthProvider correctly returns the resource_name.

        This test confirms that RemoteAuthProvider works correctly and returns
        the exact resource_name specified.
        """
        tokens = {
            "test_token": {
                "client_id": "test-client",
                "scopes": ["read"],
            }
        }
        token_verifier = StaticTokenVerifier(tokens=tokens)
        auth_provider = RemoteAuthProvider(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl("https://accounts.google.com")],
            base_url="https://my-server.com",
            resource_name="My Test Resource",
        )

        mcp = FastMCP("test-server", auth=auth_provider)
        mcp_http_app = mcp.http_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_http_app),
            base_url="https://my-server.com",
        ) as client:
            # The metadata URL is path-aware per RFC 9728
            response = await client.get("/.well-known/oauth-protected-resource/mcp")

            assert response.status_code == 200
            data = response.json()

            # The RemoteAuthProvider correctly returns the resource_name
            assert data["resource_name"] == "My Test Resource"

    async def test_resource_documentation_field(self):
        """Test that RemoteAuthProvider correctly returns the resource_documentation.

        This test confirms that RemoteAuthProvider works correctly and returns
        the exact resource_documentation specified.
        """
        tokens = {
            "test_token": {
                "client_id": "test-client",
                "scopes": ["read"],
            }
        }
        token_verifier = StaticTokenVerifier(tokens=tokens)
        auth_provider = RemoteAuthProvider(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl("https://accounts.google.com")],
            base_url="https://my-server.com",
            resource_documentation=AnyHttpUrl(
                "https://doc.my-server.com/resource-docs"
            ),
        )

        mcp = FastMCP("test-server", auth=auth_provider)
        mcp_http_app = mcp.http_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_http_app),
            base_url="https://my-server.com",
        ) as client:
            # The metadata URL is path-aware per RFC 9728
            response = await client.get("/.well-known/oauth-protected-resource/mcp")

            assert response.status_code == 200
            data = response.json()

            # The RemoteAuthProvider correctly returns the resource_documentation
            assert (
                data["resource_documentation"]
                == "https://doc.my-server.com/resource-docs"
            )
