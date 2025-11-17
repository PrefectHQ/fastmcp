"""Comprehensive tests for OIDC Proxy Provider functionality."""

import json
from unittest.mock import MagicMock, patch

import pytest
from httpx import Response
from pydantic import AnyHttpUrl

from fastmcp.server.auth.oidc_proxy import OIDCConfiguration, OIDCProxy
from fastmcp.server.auth.providers.introspection import IntrospectionTokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier

TEST_ISSUER = "https://example.com"
TEST_AUTHORIZATION_ENDPOINT = "https://example.com/authorize"
TEST_TOKEN_ENDPOINT = "https://example.com/oauth/token"

TEST_CONFIG_URL = "https://example.com/.well-known/openid-configuration"
TEST_CLIENT_ID = "test-client-id"
TEST_CLIENT_SECRET = "test-client-secret"
TEST_BASE_URL = "https://example.com:8000/"


# =============================================================================
# Test Fixtures
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


@pytest.fixture
def invalid_oidc_configuration_dict():
    """Create an invalid OIDC configuration dict for testing."""
    return {
        "issuer": TEST_ISSUER,
        "authorization_endpoint": TEST_AUTHORIZATION_ENDPOINT,
        "token_endpoint": TEST_TOKEN_ENDPOINT,
        "jwks_uri": "https://example.com/.well-known/jwks.json",
    }


@pytest.fixture
def valid_google_oidc_configuration_dict():
    """Create a valid Google OIDC configuration dict for testing.

    See: https://accounts.google.com/.well-known/openid-configuration
    """
    google_config_str = """
    {
      "issuer": "https://accounts.google.com",
      "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
      "device_authorization_endpoint": "https://oauth2.googleapis.com/device/code",
      "token_endpoint": "https://oauth2.googleapis.com/token",
      "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo",
      "revocation_endpoint": "https://oauth2.googleapis.com/revoke",
      "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
      "response_types_supported": [
        "code",
        "token",
        "id_token",
        "code token",
        "code id_token",
        "token id_token",
        "code token id_token",
        "none"
      ],
      "response_modes_supported": [
        "query",
        "fragment",
        "form_post"
      ],
      "subject_types_supported": [
        "public"
      ],
      "id_token_signing_alg_values_supported": [
        "RS256"
      ],
      "scopes_supported": [
        "openid",
        "email",
        "profile"
      ],
      "token_endpoint_auth_methods_supported": [
        "client_secret_post",
        "client_secret_basic"
      ],
      "claims_supported": [
        "aud",
        "email",
        "email_verified",
        "exp",
        "family_name",
        "given_name",
        "iat",
        "iss",
        "name",
        "picture",
        "sub"
      ],
      "code_challenge_methods_supported": [
        "plain",
        "S256"
      ],
      "grant_types_supported": [
        "authorization_code",
        "refresh_token",
        "urn:ietf:params:oauth:grant-type:device_code",
        "urn:ietf:params:oauth:grant-type:jwt-bearer"
      ]
    }
    """

    return json.loads(google_config_str)


@pytest.fixture
def valid_auth0_oidc_configuration_dict():
    """Create a valid Auth0 OIDC configuration dict for testing.

    See: https://<tenant>.us.auth0.com/.well-known/openid-configuration
    """
    auth0_config_str = """
    {
      "issuer": "https://example.us.auth0.com/",
      "authorization_endpoint": "https://example.us.auth0.com/authorize",
      "token_endpoint": "https://example.us.auth0.com/oauth/token",
      "device_authorization_endpoint": "https://example.us.auth0.com/oauth/device/code",
      "userinfo_endpoint": "https://example.us.auth0.com/userinfo",
      "mfa_challenge_endpoint": "https://example.us.auth0.com/mfa/challenge",
      "jwks_uri": "https://example.us.auth0.com/.well-known/jwks.json",
      "registration_endpoint": "https://example.us.auth0.com/oidc/register",
      "revocation_endpoint": "https://example.us.auth0.com/oauth/revoke",
      "scopes_supported": [
        "openid",
        "profile",
        "offline_access",
        "name",
        "given_name",
        "family_name",
        "nickname",
        "email",
        "email_verified",
        "picture",
        "created_at",
        "identities",
        "phone",
        "address"
      ],
      "response_types_supported": [
        "code",
        "token",
        "id_token",
        "code token",
        "code id_token",
        "token id_token",
        "code token id_token"
      ],
      "code_challenge_methods_supported": [
        "S256",
        "plain"
      ],
      "response_modes_supported": [
        "query",
        "fragment",
        "form_post"
      ],
      "subject_types_supported": [
        "public"
      ],
      "token_endpoint_auth_methods_supported": [
        "client_secret_basic",
        "client_secret_post",
        "private_key_jwt",
        "tls_client_auth",
        "self_signed_tls_client_auth"
      ],
      "token_endpoint_auth_signing_alg_values_supported": [
        "RS256",
        "RS384",
        "PS256"
      ],
      "claims_supported": [
        "aud",
        "auth_time",
        "created_at",
        "email",
        "email_verified",
        "exp",
        "family_name",
        "given_name",
        "iat",
        "identities",
        "iss",
        "name",
        "nickname",
        "phone_number",
        "picture",
        "sub"
      ],
      "request_uri_parameter_supported": false,
      "request_parameter_supported": true,
      "id_token_signing_alg_values_supported": [
        "HS256",
        "RS256",
        "PS256"
      ],
      "tls_client_certificate_bound_access_tokens": true,
      "request_object_signing_alg_values_supported": [
        "RS256",
        "RS384",
        "PS256"
      ],
      "backchannel_logout_supported": true,
      "backchannel_logout_session_supported": true,
      "end_session_endpoint": "https://example.us.auth0.com/oidc/logout",
      "backchannel_authentication_endpoint": "https://example.us.auth0.com/bc-authorize",
      "backchannel_token_delivery_modes_supported": [
        "poll"
      ],
      "global_token_revocation_endpoint": "https://example.us.auth0.com/oauth/global-token-revocation/connection/{connectionName}",
      "global_token_revocation_endpoint_auth_methods_supported": [
        "global-token-revocation+jwt"
      ]
    }
    """

    return json.loads(auth0_config_str)


# =============================================================================
# Test Classes
# =============================================================================


def validate_config(config, source_dict):
    """Validate an OIDC configuration against the source dict."""
    for source_key, source_value in source_dict.items():
        config_value = getattr(config, source_key, None)
        if not hasattr(config, source_key):
            continue

        config_value = getattr(config, source_key, None)
        if isinstance(config_value, AnyHttpUrl):
            config_value = str(config_value)

        assert config_value == source_value


class TestOIDCConfiguration:
    """Tests for OIDC configuration."""

    def test_default_configuration(self, valid_oidc_configuration_dict):
        """Test default configuration with valid dict."""
        config = OIDCConfiguration.model_validate(valid_oidc_configuration_dict)
        validate_config(config, valid_oidc_configuration_dict)

    def test_default_configuration_with_issuer_trailing_slash(
        self, valid_oidc_configuration_dict
    ):
        """Test default configuration with valid dict and issuer trailing slash."""
        valid_oidc_configuration_dict["issuer"] += "/"
        config = OIDCConfiguration.model_validate(valid_oidc_configuration_dict)
        validate_config(config, valid_oidc_configuration_dict)

    def test_explicit_strict_configuration(self, valid_oidc_configuration_dict):
        """Test default configuration with explicit True strict setting and valid dict."""
        valid_oidc_configuration_dict["strict"] = True
        config = OIDCConfiguration.model_validate(valid_oidc_configuration_dict)
        validate_config(config, valid_oidc_configuration_dict)

    def test_explicit_strict_configuration_with_issuer_trailing_slash(
        self, valid_oidc_configuration_dict
    ):
        """Test default configuration with explicit True strict setting, valid dict and issuer trailing slash."""
        valid_oidc_configuration_dict["issuer"] += "/"
        config = OIDCConfiguration.model_validate(valid_oidc_configuration_dict)
        validate_config(config, valid_oidc_configuration_dict)

    def test_default_configuration_raises_error(self, invalid_oidc_configuration_dict):
        """Test default configuration with invalid dict."""
        with pytest.raises(ValueError, match="Missing required configuration metadata"):
            OIDCConfiguration.model_validate(invalid_oidc_configuration_dict)

    def test_explicit_strict_configuration_raises_error(
        self, invalid_oidc_configuration_dict
    ):
        """Test default configuration with explicit True strict setting and invalid dict."""
        invalid_oidc_configuration_dict["strict"] = True
        with pytest.raises(ValueError, match="Missing required configuration metadata"):
            OIDCConfiguration.model_validate(invalid_oidc_configuration_dict)

    def test_bad_url_raises_error(self, valid_oidc_configuration_dict):
        """Test default configuration with bad URL setting."""
        valid_oidc_configuration_dict["issuer"] = "not-a-URL"
        with pytest.raises(ValueError, match="Invalid URL for configuration metadata"):
            OIDCConfiguration.model_validate(valid_oidc_configuration_dict)

    def test_explicit_strict_with_bad_url_raises_error(
        self, valid_oidc_configuration_dict
    ):
        """Test default configuration with explicit True strict setting and bad URL setting."""
        valid_oidc_configuration_dict["strict"] = True
        valid_oidc_configuration_dict["issuer"] = "not-a-URL"
        with pytest.raises(ValueError, match="Invalid URL for configuration metadata"):
            OIDCConfiguration.model_validate(valid_oidc_configuration_dict)

    def test_not_strict_configuration(self):
        """Test default configuration with explicit False strict setting."""
        config = OIDCConfiguration.model_validate({"strict": False})

        assert config.issuer is None
        assert config.authorization_endpoint is None
        assert config.token_endpoint is None
        assert config.jwks_uri is None
        assert config.response_types_supported is None
        assert config.subject_types_supported is None
        assert config.id_token_signing_alg_values_supported is None

    def test_not_strict_configuration_with_invalid_config(
        self, invalid_oidc_configuration_dict
    ):
        """Test default configuration with explicit False strict setting."""
        invalid_oidc_configuration_dict["strict"] = False
        config = OIDCConfiguration.model_validate(invalid_oidc_configuration_dict)

        validate_config(config, invalid_oidc_configuration_dict)

    def test_not_strict_configuration_with_bad_url(self, valid_oidc_configuration_dict):
        """Test default configuration with explicit False strict setting."""
        valid_oidc_configuration_dict["strict"] = False
        valid_oidc_configuration_dict["issuer"] = "not-a-url"
        config = OIDCConfiguration.model_validate(valid_oidc_configuration_dict)

        validate_config(config, valid_oidc_configuration_dict)

    def test_google_configuration(self, valid_google_oidc_configuration_dict):
        """Test Google configuration."""
        config = OIDCConfiguration.model_validate(valid_google_oidc_configuration_dict)

        validate_config(config, valid_google_oidc_configuration_dict)

    def test_auth0_configuration(self, valid_auth0_oidc_configuration_dict):
        """Test Auth0 configuration."""
        config = OIDCConfiguration.model_validate(valid_auth0_oidc_configuration_dict)

        validate_config(config, valid_auth0_oidc_configuration_dict)


def validate_get_oidc_configuration(oidc_configuration, strict, timeout_seconds):
    """Validate get_oidc_configuration call."""
    with patch("httpx.get") as mock_get:
        mock_response = MagicMock(spec=Response)
        mock_response.json.return_value = oidc_configuration
        mock_get.return_value = mock_response

        config = OIDCConfiguration.get_oidc_configuration(
            config_url=AnyHttpUrl(TEST_CONFIG_URL),
            strict=strict,
            timeout_seconds=timeout_seconds,
        )

        validate_config(config, oidc_configuration)

        mock_get.assert_called_once()

        call_args = mock_get.call_args
        assert call_args[0][0] == TEST_CONFIG_URL

        return call_args


class TestGetOIDCConfiguration:
    """Tests for getting OIDC configuration."""

    def test_get_oidc_configuration(self, valid_oidc_configuration_dict):
        """Test with valid response and explicit timeout."""
        call_args = validate_get_oidc_configuration(
            valid_oidc_configuration_dict, True, 10
        )
        assert call_args[1]["timeout"] == 10

    def test_get_oidc_configuration_no_timeout(self, valid_oidc_configuration_dict):
        """Test with valid response and no timeout."""
        call_args = validate_get_oidc_configuration(
            valid_oidc_configuration_dict, True, None
        )
        assert "timeout" not in call_args[1]

    def test_get_oidc_configuration_raises_error(
        self, invalid_oidc_configuration_dict
    ) -> None:
        """Test with invalid response."""
        with pytest.raises(ValueError, match="Missing required configuration metadata"):
            validate_get_oidc_configuration(invalid_oidc_configuration_dict, True, 10)

    def test_get_oidc_configuration_not_strict(
        self, invalid_oidc_configuration_dict
    ) -> None:
        """Test with invalid response and strict set to False."""
        with patch("httpx.get") as mock_get:
            mock_response = MagicMock(spec=Response)
            mock_response.json.return_value = invalid_oidc_configuration_dict
            mock_get.return_value = mock_response

            OIDCConfiguration.get_oidc_configuration(
                config_url=AnyHttpUrl(TEST_CONFIG_URL),
                strict=False,
                timeout_seconds=10,
            )

            mock_get.assert_called_once()

            call_args = mock_get.call_args
            assert call_args[0][0] == TEST_CONFIG_URL


def validate_proxy(mock_get, proxy, oidc_config):
    """Validate OIDC proxy."""
    mock_get.assert_called_once()

    call_args = mock_get.call_args
    assert str(call_args[0][0]) == TEST_CONFIG_URL

    assert proxy._upstream_authorization_endpoint == TEST_AUTHORIZATION_ENDPOINT
    assert proxy._upstream_token_endpoint == TEST_TOKEN_ENDPOINT
    assert proxy._upstream_client_id == TEST_CLIENT_ID
    assert proxy._upstream_client_secret.get_secret_value() == TEST_CLIENT_SECRET
    assert str(proxy.base_url) == TEST_BASE_URL
    assert proxy.oidc_config == oidc_config


class TestOIDCProxyInitialization:
    """Tests for OIDC proxy initialization."""

    def test_default_initialization(self, valid_oidc_configuration_dict):
        """Test default initialization."""
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

            validate_proxy(mock_get, proxy, oidc_config)

    def test_timeout_seconds_initialization(self, valid_oidc_configuration_dict):
        """Test timeout seconds initialization."""
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
                timeout_seconds=12,
                jwt_signing_key="test-secret",
            )

            validate_proxy(mock_get, proxy, oidc_config)

            call_args = mock_get.call_args
            assert call_args[1]["timeout_seconds"] == 12

    def test_token_verifier_initialization(self, valid_oidc_configuration_dict):
        """Test token verifier initialization."""
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
                algorithm="RS256",
                audience="oidc-proxy-test-audience",
                required_scopes=["required", "scopes"],
                jwt_signing_key="test-secret",
            )

            validate_proxy(mock_get, proxy, oidc_config)

            assert isinstance(proxy._token_validator, JWTVerifier)

            assert proxy._token_validator.algorithm == "RS256"
            assert proxy._token_validator.audience == "oidc-proxy-test-audience"
            assert proxy._token_validator.required_scopes == ["required", "scopes"]

    def test_extra_parameters_initialization(self, valid_oidc_configuration_dict):
        """Test other parameters initialization."""
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
                audience="oidc-proxy-test-audience",
                jwt_signing_key="test-secret",
            )

            validate_proxy(mock_get, proxy, oidc_config)

            assert proxy._extra_authorize_params == {
                "audience": "oidc-proxy-test-audience"
            }
            assert proxy._extra_token_params == {"audience": "oidc-proxy-test-audience"}

    def test_other_parameters_initialization(self, valid_oidc_configuration_dict):
        """Test other parameters initialization."""
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
                redirect_path="/oidc/proxy",
                allowed_client_redirect_uris=["http://localhost:*"],
                token_endpoint_auth_method="client_secret_post",
                jwt_signing_key="test-secret",
            )

            validate_proxy(mock_get, proxy, oidc_config)

            assert proxy._redirect_path == "/oidc/proxy"
            assert proxy._allowed_client_redirect_uris == ["http://localhost:*"]
            assert proxy._token_endpoint_auth_method == "client_secret_post"

    def test_no_config_url_initialization_raises_error(
        self, valid_oidc_configuration_dict
    ):
        """Test no config URL initialization."""
        with patch(
            "fastmcp.server.auth.oidc_proxy.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            oidc_config = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            mock_get.return_value = oidc_config

            with pytest.raises(ValueError, match="Missing required config URL"):
                OIDCProxy(
                    config_url=None,  # type: ignore
                    client_id=TEST_CLIENT_ID,
                    client_secret=TEST_CLIENT_SECRET,
                    base_url=TEST_BASE_URL,
                    jwt_signing_key="test-secret",
                )

    def test_no_client_id_initialization_raises_error(
        self, valid_oidc_configuration_dict
    ):
        """Test no client id initialization."""
        with patch(
            "fastmcp.server.auth.oidc_proxy.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            oidc_config = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            mock_get.return_value = oidc_config

            with pytest.raises(ValueError, match="Missing required client id"):
                OIDCProxy(
                    config_url=TEST_CONFIG_URL,
                    client_id=None,  # type: ignore
                    client_secret=TEST_CLIENT_SECRET,
                    base_url=TEST_BASE_URL,
                )

    def test_no_client_secret_initialization_raises_error(
        self, valid_oidc_configuration_dict
    ):
        """Test no client secret initialization."""
        with patch(
            "fastmcp.server.auth.oidc_proxy.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            oidc_config = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            mock_get.return_value = oidc_config

            with pytest.raises(ValueError, match="Missing required client secret"):
                OIDCProxy(
                    config_url=TEST_CONFIG_URL,
                    client_id=TEST_CLIENT_ID,
                    client_secret=None,  # type: ignore
                    base_url=TEST_BASE_URL,
                )

    def test_no_base_url_initialization_raises_error(
        self, valid_oidc_configuration_dict
    ):
        """Test no base URL initialization."""
        with patch(
            "fastmcp.server.auth.oidc_proxy.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            oidc_config = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            mock_get.return_value = oidc_config

            with pytest.raises(ValueError, match="Missing required base URL"):
                OIDCProxy(
                    config_url=TEST_CONFIG_URL,
                    client_id=TEST_CLIENT_ID,
                    client_secret=TEST_CLIENT_SECRET,
                    base_url=None,  # type: ignore
                )

    def test_custom_token_verifier_initialization(self, valid_oidc_configuration_dict):
        """Test initialization with custom token verifier."""
        with patch(
            "fastmcp.server.auth.oidc_proxy.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            oidc_config = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            mock_get.return_value = oidc_config

            # Create custom verifier for opaque tokens
            custom_verifier = IntrospectionTokenVerifier(
                introspection_url="https://example.com/oauth/introspect",
                client_id="introspection-client",
                client_secret="introspection-secret",
                required_scopes=["custom", "scopes"],
            )

            proxy = OIDCProxy(
                config_url=TEST_CONFIG_URL,
                client_id=TEST_CLIENT_ID,
                client_secret=TEST_CLIENT_SECRET,
                base_url=TEST_BASE_URL,
                token_verifier=custom_verifier,
                jwt_signing_key="test-secret",
            )

            validate_proxy(mock_get, proxy, oidc_config)

            # Verify the custom verifier is used
            assert proxy._token_validator is custom_verifier
            assert isinstance(proxy._token_validator, IntrospectionTokenVerifier)

            # Verify required_scopes are properly loaded from the custom verifier
            assert proxy.required_scopes == ["custom", "scopes"]

    def test_custom_token_verifier_with_algorithm_raises_error(
        self, valid_oidc_configuration_dict
    ):
        """Test that providing algorithm with custom verifier raises error."""
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

            with pytest.raises(
                ValueError,
                match="Cannot specify 'algorithm' when providing a custom token_verifier",
            ):
                OIDCProxy(
                    config_url=TEST_CONFIG_URL,
                    client_id=TEST_CLIENT_ID,
                    client_secret=TEST_CLIENT_SECRET,
                    base_url=TEST_BASE_URL,
                    token_verifier=custom_verifier,
                    algorithm="RS256",  # This should cause an error
                    jwt_signing_key="test-secret",
                )

    def test_custom_token_verifier_with_required_scopes_raises_error(
        self, valid_oidc_configuration_dict
    ):
        """Test that providing required_scopes with custom verifier raises error."""
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

            with pytest.raises(
                ValueError,
                match="Cannot specify 'required_scopes' when providing a custom token_verifier",
            ):
                OIDCProxy(
                    config_url=TEST_CONFIG_URL,
                    client_id=TEST_CLIENT_ID,
                    client_secret=TEST_CLIENT_SECRET,
                    base_url=TEST_BASE_URL,
                    token_verifier=custom_verifier,
                    required_scopes=["read", "write"],  # This should cause an error
                    jwt_signing_key="test-secret",
                )

    def test_custom_token_verifier_with_audience_allowed(
        self, valid_oidc_configuration_dict
    ):
        """Test that providing audience with custom verifier is allowed (for OAuth flow)."""
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

            # This should NOT raise an error - audience is for OAuth flow
            proxy = OIDCProxy(
                config_url=TEST_CONFIG_URL,
                client_id=TEST_CLIENT_ID,
                client_secret=TEST_CLIENT_SECRET,
                base_url=TEST_BASE_URL,
                token_verifier=custom_verifier,
                audience="test-audience",  # Should be allowed for OAuth flow
                jwt_signing_key="test-secret",
            )

            validate_proxy(mock_get, proxy, oidc_config)
            assert proxy._extra_authorize_params == {"audience": "test-audience"}
            assert proxy._extra_token_params == {"audience": "test-audience"}

    def test_extra_authorize_params_initialization(self, valid_oidc_configuration_dict):
        """Test extra authorize params initialization."""
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
                extra_authorize_params={
                    "prompt": "consent",
                    "access_type": "offline",
                },
            )

            validate_proxy(mock_get, proxy, oidc_config)

            assert proxy._extra_authorize_params == {
                "prompt": "consent",
                "access_type": "offline",
            }
            # Token params should be empty since we didn't set them
            assert proxy._extra_token_params == {}

    def test_extra_token_params_initialization(self, valid_oidc_configuration_dict):
        """Test extra token params initialization."""
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
                extra_token_params={"custom_param": "custom_value"},
            )

            validate_proxy(mock_get, proxy, oidc_config)

            # Authorize params should be empty since we didn't set them
            assert proxy._extra_authorize_params == {}
            assert proxy._extra_token_params == {"custom_param": "custom_value"}

    def test_extra_params_merge_with_audience(self, valid_oidc_configuration_dict):
        """Test that extra params merge with audience, with user params taking precedence."""
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
                audience="original-audience",
                jwt_signing_key="test-secret",
                extra_authorize_params={
                    "prompt": "consent",
                    "audience": "overridden-audience",  # Should override the audience param
                },
                extra_token_params={"custom": "value"},
            )

            validate_proxy(mock_get, proxy, oidc_config)

            # User's extra_authorize_params should override audience
            assert proxy._extra_authorize_params == {
                "audience": "overridden-audience",
                "prompt": "consent",
            }
            # Token params should have both audience (from audience param) and custom
            assert proxy._extra_token_params == {
                "audience": "original-audience",
                "custom": "value",
            }
