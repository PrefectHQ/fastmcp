"""Comprehensive tests for OIDC Proxy Provider functionality."""

from unittest.mock import MagicMock, patch

import pytest
from httpx import Response
from pydantic import AnyHttpUrl

from fastmcp.server.auth.oidc_proxy import OIDCConfiguration, OIDCProxy
from fastmcp.server.auth.providers.jwt import JWTVerifier

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
        "issuer": "https://example.com/",
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
        "issuer": "https://example.com/",
        "authorization_endpoint": TEST_AUTHORIZATION_ENDPOINT,
        "token_endpoint": TEST_TOKEN_ENDPOINT,
        "jwks_uri": "https://example.com/.well-known/jwks.json",
    }


# =============================================================================
# Test Classes
# =============================================================================


def validate_config(config):
    """Validate an OIDC configuration."""
    assert str(config.issuer) == "https://example.com/"
    assert str(config.authorization_endpoint) == TEST_AUTHORIZATION_ENDPOINT
    assert str(config.token_endpoint) == TEST_TOKEN_ENDPOINT
    assert str(config.jwks_uri) == "https://example.com/.well-known/jwks.json"
    assert config.response_types_supported == ["code"]
    assert config.subject_types_supported == ["public"]
    assert config.id_token_signing_alg_values_supported == ["RS256"]


class TestOIDCConfiguration:
    """Tests for OIDC configuration."""

    def test_default_configuration(self, valid_oidc_configuration_dict):
        """Test default configuration with valid dict."""
        config = OIDCConfiguration.model_validate(valid_oidc_configuration_dict)
        validate_config(config)

    def test_explicit_strict_configuration(self, valid_oidc_configuration_dict):
        """Test default configuration with explicit True strict setting and valid dict."""
        valid_oidc_configuration_dict["strict"] = True
        config = OIDCConfiguration.model_validate(valid_oidc_configuration_dict)
        validate_config(config)

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


def validate_get_oidc_configuration(oidc_configuration, strict, timeout_seconds):
    """Validate get_oidc_configuation call."""
    with patch("httpx.get") as mock_get:
        mock_response = MagicMock(spec=Response)
        mock_response.json.return_value = oidc_configuration
        mock_get.return_value = mock_response

        config = OIDCConfiguration.get_oidc_configuration(
            config_url=AnyHttpUrl(TEST_CONFIG_URL),
            strict=strict,
            timeout_seconds=timeout_seconds,
        )

        validate_config(config)

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
