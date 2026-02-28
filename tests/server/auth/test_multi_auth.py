import httpx
import pytest
from pydantic import AnyHttpUrl

from fastmcp import FastMCP
from fastmcp.server.auth import MultiAuth, RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier


class TestMultiAuthInit:
    """Test MultiAuth initialization and validation."""

    def test_requires_server_or_verifiers(self):
        """MultiAuth with neither server nor verifiers raises ValueError."""
        with pytest.raises(ValueError, match="at least a server or one verifier"):
            MultiAuth()

    def test_server_only(self):
        verifier = StaticTokenVerifier(tokens={"t": {"client_id": "c", "scopes": []}})
        provider = RemoteAuthProvider(
            token_verifier=verifier,
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="https://api.example.com",
        )
        auth = MultiAuth(server=provider)
        assert auth.server is provider
        assert auth.verifiers == []

    def test_verifiers_only(self):
        v = StaticTokenVerifier(tokens={"t": {"client_id": "c", "scopes": []}})
        auth = MultiAuth(verifiers=[v])
        assert auth.server is None
        assert auth.verifiers == [v]

    def test_single_verifier_not_in_list(self):
        """A single TokenVerifier (not in a list) is accepted."""
        v = StaticTokenVerifier(tokens={"t": {"client_id": "c", "scopes": []}})
        auth = MultiAuth(verifiers=v)
        assert auth.verifiers == [v]

    def test_base_url_from_server(self):
        verifier = StaticTokenVerifier(tokens={"t": {"client_id": "c", "scopes": []}})
        provider = RemoteAuthProvider(
            token_verifier=verifier,
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="https://api.example.com",
        )
        auth = MultiAuth(server=provider)
        assert auth.base_url == AnyHttpUrl("https://api.example.com/")

    def test_base_url_override(self):
        verifier = StaticTokenVerifier(tokens={"t": {"client_id": "c", "scopes": []}})
        provider = RemoteAuthProvider(
            token_verifier=verifier,
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="https://api.example.com",
        )
        auth = MultiAuth(server=provider, base_url="https://override.example.com")
        assert auth.base_url == AnyHttpUrl("https://override.example.com/")

    def test_required_scopes_from_server(self):
        verifier = StaticTokenVerifier(
            tokens={"t": {"client_id": "c", "scopes": ["read"]}},
            required_scopes=["read"],
        )
        provider = RemoteAuthProvider(
            token_verifier=verifier,
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="https://api.example.com",
        )
        auth = MultiAuth(server=provider)
        assert auth.required_scopes == ["read"]


class TestMultiAuthVerifyToken:
    """Test MultiAuth token verification chain."""

    async def test_server_verified_first(self):
        """Server's verify_token is tried before verifiers."""
        server_verifier = StaticTokenVerifier(
            tokens={"server_token": {"client_id": "server-client", "scopes": []}}
        )
        server = RemoteAuthProvider(
            token_verifier=server_verifier,
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="https://api.example.com",
        )
        extra = StaticTokenVerifier(
            tokens={"extra_token": {"client_id": "extra-client", "scopes": []}}
        )

        auth = MultiAuth(server=server, verifiers=[extra])

        result = await auth.verify_token("server_token")
        assert result is not None
        assert result.client_id == "server-client"

    async def test_falls_back_to_verifiers(self):
        """When server rejects a token, verifiers are tried."""
        server_verifier = StaticTokenVerifier(
            tokens={"server_token": {"client_id": "server-client", "scopes": []}}
        )
        server = RemoteAuthProvider(
            token_verifier=server_verifier,
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="https://api.example.com",
        )
        extra = StaticTokenVerifier(
            tokens={"m2m_token": {"client_id": "m2m-service", "scopes": []}}
        )

        auth = MultiAuth(server=server, verifiers=[extra])

        result = await auth.verify_token("m2m_token")
        assert result is not None
        assert result.client_id == "m2m-service"

    async def test_verifier_order_matters(self):
        """Verifiers are tried in order; first match wins."""
        v1 = StaticTokenVerifier(
            tokens={"shared_token": {"client_id": "first", "scopes": []}}
        )
        v2 = StaticTokenVerifier(
            tokens={"shared_token": {"client_id": "second", "scopes": []}}
        )

        auth = MultiAuth(verifiers=[v1, v2])
        result = await auth.verify_token("shared_token")
        assert result is not None
        assert result.client_id == "first"

    async def test_no_match_returns_none(self):
        """When no server or verifier accepts the token, returns None."""
        v = StaticTokenVerifier(tokens={"known": {"client_id": "c", "scopes": []}})
        auth = MultiAuth(verifiers=[v])
        result = await auth.verify_token("unknown")
        assert result is None

    async def test_verifiers_only_no_server(self):
        """MultiAuth with only verifiers (no server) works."""
        v1 = StaticTokenVerifier(tokens={"token_a": {"client_id": "a", "scopes": []}})
        v2 = StaticTokenVerifier(tokens={"token_b": {"client_id": "b", "scopes": []}})

        auth = MultiAuth(verifiers=[v1, v2])

        result_a = await auth.verify_token("token_a")
        assert result_a is not None
        assert result_a.client_id == "a"

        result_b = await auth.verify_token("token_b")
        assert result_b is not None
        assert result_b.client_id == "b"

    async def test_server_match_short_circuits(self):
        """When the server matches, verifiers are not consulted."""
        # Both server and verifier know the same token with different client_ids
        server_verifier = StaticTokenVerifier(
            tokens={"token": {"client_id": "from-server", "scopes": []}}
        )
        server = RemoteAuthProvider(
            token_verifier=server_verifier,
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="https://api.example.com",
        )
        extra = StaticTokenVerifier(
            tokens={"token": {"client_id": "from-verifier", "scopes": []}}
        )

        auth = MultiAuth(server=server, verifiers=[extra])
        result = await auth.verify_token("token")
        assert result is not None
        assert result.client_id == "from-server"


class TestMultiAuthRoutes:
    """Test that routes delegate to the server."""

    def test_routes_from_server(self):
        verifier = StaticTokenVerifier(tokens={"t": {"client_id": "c", "scopes": []}})
        server = RemoteAuthProvider(
            token_verifier=verifier,
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="https://api.example.com",
        )
        auth = MultiAuth(server=server)
        routes = auth.get_routes(mcp_path="/mcp")
        # RemoteAuthProvider creates a protected resource metadata route
        assert len(routes) >= 1

    def test_no_routes_without_server(self):
        v = StaticTokenVerifier(tokens={"t": {"client_id": "c", "scopes": []}})
        auth = MultiAuth(verifiers=[v])
        assert auth.get_routes() == []


class TestMultiAuthIntegration:
    """Integration tests: MultiAuth with a real FastMCP HTTP app."""

    async def test_multi_auth_rejects_bad_tokens(self):
        """End-to-end: MultiAuth rejects unknown tokens at the HTTP layer."""
        oauth_tokens = StaticTokenVerifier(
            tokens={
                "oauth_token": {
                    "client_id": "interactive-client",
                    "scopes": ["read"],
                }
            }
        )
        m2m_tokens = StaticTokenVerifier(
            tokens={
                "m2m_token": {
                    "client_id": "backend-service",
                    "scopes": ["read"],
                }
            }
        )

        auth = MultiAuth(verifiers=[oauth_tokens, m2m_tokens])
        mcp = FastMCP("test", auth=auth)
        app = mcp.http_app(path="/mcp")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
        ) as client:
            # No token → 401
            response = await client.get("/mcp")
            assert response.status_code == 401

            # Bad token → 401
            response = await client.get(
                "/mcp", headers={"Authorization": "Bearer bad_token"}
            )
            assert response.status_code == 401

    async def test_multi_auth_with_server_provides_routes(self):
        """MultiAuth with a server exposes the server's metadata routes."""
        verifier = StaticTokenVerifier(tokens={"t": {"client_id": "c", "scopes": []}})
        server = RemoteAuthProvider(
            token_verifier=verifier,
            authorization_servers=[AnyHttpUrl("https://auth.example.com")],
            base_url="https://api.example.com",
        )
        extra = StaticTokenVerifier(tokens={"m2m": {"client_id": "svc", "scopes": []}})

        auth = MultiAuth(server=server, verifiers=[extra])
        mcp = FastMCP("test", auth=auth)
        app = mcp.http_app(path="/mcp")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://api.example.com",
        ) as client:
            # Protected resource metadata should be available
            response = await client.get("/.well-known/oauth-protected-resource/mcp")
            assert response.status_code == 200
            data = response.json()
            assert data["resource"] == "https://api.example.com/mcp"
