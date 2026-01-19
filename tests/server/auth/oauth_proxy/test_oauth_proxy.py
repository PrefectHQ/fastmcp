"""Tests for OAuth proxy initialization and configuration."""

from fastmcp.server.auth.oauth_proxy import OAuthProxy


class TestOAuthProxyInitialization:
    """Tests for OAuth proxy initialization and configuration."""

    def test_basic_initialization(self, jwt_verifier):
        """Test basic proxy initialization with required parameters."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            jwt_signing_key="test-secret",
        )

        assert (
            proxy._upstream_authorization_endpoint
            == "https://auth.example.com/authorize"
        )
        assert proxy._upstream_token_endpoint == "https://auth.example.com/token"
        assert proxy._upstream_client_id == "client-123"
        assert proxy._upstream_client_secret.get_secret_value() == "secret-456"
        assert str(proxy.base_url) == "https://api.example.com/"

    def test_all_optional_parameters(self, jwt_verifier):
        """Test initialization with all optional parameters."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.example.com/authorize",
            upstream_token_endpoint="https://auth.example.com/token",
            upstream_client_id="client-123",
            upstream_client_secret="secret-456",
            upstream_revocation_endpoint="https://auth.example.com/revoke",
            token_verifier=jwt_verifier,
            base_url="https://api.example.com",
            redirect_path="/custom/callback",
            issuer_url="https://issuer.example.com",
            service_documentation_url="https://docs.example.com",
            allowed_client_redirect_uris=["http://localhost:*"],
            valid_scopes=["custom", "scopes"],
            forward_pkce=False,
            token_endpoint_auth_method="client_secret_post",
            jwt_signing_key="test-secret",
        )

        assert proxy._upstream_revocation_endpoint == "https://auth.example.com/revoke"
        assert proxy._redirect_path == "/custom/callback"
        assert proxy._forward_pkce is False
        assert proxy._token_endpoint_auth_method == "client_secret_post"
        assert proxy.client_registration_options is not None
        assert proxy.client_registration_options.valid_scopes == ["custom", "scopes"]

    def test_redirect_path_normalization(self, jwt_verifier):
        """Test that redirect_path is normalized with leading slash."""
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://auth.com/authorize",
            upstream_token_endpoint="https://auth.com/token",
            upstream_client_id="client",
            upstream_client_secret="secret",
            token_verifier=jwt_verifier,
            base_url="https://api.com",
            redirect_path="auth/callback",  # No leading slash
            jwt_signing_key="test-secret",
        )
        assert proxy._redirect_path == "/auth/callback"
