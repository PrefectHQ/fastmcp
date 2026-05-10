"""Tests for first-party auth plugin wrappers."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from fastmcp import FastMCP
from fastmcp.server.auth.oidc_proxy import OIDCConfiguration
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.server.plugins.auth.auth0 import Auth0Auth
from fastmcp.server.plugins.auth.auth0.provider import Auth0Provider
from fastmcp.server.plugins.auth.authkit import AuthKitAuth
from fastmcp.server.plugins.auth.authkit.provider import AuthKitProvider
from fastmcp.server.plugins.auth.aws import AWSCognitoAuth
from fastmcp.server.plugins.auth.aws.provider import AWSCognitoProvider
from fastmcp.server.plugins.auth.azure import AzureAuth
from fastmcp.server.plugins.auth.azure.provider import AzureProvider
from fastmcp.server.plugins.auth.clerk import ClerkAuth
from fastmcp.server.plugins.auth.clerk.provider import ClerkProvider
from fastmcp.server.plugins.auth.descope import DescopeAuth
from fastmcp.server.plugins.auth.descope.provider import DescopeProvider
from fastmcp.server.plugins.auth.discord import DiscordAuth
from fastmcp.server.plugins.auth.discord.provider import DiscordProvider
from fastmcp.server.plugins.auth.github import GitHubAuth
from fastmcp.server.plugins.auth.github.provider import GitHubProvider
from fastmcp.server.plugins.auth.google import GoogleAuth
from fastmcp.server.plugins.auth.google.provider import GoogleProvider
from fastmcp.server.plugins.auth.keycloak import KeycloakAuth
from fastmcp.server.plugins.auth.keycloak.provider import KeycloakAuthProvider
from fastmcp.server.plugins.auth.oci import OCIAuth
from fastmcp.server.plugins.auth.oci.provider import OCIProvider
from fastmcp.server.plugins.auth.propelauth import PropelAuth
from fastmcp.server.plugins.auth.propelauth.provider import PropelAuthProvider
from fastmcp.server.plugins.auth.scalekit import ScalekitAuth
from fastmcp.server.plugins.auth.scalekit.provider import ScalekitProvider
from fastmcp.server.plugins.auth.supabase import SupabaseAuth
from fastmcp.server.plugins.auth.supabase.provider import SupabaseProvider
from fastmcp.server.plugins.auth.workos import WorkOSAuth
from fastmcp.server.plugins.auth.workos.provider import WorkOSProvider


def _verifier() -> StaticTokenVerifier:
    return StaticTokenVerifier(tokens={"t": {"client_id": "c", "scopes": []}})


def _oidc_config() -> OIDCConfiguration:
    return OIDCConfiguration.model_validate(
        {
            "issuer": "https://idp.example.com",
            "authorization_endpoint": "https://idp.example.com/authorize",
            "token_endpoint": "https://idp.example.com/token",
            "jwks_uri": "https://idp.example.com/jwks.json",
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
        }
    )


@pytest.fixture(autouse=True)
def _mock_oidc_discovery():
    with patch(
        "fastmcp.server.auth.oidc_proxy.OIDCConfiguration.get_oidc_configuration",
        return_value=_oidc_config(),
    ):
        yield


PROVIDER_CASES: list[tuple[type, type, dict[str, Any], type]] = [
    (
        Auth0Auth,
        Auth0Auth.Config,
        {
            "config_url": "https://idp.example.com/.well-known/openid-configuration",
            "client_id": "client",
            "client_secret": "secret",
            "audience": "audience",
            "base_url": "https://mcp.example.com",
        },
        Auth0Provider,
    ),
    (
        AuthKitAuth,
        AuthKitAuth.Config,
        {
            "authkit_domain": "https://example.authkit.app",
            "base_url": "https://mcp.example.com",
        },
        AuthKitProvider,
    ),
    (
        AWSCognitoAuth,
        AWSCognitoAuth.Config,
        {
            "user_pool_id": "us-east-1_abc",
            "client_id": "client",
            "client_secret": "secret",
            "aws_region": "us-east-1",
            "base_url": "https://mcp.example.com",
        },
        AWSCognitoProvider,
    ),
    (
        AzureAuth,
        AzureAuth.Config,
        {
            "client_id": "client",
            "client_secret": "secret",
            "tenant_id": "tenant",
            "required_scopes": ["read"],
            "base_url": "https://mcp.example.com",
        },
        AzureProvider,
    ),
    (
        ClerkAuth,
        ClerkAuth.Config,
        {
            "domain": "example.clerk.accounts.dev",
            "client_id": "client",
            "client_secret": "secret",
            "base_url": "https://mcp.example.com",
        },
        ClerkProvider,
    ),
    (
        DescopeAuth,
        DescopeAuth.Config,
        {
            "config_url": "https://api.descope.com/v1/apps/agentic/P123/M456/.well-known/openid-configuration",
            "base_url": "https://mcp.example.com",
        },
        DescopeProvider,
    ),
    (
        DiscordAuth,
        DiscordAuth.Config,
        {
            "client_id": "client",
            "client_secret": "secret",
            "base_url": "https://mcp.example.com",
        },
        DiscordProvider,
    ),
    (
        GitHubAuth,
        GitHubAuth.Config,
        {
            "client_id": "client",
            "client_secret": "secret",
            "base_url": "https://mcp.example.com",
        },
        GitHubProvider,
    ),
    (
        GoogleAuth,
        GoogleAuth.Config,
        {
            "client_id": "client",
            "client_secret": "secret",
            "base_url": "https://mcp.example.com",
        },
        GoogleProvider,
    ),
    (
        KeycloakAuth,
        KeycloakAuth.Config,
        {
            "realm_url": "https://keycloak.example.com/realms/main",
            "base_url": "https://mcp.example.com",
        },
        KeycloakAuthProvider,
    ),
    (
        OCIAuth,
        OCIAuth.Config,
        {
            "config_url": "https://idp.example.com/.well-known/openid-configuration",
            "client_id": "client",
            "client_secret": "secret",
            "base_url": "https://mcp.example.com",
        },
        OCIProvider,
    ),
    (
        PropelAuth,
        PropelAuth.Config,
        {
            "auth_url": "https://auth.example.com",
            "introspection_client_id": "client",
            "introspection_client_secret": "secret",
            "base_url": "https://mcp.example.com",
        },
        PropelAuthProvider,
    ),
    (
        ScalekitAuth,
        ScalekitAuth.Config,
        {
            "environment_url": "https://env.scalekit.com",
            "resource_id": "res_123",
            "base_url": "https://mcp.example.com",
        },
        ScalekitProvider,
    ),
    (
        SupabaseAuth,
        SupabaseAuth.Config,
        {
            "project_url": "https://abc123.supabase.co",
            "base_url": "https://mcp.example.com",
        },
        SupabaseProvider,
    ),
    (
        WorkOSAuth,
        WorkOSAuth.Config,
        {
            "client_id": "client",
            "client_secret": "secret",
            "authkit_domain": "https://example.authkit.app",
            "base_url": "https://mcp.example.com",
        },
        WorkOSProvider,
    ),
]


def _plugin_kwargs(plugin_cls: type) -> dict[str, Any]:
    if plugin_cls in {
        AuthKitAuth,
        DescopeAuth,
        KeycloakAuth,
        ScalekitAuth,
        SupabaseAuth,
    }:
        return {"token_verifier": _verifier()}
    return {}


class TestAuthProviderPlugins:
    @pytest.mark.parametrize(
        ("plugin_cls", "config_cls", "config", "provider_cls"), PROVIDER_CASES
    )
    def test_config_generic_binding(self, plugin_cls, config_cls, config, provider_cls):
        assert plugin_cls._config_cls is config_cls
        assert plugin_cls.Config is config_cls

    @pytest.mark.parametrize(
        ("plugin_cls", "config_cls", "config", "provider_cls"), PROVIDER_CASES
    )
    def test_default_config_instantiable(
        self, plugin_cls, config_cls, config, provider_cls
    ):
        assert config_cls()

    @pytest.mark.parametrize(
        ("plugin_cls", "config_cls", "config", "provider_cls"), PROVIDER_CASES
    )
    def test_unknown_config_key_rejected(
        self, plugin_cls, config_cls, config, provider_cls
    ):
        with pytest.raises((ValidationError, Exception), match="forbid|extra"):
            config_cls(not_a_real_option=True)

    @pytest.mark.parametrize(
        ("plugin_cls", "config_cls", "config", "provider_cls"), PROVIDER_CASES
    )
    def test_auth_builds_provider(self, plugin_cls, config_cls, config, provider_cls):
        auth = plugin_cls(config, **_plugin_kwargs(plugin_cls)).auth()

        assert isinstance(auth, provider_cls)

    @pytest.mark.parametrize(
        ("plugin_cls", "config_cls", "config", "provider_cls"), PROVIDER_CASES
    )
    def test_plugin_installs_as_server_auth(
        self, plugin_cls, config_cls, config, provider_cls
    ):
        plugin = plugin_cls(config, **_plugin_kwargs(plugin_cls))

        mcp = FastMCP("t", plugins=[plugin])

        assert isinstance(mcp.auth, provider_cls)

    @pytest.mark.parametrize("missing", ["project_url", "base_url"])
    def test_required_fields_checked_when_auth_builds(self, missing: str):
        config = {
            "project_url": "https://abc123.supabase.co",
            "base_url": "https://mcp.example.com",
        }
        del config[missing]

        plugin = SupabaseAuth(config, token_verifier=_verifier())

        with pytest.raises(ValueError, match=missing):
            plugin.auth()

    def test_supabase_passthroughs_config_and_python_verifier(self):
        verifier = _verifier()
        plugin = SupabaseAuth(
            SupabaseAuth.Config(
                project_url="https://abc123.supabase.co",
                base_url="https://mcp.example.com",
                required_scopes=["read"],
                scopes_supported=["read", "write"],
                resource_name="Example MCP",
            ),
            token_verifier=verifier,
        )

        auth = plugin.auth()

        assert isinstance(auth, SupabaseProvider)
        assert auth.token_verifier is verifier
        assert str(auth.base_url).rstrip("/") == "https://mcp.example.com"
        assert auth._scopes_supported == ["read", "write"]
        assert auth.resource_name == "Example MCP"
