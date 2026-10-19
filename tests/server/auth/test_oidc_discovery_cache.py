"""Regression tests for OIDC discovery caching."""

from unittest.mock import MagicMock, patch

import pytest
from httpx2 import Response
from pydantic import AnyHttpUrl

from fastmcp.server.auth.oidc_proxy import (
    _OIDC_DISCOVERY_CACHE_TTL_SECONDS,
    OIDCConfiguration,
)

CONFIG_URL = AnyHttpUrl("https://cache.example.com/.well-known/openid-configuration")


@pytest.fixture
def discovery_document() -> dict[str, object]:
    return {
        "issuer": "https://cache.example.com",
        "authorization_endpoint": "https://cache.example.com/authorize",
        "token_endpoint": "https://cache.example.com/token",
        "jwks_uri": "https://cache.example.com/jwks",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }


def _response(document: dict[str, object]) -> MagicMock:
    response = MagicMock(spec=Response)
    response.json.return_value = document
    return response


def test_identical_discovery_configuration_is_cached(discovery_document):
    response = _response(discovery_document)

    with patch("httpx2.get", return_value=response) as mock_get:
        first = OIDCConfiguration.get_oidc_configuration(
            config_url=CONFIG_URL,
            strict=True,
            timeout_seconds=10,
        )
        second = OIDCConfiguration.get_oidc_configuration(
            config_url=CONFIG_URL,
            strict=True,
            timeout_seconds=10,
        )

    assert first == second
    mock_get.assert_called_once()


def test_cached_configuration_expires_and_refetches(discovery_document):
    response = _response(discovery_document)

    with (
        patch("httpx2.get", return_value=response) as mock_get,
        patch(
            "fastmcp.server.auth.oidc_proxy.monotonic",
            side_effect=[
                0.0,
                0.0,
                _OIDC_DISCOVERY_CACHE_TTL_SECONDS + 1,
                _OIDC_DISCOVERY_CACHE_TTL_SECONDS + 1,
            ],
        ),
    ):
        OIDCConfiguration.get_oidc_configuration(
            config_url=CONFIG_URL,
            strict=True,
            timeout_seconds=10,
        )
        OIDCConfiguration.get_oidc_configuration(
            config_url=CONFIG_URL,
            strict=True,
            timeout_seconds=10,
        )

    assert mock_get.call_count == 2


def test_cached_configuration_is_isolated_between_callers(discovery_document):
    response = _response(discovery_document)

    with patch("httpx2.get", return_value=response) as mock_get:
        first = OIDCConfiguration.get_oidc_configuration(
            config_url=CONFIG_URL,
            strict=True,
            timeout_seconds=10,
        )
        second = OIDCConfiguration.get_oidc_configuration(
            config_url=CONFIG_URL,
            strict=True,
            timeout_seconds=10,
        )

    assert first is not second
    assert isinstance(first.response_types_supported, list)
    first.response_types_supported.append("token")
    first.authorization_endpoint = "https://mutated.example.com/authorize"

    third = OIDCConfiguration.get_oidc_configuration(
        config_url=CONFIG_URL,
        strict=True,
        timeout_seconds=10,
    )

    assert list(second.response_types_supported or []) == ["code"]
    assert list(third.response_types_supported or []) == ["code"]
    assert str(third.authorization_endpoint) == "https://cache.example.com/authorize"
    mock_get.assert_called_once()


def test_cache_isolated_by_configuration_class(discovery_document):
    class ProviderOIDCConfiguration(OIDCConfiguration):
        provider_specific: str

    provider_document = {
        **discovery_document,
        "provider_specific": "provider-value",
    }
    response = _response(provider_document)

    with patch("httpx2.get", return_value=response) as mock_get:
        OIDCConfiguration.get_oidc_configuration(
            config_url=CONFIG_URL,
            strict=True,
            timeout_seconds=10,
        )
        provider = ProviderOIDCConfiguration.get_oidc_configuration(
            config_url=CONFIG_URL,
            strict=True,
            timeout_seconds=10,
        )

    assert provider.provider_specific == "provider-value"
    assert mock_get.call_count == 2


def test_failed_discovery_is_not_cached(discovery_document):
    invalid_document = {"issuer": "https://cache.example.com"}
    response = MagicMock(spec=Response)
    response.json.side_effect = [invalid_document, discovery_document]

    with patch("httpx2.get", return_value=response) as mock_get:
        with pytest.raises(ValueError, match="Missing required configuration metadata"):
            OIDCConfiguration.get_oidc_configuration(
                config_url=CONFIG_URL,
                strict=True,
                timeout_seconds=10,
            )

        config = OIDCConfiguration.get_oidc_configuration(
            config_url=CONFIG_URL,
            strict=True,
            timeout_seconds=10,
        )

    assert str(config.issuer) == "https://cache.example.com"
    assert mock_get.call_count == 2
