"""Tests for OIDC Proxy verify_id_token functionality."""

from unittest.mock import patch

import pytest
from pydantic import AnyHttpUrl

from fastmcp.server.auth.oauth_proxy.models import UpstreamTokenSet
from fastmcp.server.auth.oidc_proxy import OIDCConfiguration, OIDCProxy
from fastmcp.server.auth.providers.introspection import IntrospectionTokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier

TEST_ISSUER = "https://example.com"
TEST_AUTHORIZATION_ENDPOINT = "https://example.com/authorize"
TEST_TOKEN_ENDPOINT = "https://example.com/oauth/token"

TEST_CONFIG_URL = AnyHttpUrl("https://example.com/.well-known/openid-configuration")
TEST_CLIENT_ID = "test-client-id"
TEST_CLIENT_SECRET = "test-client-secret"
TEST_BASE_URL = AnyHttpUrl("https://example.com:8000/")


# =============================================================================
# Shared Fixtures
# =============================================================================


@pytest.fixture
def valid_oidc_configuration_dict():
    """Create a valid OIDC configuration dict for testing."""
    return {
        "issuer": TEST_ISSUER,
        "authorization_endpoint": TEST_AUTHORIZATION_ENDPOINT,
        "token_endpoint": TEST_TOKEN_ENDPOINT,
        "jwks_uri": "https://example.com/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }


# =============================================================================
# Test Helpers
# =============================================================================


def _make_upstream_token_set(*, id_token: str | None = None) -> UpstreamTokenSet:
    """Create an UpstreamTokenSet with optional id_token."""
    raw_token_data: dict[str, str] = {"access_token": "opaque-access-token"}
    if id_token is not None:
        raw_token_data["id_token"] = id_token
    return UpstreamTokenSet(
        upstream_token_id="test-id",
        access_token="opaque-access-token",
        refresh_token=None,
        refresh_token_expires_at=None,
        expires_at=9999999999.0,
        token_type="Bearer",
        scope="openid",
        client_id="test-client",
        created_at=1000000000.0,
        raw_token_data=raw_token_data,
    )


# =============================================================================
# Test Classes
# =============================================================================


class TestVerifyIdToken:
    """Tests for verify_id_token functionality."""

    def test_verify_id_token_disabled_by_default(self, valid_oidc_configuration_dict):
        """Default behavior: verify the access_token."""
        with patch(
            "fastmcp.server.auth.oidc_proxy.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            oidc_config = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            mock_get.return_value = oidc_config

            proxy = OIDCProxy(
                config_url=TEST_CONFIG_URL,
                client_id=TEST_CLIENT_ID,
                client_secret=TEST_CLIENT_SECRET,
                base_url=TEST_BASE_URL,
                jwt_signing_key="test-secret",
            )
            token_set = _make_upstream_token_set(id_token="jwt-id-token")

            assert proxy._get_verification_token(token_set) == "opaque-access-token"

    def test_verify_id_token_returns_id_token(self, valid_oidc_configuration_dict):
        """When enabled, verify the id_token instead of access_token."""
        with patch(
            "fastmcp.server.auth.oidc_proxy.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            oidc_config = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            mock_get.return_value = oidc_config

            proxy = OIDCProxy(
                config_url=TEST_CONFIG_URL,
                client_id=TEST_CLIENT_ID,
                client_secret=TEST_CLIENT_SECRET,
                base_url=TEST_BASE_URL,
                jwt_signing_key="test-secret",
                verify_id_token=True,
            )
            token_set = _make_upstream_token_set(id_token="jwt-id-token")

            assert proxy._get_verification_token(token_set) == "jwt-id-token"

    def test_verify_id_token_returns_none_when_missing(
        self, valid_oidc_configuration_dict
    ):
        """When enabled but id_token is absent, return None."""
        with patch(
            "fastmcp.server.auth.oidc_proxy.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            oidc_config = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            mock_get.return_value = oidc_config

            proxy = OIDCProxy(
                config_url=TEST_CONFIG_URL,
                client_id=TEST_CLIENT_ID,
                client_secret=TEST_CLIENT_SECRET,
                base_url=TEST_BASE_URL,
                jwt_signing_key="test-secret",
                verify_id_token=True,
            )
            token_set = _make_upstream_token_set(id_token=None)

            assert proxy._get_verification_token(token_set) is None

    def test_verify_id_token_works_with_custom_verifier(
        self, valid_oidc_configuration_dict
    ):
        """verify_id_token can be combined with a custom token_verifier."""
        with patch(
            "fastmcp.server.auth.oidc_proxy.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            oidc_config = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            mock_get.return_value = oidc_config

            custom_verifier = IntrospectionTokenVerifier(
                introspection_url="https://example.com/oauth/introspect",
                client_id="introspection-client",
                client_secret="introspection-secret",
            )
            proxy = OIDCProxy(
                config_url=TEST_CONFIG_URL,
                client_id=TEST_CLIENT_ID,
                client_secret=TEST_CLIENT_SECRET,
                base_url=TEST_BASE_URL,
                jwt_signing_key="test-secret",
                verify_id_token=True,
                token_verifier=custom_verifier,
            )
            token_set = _make_upstream_token_set(id_token="jwt-id-token")

            assert proxy._get_verification_token(token_set) == "jwt-id-token"
            assert proxy._token_validator is custom_verifier

    def test_verify_id_token_survives_refresh_without_id_token(
        self, valid_oidc_configuration_dict
    ):
        """id_token from original auth is preserved when refresh omits it."""
        with patch(
            "fastmcp.server.auth.oidc_proxy.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            oidc_config = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            mock_get.return_value = oidc_config

            proxy = OIDCProxy(
                config_url=TEST_CONFIG_URL,
                client_id=TEST_CLIENT_ID,
                client_secret=TEST_CLIENT_SECRET,
                base_url=TEST_BASE_URL,
                jwt_signing_key="test-secret",
                verify_id_token=True,
            )

            token_set = _make_upstream_token_set(id_token="original-id-token")

            # Simulate a refresh response that omits id_token —
            # the merge in exchange_refresh_token should preserve it
            refresh_response = {
                "access_token": "new-access-token",
                "token_type": "Bearer",
            }
            token_set.raw_token_data = {**token_set.raw_token_data, **refresh_response}
            token_set.access_token = "new-access-token"

            assert proxy._get_verification_token(token_set) == "original-id-token"

    def test_verify_id_token_updated_when_refresh_includes_it(
        self, valid_oidc_configuration_dict
    ):
        """id_token is updated when refresh response includes a new one."""
        with patch(
            "fastmcp.server.auth.oidc_proxy.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            oidc_config = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            mock_get.return_value = oidc_config

            proxy = OIDCProxy(
                config_url=TEST_CONFIG_URL,
                client_id=TEST_CLIENT_ID,
                client_secret=TEST_CLIENT_SECRET,
                base_url=TEST_BASE_URL,
                jwt_signing_key="test-secret",
                verify_id_token=True,
            )

            token_set = _make_upstream_token_set(id_token="original-id-token")

            # Simulate a refresh response that includes a new id_token
            refresh_response = {
                "access_token": "new-access-token",
                "id_token": "refreshed-id-token",
            }
            token_set.raw_token_data = {**token_set.raw_token_data, **refresh_response}
            token_set.access_token = "new-access-token"

            assert proxy._get_verification_token(token_set) == "refreshed-id-token"

    def test_verify_id_token_uses_client_id_as_verifier_audience(
        self, valid_oidc_configuration_dict
    ):
        """When verify_id_token is enabled, the verifier audience should be
        client_id (matching id_token.aud), not the API audience parameter."""
        with patch(
            "fastmcp.server.auth.oidc_proxy.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            oidc_config = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            mock_get.return_value = oidc_config

            proxy = OIDCProxy(
                config_url=TEST_CONFIG_URL,
                client_id=TEST_CLIENT_ID,
                client_secret=TEST_CLIENT_SECRET,
                base_url=TEST_BASE_URL,
                jwt_signing_key="test-secret",
                audience="https://api.example.com",
                verify_id_token=True,
            )

            assert isinstance(proxy._token_validator, JWTVerifier)
            assert proxy._token_validator.audience == TEST_CLIENT_ID

            # The API audience should still be sent upstream
            assert (
                proxy._extra_authorize_params["audience"] == "https://api.example.com"
            )
            assert proxy._extra_token_params["audience"] == "https://api.example.com"

    def test_verify_id_token_without_audience_uses_client_id(
        self, valid_oidc_configuration_dict
    ):
        """When verify_id_token is enabled without an audience param,
        the verifier audience should still be client_id."""
        with patch(
            "fastmcp.server.auth.oidc_proxy.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            oidc_config = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            mock_get.return_value = oidc_config

            proxy = OIDCProxy(
                config_url=TEST_CONFIG_URL,
                client_id=TEST_CLIENT_ID,
                client_secret=TEST_CLIENT_SECRET,
                base_url=TEST_BASE_URL,
                jwt_signing_key="test-secret",
                verify_id_token=True,
            )

            assert isinstance(proxy._token_validator, JWTVerifier)
            assert proxy._token_validator.audience == TEST_CLIENT_ID

    def test_verify_id_token_does_not_enforce_scopes_on_verifier(
        self, valid_oidc_configuration_dict
    ):
        """When verify_id_token is enabled, required_scopes should not be
        passed to the JWTVerifier since id_tokens don't carry scope claims."""
        with patch(
            "fastmcp.server.auth.oidc_proxy.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            oidc_config = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            mock_get.return_value = oidc_config

            proxy = OIDCProxy(
                config_url=TEST_CONFIG_URL,
                client_id=TEST_CLIENT_ID,
                client_secret=TEST_CLIENT_SECRET,
                base_url=TEST_BASE_URL,
                jwt_signing_key="test-secret",
                required_scopes=["read", "write"],
                verify_id_token=True,
            )

            assert isinstance(proxy._token_validator, JWTVerifier)
            assert proxy._token_validator.required_scopes == []

            # Scopes should still be advertised via the proxy's required_scopes
            assert proxy.required_scopes == ["read", "write"]

            # Derived scope state should also be recomputed
            assert proxy._default_scope_str == "read write"
            assert proxy.client_registration_options is not None
            assert proxy.client_registration_options.valid_scopes == [
                "read",
                "write",
            ]
