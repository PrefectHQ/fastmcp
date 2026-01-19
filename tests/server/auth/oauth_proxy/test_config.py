"""Tests for OAuth proxy configuration and validation."""

import pytest
from mcp.server.auth.provider import AuthorizationParams, AuthorizeError
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from fastmcp.server.auth.oauth_proxy import OAuthProxy


class TestResourceURLValidation:
    """Tests for OAuth Proxy resource URL validation (GHSA-5h2m-4q8j-pqpj fix)."""

    @pytest.fixture
    def proxy_with_resource_url(self, jwt_verifier):
        """Create an OAuthProxy with set_mcp_path called."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="upstream-client",
            upstream_client_secret="upstream-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            jwt_signing_key="test-secret",
        )
        # Use non-default path to prove fix isn't relying on old hardcoded /mcp
        proxy.set_mcp_path("/api/v2/mcp")
        return proxy

    async def test_authorize_rejects_mismatched_resource(self, proxy_with_resource_url):
        """Test that authorization rejects requests with mismatched resource."""

        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        await proxy_with_resource_url.register_client(client)

        # Client requests a different resource than the server's
        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state",
            code_challenge="challenge",
            scopes=["read"],
            resource="https://malicious-server.com/mcp",  # Wrong resource
        )

        with pytest.raises(AuthorizeError) as exc_info:
            await proxy_with_resource_url.authorize(client, params)

        assert exc_info.value.error == "invalid_target"
        assert "Resource does not match" in exc_info.value.error_description

    async def test_authorize_accepts_matching_resource(self, proxy_with_resource_url):
        """Test that authorization accepts requests with matching resource."""
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        await proxy_with_resource_url.register_client(client)

        # Client requests the correct resource (must match /api/v2/mcp path)
        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state",
            code_challenge="challenge",
            scopes=["read"],
            resource="https://proxy.example.com/api/v2/mcp",  # Correct resource
        )

        # Should succeed (redirect to consent page)
        redirect_url = await proxy_with_resource_url.authorize(client, params)
        assert "/consent" in redirect_url

    async def test_authorize_rejects_old_hardcoded_mcp_path(
        self, proxy_with_resource_url
    ):
        """Test that old hardcoded /mcp path is rejected when server uses different path."""

        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        await proxy_with_resource_url.register_client(client)

        # Client requests the old hardcoded /mcp path (would have worked before fix)
        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state",
            code_challenge="challenge",
            scopes=["read"],
            resource="https://proxy.example.com/mcp",  # Old hardcoded path
        )

        # Should fail because server is at /api/v2/mcp, not /mcp
        with pytest.raises(AuthorizeError) as exc_info:
            await proxy_with_resource_url.authorize(client, params)

        assert exc_info.value.error == "invalid_target"

    async def test_authorize_accepts_no_resource(self, proxy_with_resource_url):
        """Test that authorization accepts requests without resource parameter."""
        client = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="test-secret",
            redirect_uris=[AnyUrl("http://localhost:12345/callback")],
        )

        await proxy_with_resource_url.register_client(client)

        # Client doesn't specify resource
        params = AuthorizationParams(
            redirect_uri=AnyUrl("http://localhost:12345/callback"),
            redirect_uri_provided_explicitly=True,
            state="client-state",
            code_challenge="challenge",
            scopes=["read"],
            # No resource parameter
        )

        # Should succeed (no resource check needed)
        redirect_url = await proxy_with_resource_url.authorize(client, params)
        assert "/consent" in redirect_url

    def test_set_mcp_path_creates_jwt_issuer_with_correct_audience(self, jwt_verifier):
        """Test that set_mcp_path creates JWTIssuer with correct audience."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="upstream-client",
            upstream_client_secret="upstream-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            jwt_signing_key="test-secret",
        )

        # Before set_mcp_path, _jwt_issuer is None
        assert proxy._jwt_issuer is None

        # Call set_mcp_path with custom path
        proxy.set_mcp_path("/custom/mcp")

        # After set_mcp_path, _jwt_issuer should be created
        assert proxy._jwt_issuer is not None
        assert proxy.jwt_issuer.audience == "https://proxy.example.com/custom/mcp"
        assert proxy.jwt_issuer.issuer == "https://proxy.example.com/"

    def test_set_mcp_path_uses_base_url_if_no_path(self, jwt_verifier):
        """Test that set_mcp_path uses base_url as audience if no path provided."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="upstream-client",
            upstream_client_secret="upstream-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            jwt_signing_key="test-secret",
        )

        proxy.set_mcp_path(None)

        assert proxy.jwt_issuer.audience == "https://proxy.example.com/"

    def test_jwt_issuer_property_raises_if_not_initialized(self, jwt_verifier):
        """Test that jwt_issuer property raises if set_mcp_path not called."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="upstream-client",
            upstream_client_secret="upstream-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            jwt_signing_key="test-secret",
        )

        with pytest.raises(RuntimeError) as exc_info:
            _ = proxy.jwt_issuer

        assert "JWT issuer not initialized" in str(exc_info.value)

    def test_get_routes_calls_set_mcp_path(self, jwt_verifier):
        """Test that get_routes() calls set_mcp_path() to initialize JWT issuer."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://oauth.example.com/authorize",
            upstream_token_endpoint="https://oauth.example.com/token",
            upstream_client_id="upstream-client",
            upstream_client_secret="upstream-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            jwt_signing_key="test-secret",
        )

        # Before get_routes, _jwt_issuer is None
        assert proxy._jwt_issuer is None

        # get_routes should call set_mcp_path internally
        proxy.get_routes("/api/mcp")

        # After get_routes, _jwt_issuer should be created with correct audience
        assert proxy._jwt_issuer is not None
        assert proxy.jwt_issuer.audience == "https://proxy.example.com/api/mcp"
