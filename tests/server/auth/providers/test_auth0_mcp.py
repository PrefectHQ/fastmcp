"""Tests for Auth0 MCP resource server provider."""

from unittest.mock import patch

import httpx2
import pytest

from fastmcp import FastMCP
from fastmcp.server.auth.oidc_proxy import OIDCConfiguration
from fastmcp.server.auth.providers.auth0 import Auth0JWTVerifier, Auth0MCPProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair

TEST_CONFIG_URL = "https://example.us.auth0.com/.well-known/openid-configuration"
TEST_BASE_URL = "http://127.0.0.1:8000"
TEST_ISSUER = "https://example.us.auth0.com/"
TEST_JWKS_URI = "https://example.us.auth0.com/.well-known/jwks.json"


@pytest.fixture
def valid_oidc_configuration_dict():
    return {
        "issuer": TEST_ISSUER,
        "authorization_endpoint": "https://example.us.auth0.com/authorize",
        "token_endpoint": "https://example.us.auth0.com/oauth/token",
        "jwks_uri": TEST_JWKS_URI,
        "registration_endpoint": "https://example.us.auth0.com/oidc/register",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }


class TestAuth0JWTVerifier:
    def test_extract_scopes_includes_permissions(self):
        verifier = Auth0JWTVerifier(
            jwks_uri=TEST_JWKS_URI,
            issuer=TEST_ISSUER,
        )
        scopes = verifier._extract_scopes(
            {"scope": "openid", "permissions": ["tool:whoami", "tool:greet"]}
        )
        assert scopes == ["openid", "tool:whoami", "tool:greet"]

    def test_extract_scopes_permissions_string(self):
        verifier = Auth0JWTVerifier(
            jwks_uri=TEST_JWKS_URI,
            issuer=TEST_ISSUER,
        )
        scopes = verifier._extract_scopes({"permissions": "tool:whoami tool:greet"})
        assert scopes == ["tool:whoami", "tool:greet"]

    async def test_verify_token_accepts_permissions_as_required_scopes(
        self, rsa_key_pair: RSAKeyPair
    ):
        key_pair = rsa_key_pair
        verifier = Auth0JWTVerifier(
            public_key=key_pair.public_key,
            issuer=TEST_ISSUER,
            required_scopes=["tool:echo"],
        )
        token = key_pair.create_token(
            subject="user_123",
            issuer=TEST_ISSUER,
            additional_claims={"permissions": ["tool:echo"]},
        )

        access_token = await verifier.load_access_token(token)
        assert access_token is not None
        assert access_token.client_id == "user_123"

    async def test_verify_token_rejects_missing_permissions(
        self, rsa_key_pair: RSAKeyPair
    ):
        key_pair = rsa_key_pair
        verifier = Auth0JWTVerifier(
            public_key=key_pair.public_key,
            issuer=TEST_ISSUER,
            required_scopes=["tool:echo"],
        )
        token = key_pair.create_token(
            subject="user_123",
            issuer=TEST_ISSUER,
            additional_claims={"permissions": ["tool:other"]},
        )

        access_token = await verifier.load_access_token(token)
        assert access_token is None


class TestAuth0MCPProviderInit:
    def test_init_from_oidc_discovery(self, valid_oidc_configuration_dict):
        with patch(
            "fastmcp.server.auth.providers.auth0.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            mock_get.return_value = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )

            provider = Auth0MCPProvider(
                config_url=TEST_CONFIG_URL,
                base_url=TEST_BASE_URL,
            )

            mock_get.assert_called_once()
            assert provider.issuer == "https://example.us.auth0.com"
            assert str(provider.base_url) == f"{TEST_BASE_URL}/"
            verifier = provider.token_verifier
            assert isinstance(verifier, Auth0JWTVerifier)
            assert verifier.jwks_uri == TEST_JWKS_URI
            assert verifier.issuer == TEST_ISSUER
            assert len(provider.authorization_servers) == 1
            assert (
                str(provider.authorization_servers[0]).rstrip("/")
                == "https://example.us.auth0.com"
            )

    def test_custom_token_verifier_not_replaced(self, valid_oidc_configuration_dict):
        custom = JWTVerifier(
            jwks_uri=TEST_JWKS_URI,
            issuer=TEST_ISSUER,
            audience="https://custom.example.com/mcp",
        )
        with patch(
            "fastmcp.server.auth.providers.auth0.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            mock_get.return_value = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            provider = Auth0MCPProvider(
                config_url=TEST_CONFIG_URL,
                base_url=TEST_BASE_URL,
                token_verifier=custom,
            )

        assert provider.token_verifier is custom
        assert provider._auto_bind_audience is False


class TestAuth0MCPAudienceBinding:
    def test_audience_binds_on_set_mcp_path(self, valid_oidc_configuration_dict):
        with patch(
            "fastmcp.server.auth.providers.auth0.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            mock_get.return_value = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            provider = Auth0MCPProvider(
                config_url=TEST_CONFIG_URL,
                base_url=TEST_BASE_URL,
            )

        verifier = provider.token_verifier
        assert isinstance(verifier, Auth0JWTVerifier)
        assert verifier.audience is None

        provider.set_mcp_path("/mcp")
        assert verifier.audience == "http://127.0.0.1:8000/mcp"

    def test_audience_respects_resource_base_url(self, valid_oidc_configuration_dict):
        with patch(
            "fastmcp.server.auth.providers.auth0.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            mock_get.return_value = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            provider = Auth0MCPProvider(
                config_url=TEST_CONFIG_URL,
                base_url="https://oauth.example.com",
                resource_base_url="https://api.example.com",
            )

        provider.set_mcp_path("/mcp")
        verifier = provider.token_verifier
        assert isinstance(verifier, Auth0JWTVerifier)
        assert verifier.audience == "https://api.example.com/mcp"

    def test_custom_verifier_audience_not_overwritten(
        self, valid_oidc_configuration_dict
    ):
        custom_audience = "https://other.example.com"
        custom = JWTVerifier(
            jwks_uri=TEST_JWKS_URI,
            issuer=TEST_ISSUER,
            audience=custom_audience,
        )
        with patch(
            "fastmcp.server.auth.providers.auth0.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            mock_get.return_value = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            provider = Auth0MCPProvider(
                config_url=TEST_CONFIG_URL,
                base_url=TEST_BASE_URL,
                token_verifier=custom,
            )
            provider.set_mcp_path("/mcp")

        assert custom.audience == custom_audience

    def test_set_mcp_path_none_binds_to_base_url(self, valid_oidc_configuration_dict):
        with patch(
            "fastmcp.server.auth.providers.auth0.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            mock_get.return_value = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            provider = Auth0MCPProvider(
                config_url=TEST_CONFIG_URL,
                base_url=TEST_BASE_URL,
            )

        provider.set_mcp_path(None)

        verifier = provider.token_verifier
        assert isinstance(verifier, Auth0JWTVerifier)
        assert verifier.audience == "http://127.0.0.1:8000/"

    def test_audience_binds_through_http_app(self, valid_oidc_configuration_dict):
        with patch(
            "fastmcp.server.auth.providers.auth0.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            mock_get.return_value = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            auth = Auth0MCPProvider(
                config_url=TEST_CONFIG_URL,
                base_url=TEST_BASE_URL,
            )
            mcp = FastMCP("test", auth=auth)
            mcp.http_app(path="/mcp")

        verifier = auth.token_verifier
        assert isinstance(verifier, Auth0JWTVerifier)
        assert verifier.audience == "http://127.0.0.1:8000/mcp"


class TestAuth0MCPMetadataForwarding:
    async def test_forwards_authorization_server_metadata(
        self, valid_oidc_configuration_dict, monkeypatch
    ):
        metadata_payload = {
            "issuer": TEST_ISSUER,
            "authorization_endpoint": "https://example.us.auth0.com/authorize",
            "token_endpoint": "https://example.us.auth0.com/oauth/token",
            "registration_endpoint": "https://example.us.auth0.com/oidc/register",
        }

        class DummyResponse:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        class DummyAsyncClient:
            last_url: str | None = None

            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def get(self, url):
                DummyAsyncClient.last_url = url
                return DummyResponse(metadata_payload)

        real_httpx_client = httpx2.AsyncClient

        monkeypatch.setattr(
            "fastmcp.server.auth.providers.auth0.httpx2.AsyncClient",
            DummyAsyncClient,
        )

        with patch(
            "fastmcp.server.auth.providers.auth0.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            mock_get.return_value = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            provider = Auth0MCPProvider(
                config_url=TEST_CONFIG_URL,
                base_url=TEST_BASE_URL,
            )

        mcp = FastMCP("test", auth=provider)
        app = mcp.http_app()

        async with real_httpx_client(
            transport=httpx2.ASGITransport(app=app),
            base_url=TEST_BASE_URL,
        ) as client:
            response = await client.get("/.well-known/oauth-authorization-server")

        assert response.status_code == 200
        assert response.json() == metadata_payload
        assert (
            DummyAsyncClient.last_url
            == "https://example.us.auth0.com/.well-known/oauth-authorization-server"
        )


class TestAuth0MCPIntegration:
    async def test_unauthenticated_mcp_request_returns_401(
        self, valid_oidc_configuration_dict
    ):
        with patch(
            "fastmcp.server.auth.providers.auth0.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            mock_get.return_value = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            provider = Auth0MCPProvider(
                config_url=TEST_CONFIG_URL,
                base_url=TEST_BASE_URL,
            )

        mcp = FastMCP("test-server", auth=provider)

        @mcp.tool
        def echo(message: str) -> str:
            return message

        app = mcp.http_app()

        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url=TEST_BASE_URL,
        ) as client:
            response = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
                headers={"Content-Type": "application/json"},
            )

        assert response.status_code == 401

    async def test_no_register_proxy_route(self, valid_oidc_configuration_dict):
        with patch(
            "fastmcp.server.auth.providers.auth0.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            mock_get.return_value = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            provider = Auth0MCPProvider(
                config_url=TEST_CONFIG_URL,
                base_url=TEST_BASE_URL,
            )

        mcp = FastMCP("test-server", auth=provider)
        app = mcp.http_app()

        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url=TEST_BASE_URL,
        ) as client:
            response = await client.post(
                "/register",
                json={"client_name": "Test", "redirect_uris": ["http://localhost/cb"]},
                headers={"Content-Type": "application/json"},
            )

        assert response.status_code == 404

    async def test_protected_resource_metadata(self, valid_oidc_configuration_dict):
        with patch(
            "fastmcp.server.auth.providers.auth0.OIDCConfiguration.get_oidc_configuration"
        ) as mock_get:
            mock_get.return_value = OIDCConfiguration.model_validate(
                valid_oidc_configuration_dict
            )
            provider = Auth0MCPProvider(
                config_url=TEST_CONFIG_URL,
                base_url=TEST_BASE_URL,
            )

        mcp = FastMCP("test-server", auth=provider)
        app = mcp.http_app()

        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url=TEST_BASE_URL,
        ) as client:
            response = await client.get("/.well-known/oauth-protected-resource/mcp")

        assert response.status_code == 200
        data = response.json()
        assert data["resource"] == f"{TEST_BASE_URL}/mcp"
        assert data["authorization_servers"] == [TEST_ISSUER]
