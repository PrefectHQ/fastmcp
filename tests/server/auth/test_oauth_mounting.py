"""Tests for OAuth .well-known routes when FastMCP apps are mounted in parent ASGI apps.

This test file validates the fix for issue #2077 where .well-known/oauth-protected-resource
returns 404 at root level when a FastMCP app is mounted under a path prefix.

The fix uses MCP SDK 1.17+ which implements RFC 9728 path-scoped well-known URLs.
"""

import httpx
import pytest
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.routing import Mount

from fastmcp import FastMCP
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier


@pytest.fixture
def test_tokens():
    """Standard test tokens fixture."""
    return {
        "test_token": {
            "client_id": "test-client",
            "scopes": ["read", "write"],
        }
    }


class TestOAuthMounting:
    """Test OAuth .well-known routes with mounted FastMCP apps."""

    async def test_well_known_with_direct_deployment(self, test_tokens):
        """Test that .well-known routes work when app is deployed directly (not mounted).

        This is the baseline - it should work as expected.
        Per RFC 9728, if the resource is at /mcp, the well-known endpoint is at
        /.well-known/oauth-protected-resource/mcp (path-scoped).
        """
        token_verifier = StaticTokenVerifier(tokens=test_tokens)
        auth_provider = RemoteAuthProvider(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="https://api.example.com",
        )

        mcp = FastMCP("test-server", auth=auth_provider)
        mcp_app = mcp.http_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_app),
            base_url="https://api.example.com",
        ) as client:
            # RFC 9728: path-scoped well-known URL
            # Resource is at /mcp, so well-known should be at /.well-known/oauth-protected-resource/mcp
            response = await client.get("/.well-known/oauth-protected-resource/mcp")
            assert response.status_code == 200

            data = response.json()
            assert data["resource"] == "https://api.example.com/mcp"
            assert data["authorization_servers"] == ["https://auth.example.com/"]

    async def test_well_known_with_mounted_app(self, test_tokens):
        """Test that .well-known routes work when explicitly mounted at root.

        This test uses the CANONICAL pattern for mounting:
        - base_url includes the mount prefix ("/api")
        - mcp_path is just the internal MCP path ("/mcp")
        - These combine: base_url + mcp_path = actual URL

        The well-known routes are mounted at root level for RFC 9728 compliance.
        """
        token_verifier = StaticTokenVerifier(tokens=test_tokens)
        # CANONICAL PATTERN: base_url includes the mount prefix
        auth_provider = RemoteAuthProvider(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="https://api.example.com/api",  # Includes /api mount prefix
        )

        mcp = FastMCP("test-server", auth=auth_provider)
        mcp_app = mcp.http_app(path="/mcp")

        # Pass just the internal mcp_path, NOT the full mount path
        # The auth provider will combine base_url + mcp_path internally
        well_known_routes = auth_provider.get_well_known_routes(mcp_path="/mcp")

        parent_app = Starlette(
            routes=[
                *well_known_routes,  # Well-known routes at root level
                Mount("/api", app=mcp_app),  # MCP app under /api
            ],
            lifespan=mcp_app.lifespan,
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent_app),
            base_url="https://api.example.com",
        ) as client:
            # The CORRECT RFC 9728 path-scoped well-known URL at root
            # Resource is at /api/mcp, so well-known is at /.well-known/oauth-protected-resource/api/mcp
            response = await client.get("/.well-known/oauth-protected-resource/api/mcp")
            assert response.status_code == 200

            data = response.json()
            assert data["resource"] == "https://api.example.com/api/mcp"
            assert data["authorization_servers"] == ["https://auth.example.com/"]

            # There will also be an extra route at /api/.well-known/oauth-protected-resource/mcp
            # (from the mounted MCP app), but we don't care about that as long as the correct one exists

    async def test_mcp_endpoint_with_mounted_app(self, test_tokens):
        """Test that MCP endpoint works correctly when mounted.

        This confirms the MCP functionality itself works with mounting.
        """
        token_verifier = StaticTokenVerifier(tokens=test_tokens)
        auth_provider = RemoteAuthProvider(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="https://api.example.com",
        )

        mcp = FastMCP("test-server", auth=auth_provider)

        @mcp.tool
        def test_tool(message: str) -> str:
            return f"Echo: {message}"

        mcp_app = mcp.http_app(path="/mcp")

        # Mount the MCP app under /api prefix
        parent_app = Starlette(
            routes=[
                Mount("/api", app=mcp_app),
            ],
            lifespan=mcp_app.lifespan,
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent_app),
            base_url="https://api.example.com",
        ) as client:
            # The MCP endpoint should work at /api/mcp (mounted correctly)
            # This is a basic connectivity test
            response = await client.get("/api/mcp")

            # We expect either 200 (if no auth required for GET) or 401 (if auth required)
            # The key is that it's NOT 404
            assert response.status_code in [200, 401, 405]

    async def test_nested_mounting(self, test_tokens):
        """Test .well-known routes with deeply nested mounts.

        Uses CANONICAL pattern: base_url includes all mount prefixes.
        """
        token_verifier = StaticTokenVerifier(tokens=test_tokens)
        # CANONICAL PATTERN: base_url includes full mount path /outer/inner
        auth_provider = RemoteAuthProvider(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="https://api.example.com/outer/inner",  # Includes nested mount path
        )

        mcp = FastMCP("test-server", auth=auth_provider)
        mcp_app = mcp.http_app(path="/mcp")

        # Pass just the internal mcp_path
        well_known_routes = auth_provider.get_well_known_routes(mcp_path="/mcp")

        # Create nested mounts
        inner_app = Starlette(
            routes=[Mount("/inner", app=mcp_app)],
        )
        outer_app = Starlette(
            routes=[
                *well_known_routes,  # Well-known routes at root level
                Mount("/outer", app=inner_app),
            ],
            lifespan=mcp_app.lifespan,
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=outer_app),
            base_url="https://api.example.com",
        ) as client:
            # RFC 9728: path-scoped well-known URL for nested mounting
            # Resource is at /outer/inner/mcp, so well-known is at /.well-known/oauth-protected-resource/outer/inner/mcp
            response = await client.get(
                "/.well-known/oauth-protected-resource/outer/inner/mcp"
            )
            assert response.status_code == 200

            data = response.json()
            assert data["resource"] == "https://api.example.com/outer/inner/mcp"

    async def test_oauth_authorization_server_metadata_with_base_url_and_issuer_url(
        self, test_tokens
    ):
        """Test OAuth authorization server metadata when base_url and issuer_url differ.

        This validates the fix for issue #2287 where operational OAuth endpoints
        (/authorize, /token) should be declared at base_url in the metadata,
        not at issuer_url.

        Scenario: FastMCP server mounted at /api prefix
        - issuer_url: https://api.example.com (root level)
        - base_url: https://api.example.com/api (includes mount prefix)
        - Expected: metadata declares endpoints at base_url
        """
        # Create OAuth proxy with different base_url and issuer_url
        token_verifier = StaticTokenVerifier(tokens=test_tokens)
        auth_provider = OAuthProxy(
            upstream_authorization_endpoint="https://upstream.example.com/authorize",
            upstream_token_endpoint="https://upstream.example.com/token",
            upstream_client_id="test-client-id",
            upstream_client_secret="test-client-secret",
            token_verifier=token_verifier,
            base_url="https://api.example.com/api",  # Includes mount prefix
            issuer_url="https://api.example.com",  # Root level
        )

        mcp = FastMCP("test-server", auth=auth_provider)
        mcp_app = mcp.http_app(path="/mcp")

        # Get well-known routes for mounting at root
        well_known_routes = auth_provider.get_well_known_routes(mcp_path="/mcp")

        # Mount the app under /api prefix
        parent_app = Starlette(
            routes=[
                *well_known_routes,  # Well-known routes at root level
                Mount("/api", app=mcp_app),  # MCP app under /api
            ],
            lifespan=mcp_app.lifespan,
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent_app),
            base_url="https://api.example.com",
        ) as client:
            # Fetch the authorization server metadata
            response = await client.get("/.well-known/oauth-authorization-server")
            assert response.status_code == 200

            metadata = response.json()

            # CRITICAL: The metadata should declare endpoints at base_url,
            # not issuer_url, because that's where they're actually mounted
            assert (
                metadata["authorization_endpoint"]
                == "https://api.example.com/api/authorize"
            )
            assert metadata["token_endpoint"] == "https://api.example.com/api/token"
            assert (
                metadata["registration_endpoint"]
                == "https://api.example.com/api/register"
            )

            # The issuer field should use base_url (where the server is actually running)
            # Note: MCP SDK may or may not add a trailing slash
            assert metadata["issuer"] in [
                "https://api.example.com/api",
                "https://api.example.com/api/",
            ]

    async def test_oauth_authorization_server_metadata_path_aware_discovery(
        self, test_tokens
    ):
        """Test RFC 8414 path-aware discovery when issuer_url has a path.

        This validates the fix for issue #2527 where authorization server metadata
        should be exposed at a path-aware URL when issuer_url has a path component.

        When issuer_url defaults to base_url (e.g., http://example.com/api), the
        authorization server metadata should be at:
        /.well-known/oauth-authorization-server/api

        This is consistent with how protected resource metadata already works
        (RFC 9728) and complies with RFC 8414 path-aware discovery.
        """
        # Create OAuth proxy where issuer_url defaults to base_url (which has a path)
        token_verifier = StaticTokenVerifier(tokens=test_tokens)
        auth_provider = OAuthProxy(
            upstream_authorization_endpoint="https://upstream.example.com/authorize",
            upstream_token_endpoint="https://upstream.example.com/token",
            upstream_client_id="test-client-id",
            upstream_client_secret="test-client-secret",
            token_verifier=token_verifier,
            base_url="https://api.example.com/api",  # Has path, no explicit issuer_url
        )

        mcp = FastMCP("test-server", auth=auth_provider)
        mcp_app = mcp.http_app(path="/mcp")

        # Get well-known routes - should include path-aware authorization server metadata
        well_known_routes = auth_provider.get_well_known_routes(mcp_path="/mcp")

        # Find the authorization server metadata route
        auth_server_routes = [
            r for r in well_known_routes if "oauth-authorization-server" in r.path
        ]
        assert len(auth_server_routes) == 1

        # The route should be path-aware (RFC 8414)
        assert (
            auth_server_routes[0].path == "/.well-known/oauth-authorization-server/api"
        )

        # Find the protected resource metadata route for comparison
        protected_resource_routes = [
            r for r in well_known_routes if "oauth-protected-resource" in r.path
        ]
        assert len(protected_resource_routes) == 1
        # Protected resource should also be path-aware (RFC 9728)
        assert (
            protected_resource_routes[0].path
            == "/.well-known/oauth-protected-resource/api/mcp"
        )

        # Mount the app and verify the routes are accessible
        parent_app = Starlette(
            routes=[
                *well_known_routes,
                Mount("/api", app=mcp_app),
            ],
            lifespan=mcp_app.lifespan,
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent_app),
            base_url="https://api.example.com",
        ) as client:
            # Path-aware authorization server metadata should be accessible
            response = await client.get("/.well-known/oauth-authorization-server/api")
            assert response.status_code == 200

            metadata = response.json()
            assert (
                metadata["authorization_endpoint"]
                == "https://api.example.com/api/authorize"
            )
            assert metadata["token_endpoint"] == "https://api.example.com/api/token"

            # Path-aware protected resource metadata should also work
            response = await client.get("/.well-known/oauth-protected-resource/api/mcp")
            assert response.status_code == 200

    async def test_oauth_authorization_server_metadata_root_issuer(self, test_tokens):
        """Test that root-level issuer_url still uses root discovery path.

        When issuer_url is explicitly set to root (no path), the authorization
        server metadata should remain at the root path:
        /.well-known/oauth-authorization-server

        This maintains backwards compatibility with the documented mounting pattern.
        """
        token_verifier = StaticTokenVerifier(tokens=test_tokens)
        auth_provider = OAuthProxy(
            upstream_authorization_endpoint="https://upstream.example.com/authorize",
            upstream_token_endpoint="https://upstream.example.com/token",
            upstream_client_id="test-client-id",
            upstream_client_secret="test-client-secret",
            token_verifier=token_verifier,
            base_url="https://api.example.com/api",
            issuer_url="https://api.example.com",  # Explicitly root
        )

        well_known_routes = auth_provider.get_well_known_routes(mcp_path="/mcp")

        # Find the authorization server metadata route
        auth_server_routes = [
            r for r in well_known_routes if "oauth-authorization-server" in r.path
        ]
        assert len(auth_server_routes) == 1

        # Should be at root (no path suffix) when issuer_url is root
        assert auth_server_routes[0].path == "/.well-known/oauth-authorization-server"
