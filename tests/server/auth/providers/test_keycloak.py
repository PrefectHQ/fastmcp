"""Unit tests for Keycloak OAuth provider."""

import pytest

from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.auth.providers.keycloak import KeycloakAuthProvider

TEST_REALM_URL = "https://keycloak.example.com/realms/test"
TEST_BASE_URL = "https://example.com:8000"
TEST_REQUIRED_SCOPES = ["openid", "profile"]


class TestKeycloakAuthProvider:
    """Test KeycloakAuthProvider initialization."""

    def test_init_with_explicit_params(self):
        """Test initialization with explicit parameters."""
        provider = KeycloakAuthProvider(
            realm_url=TEST_REALM_URL,
            base_url=TEST_BASE_URL,
            required_scopes=TEST_REQUIRED_SCOPES,
        )

        assert provider.realm_url == TEST_REALM_URL
        assert str(provider.base_url) == TEST_BASE_URL + "/"
        assert isinstance(provider.token_verifier, JWTVerifier)
        assert provider.token_verifier.required_scopes == TEST_REQUIRED_SCOPES
        jwt_verifier = provider.token_verifier
        assert isinstance(jwt_verifier, JWTVerifier)
        assert (
            jwt_verifier.jwks_uri == f"{TEST_REALM_URL}/protocol/openid-connect/certs"
        )
        assert jwt_verifier.issuer == TEST_REALM_URL

    def test_init_with_string_scopes(self):
        """Test initialization with scopes as comma-separated string."""
        provider = KeycloakAuthProvider(
            realm_url=TEST_REALM_URL,
            base_url=TEST_BASE_URL,
            required_scopes="openid,profile,email",
        )

        assert provider.token_verifier.required_scopes == ["openid", "profile", "email"]

    def test_init_with_custom_token_verifier(self):
        """Test initialization with custom token verifier."""
        custom_verifier = JWTVerifier(
            jwks_uri=f"{TEST_REALM_URL}/protocol/openid-connect/certs",
            issuer=TEST_REALM_URL,
            audience="custom-client-id",
            required_scopes=["custom:scope"],
        )

        provider = KeycloakAuthProvider(
            realm_url=TEST_REALM_URL,
            base_url=TEST_BASE_URL,
            token_verifier=custom_verifier,
        )

        assert provider.token_verifier is custom_verifier
        assert provider.token_verifier.audience == "custom-client-id"
        assert provider.token_verifier.required_scopes == ["custom:scope"]

    def test_authorization_servers_point_to_keycloak(self):
        """Test that authorization_servers points directly to the Keycloak realm."""
        provider = KeycloakAuthProvider(
            realm_url=TEST_REALM_URL,
            base_url=TEST_BASE_URL,
        )

        assert len(provider.authorization_servers) == 1
        assert str(provider.authorization_servers[0]).rstrip("/") == TEST_REALM_URL


class TestKeycloakHardCodedEndpoints:
    """Test hard-coded Keycloak endpoint patterns."""

    def test_uses_standard_keycloak_url_patterns(self):
        """Test that provider uses Keycloak-specific URL patterns."""
        provider = KeycloakAuthProvider(
            realm_url=TEST_REALM_URL,
            base_url=TEST_BASE_URL,
        )

        jwt_verifier = provider.token_verifier
        assert isinstance(jwt_verifier, JWTVerifier)
        assert (
            jwt_verifier.jwks_uri == f"{TEST_REALM_URL}/protocol/openid-connect/certs"
        )
        assert jwt_verifier.issuer == TEST_REALM_URL


class TestKeycloakRoutes:
    """Test Keycloak auth provider routes."""

    @pytest.fixture
    def keycloak_provider(self):
        """Create a KeycloakAuthProvider for testing."""
        return KeycloakAuthProvider(
            realm_url=TEST_REALM_URL,
            base_url=TEST_BASE_URL,
            required_scopes=TEST_REQUIRED_SCOPES,
        )

    def test_get_routes(self, keycloak_provider):
        """Test that get_routes returns only protected resource metadata (no proxy routes)."""
        routes = keycloak_provider.get_routes()

        paths = [route.path for route in routes]
        assert "/.well-known/oauth-protected-resource" in paths
        assert "/register" not in paths
        assert "/authorize" not in paths


class TestKeycloakEdgeCases:
    """Test edge cases for KeycloakAuthProvider."""

    def test_empty_required_scopes_handling(self):
        """Test handling of empty required scopes."""
        provider = KeycloakAuthProvider(
            realm_url=TEST_REALM_URL,
            base_url=TEST_BASE_URL,
            required_scopes=[],
        )

        assert provider.token_verifier.required_scopes == []

    def test_realm_url_with_trailing_slash(self):
        """Test handling of realm URL with trailing slash."""
        provider = KeycloakAuthProvider(
            realm_url=TEST_REALM_URL + "/",
            base_url=TEST_BASE_URL,
        )

        assert provider.realm_url == TEST_REALM_URL
