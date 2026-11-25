"""Provider-level required_scopes must survive a custom token_verifier."""

from collections.abc import Callable
from unittest.mock import patch

import httpx2
import pytest

from fastmcp import FastMCP
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.oidc_proxy import OIDCConfiguration
from fastmcp.server.auth.providers.auth0 import Auth0MCPProvider
from fastmcp.server.auth.providers.descope import DescopeProvider
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.server.auth.providers.keycloak import KeycloakAuthProvider
from fastmcp.server.auth.providers.scalekit import ScalekitProvider
from fastmcp.server.auth.providers.supabase import SupabaseProvider
from fastmcp.server.auth.providers.workos import AuthKitProvider

BASE_URL = "https://api.example.com"
AUTH0_ISSUER = "https://example.us.auth0.com/"


def custom_verifier() -> StaticTokenVerifier:
    return StaticTokenVerifier(
        tokens={
            "read-token": {"client_id": "reader", "scopes": ["read"]},
            "admin-token": {"client_id": "admin", "scopes": ["read", "admin"]},
        }
    )


def auth0_mcp(**kwargs) -> Auth0MCPProvider:
    config = OIDCConfiguration.model_validate(
        {
            "issuer": AUTH0_ISSUER,
            "authorization_endpoint": f"{AUTH0_ISSUER}authorize",
            "token_endpoint": f"{AUTH0_ISSUER}oauth/token",
            "jwks_uri": f"{AUTH0_ISSUER}.well-known/jwks.json",
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
        }
    )
    with patch(
        "fastmcp.server.auth.providers.auth0.OIDCConfiguration.get_oidc_configuration",
        return_value=config,
    ):
        return Auth0MCPProvider(
            config_url=f"{AUTH0_ISSUER}.well-known/openid-configuration",
            base_url=BASE_URL,
            **kwargs,
        )


PROVIDERS: dict[str, Callable[..., RemoteAuthProvider]] = {
    "authkit": lambda **kw: AuthKitProvider(
        authkit_domain="https://test.authkit.app", base_url=BASE_URL, **kw
    ),
    "supabase": lambda **kw: SupabaseProvider(
        project_url="https://abc.supabase.co", base_url=BASE_URL, **kw
    ),
    "keycloak": lambda **kw: KeycloakAuthProvider(
        realm_url="https://kc.example.com/realms/test", base_url=BASE_URL, **kw
    ),
    "scalekit": lambda **kw: ScalekitProvider(
        environment_url="https://env.scalekit.com",
        resource_id="res_1",
        base_url=BASE_URL,
        **kw,
    ),
    "descope": lambda **kw: DescopeProvider(
        config_url="https://api.descope.com/v1/apps/P2abc/.well-known/openid-configuration",
        base_url=BASE_URL,
        **kw,
    ),
    "auth0_mcp": auth0_mcp,
}


@pytest.mark.parametrize("name", PROVIDERS)
def test_custom_verifier_keeps_provider_required_scopes(name: str):
    provider = PROVIDERS[name](
        required_scopes=["admin"], token_verifier=custom_verifier()
    )
    assert provider.required_scopes == ["admin"]


@pytest.mark.parametrize("name", PROVIDERS)
def test_custom_verifier_without_required_scopes_uses_verifier(name: str):
    provider = PROVIDERS[name](token_verifier=custom_verifier())
    assert provider.required_scopes == []


def test_keycloak_openid_default_stays_on_default_verifier():
    provider = KeycloakAuthProvider(
        realm_url="https://kc.example.com/realms/test", base_url=BASE_URL
    )
    assert provider.required_scopes == ["openid"]


@pytest.mark.parametrize("name", PROVIDERS)
async def test_token_missing_provider_scope_is_rejected(name: str):
    provider = PROVIDERS[name](
        required_scopes=["admin"], token_verifier=custom_verifier()
    )
    app = FastMCP("test", auth=provider).http_app()
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"},
        },
    }
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url=BASE_URL
        ) as client:
            denied = await client.post(
                "/mcp",
                json=body,
                headers={**headers, "Authorization": "Bearer read-token"},
            )
            allowed = await client.post(
                "/mcp",
                json=body,
                headers={**headers, "Authorization": "Bearer admin-token"},
            )
    assert denied.status_code == 403
    assert allowed.status_code == 200
