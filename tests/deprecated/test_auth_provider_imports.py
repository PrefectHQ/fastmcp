"""Test that deprecated auth provider import paths still work."""

from __future__ import annotations

import importlib
import sys
import warnings

import pytest

from fastmcp.exceptions import FastMCPDeprecationWarning
from fastmcp.utilities.tests import temporary_settings

AUTH_PROVIDER_SHIMS = [
    (
        "auth0",
        "fastmcp.server.plugins.auth.auth0.provider",
        ("Auth0Provider",),
    ),
    (
        "aws",
        "fastmcp.server.plugins.auth.aws.provider",
        ("AWSCognitoProvider", "AWSCognitoTokenVerifier"),
    ),
    (
        "azure",
        "fastmcp.server.plugins.auth.azure.provider",
        ("AzureJWTVerifier", "AzureProvider", "EntraOBOToken"),
    ),
    (
        "clerk",
        "fastmcp.server.plugins.auth.clerk.provider",
        ("ClerkProvider", "ClerkTokenVerifier"),
    ),
    (
        "descope",
        "fastmcp.server.plugins.auth.descope.provider",
        ("DescopeProvider",),
    ),
    (
        "discord",
        "fastmcp.server.plugins.auth.discord.provider",
        ("DiscordProvider", "DiscordTokenVerifier"),
    ),
    (
        "github",
        "fastmcp.server.plugins.auth.github.provider",
        ("GitHubProvider", "GitHubTokenVerifier"),
    ),
    (
        "google",
        "fastmcp.server.plugins.auth.google.provider",
        ("GoogleProvider", "GoogleTokenVerifier"),
    ),
    (
        "keycloak",
        "fastmcp.server.plugins.auth.keycloak.provider",
        ("KeycloakAuthProvider",),
    ),
    (
        "oci",
        "fastmcp.server.plugins.auth.oci.provider",
        ("OCIProvider",),
    ),
    (
        "propelauth",
        "fastmcp.server.plugins.auth.propelauth.provider",
        ("PropelAuthProvider", "PropelAuthTokenIntrospectionOverrides"),
    ),
    (
        "scalekit",
        "fastmcp.server.plugins.auth.scalekit.provider",
        ("ScalekitProvider",),
    ),
    (
        "supabase",
        "fastmcp.server.plugins.auth.supabase.provider",
        ("SupabaseProvider",),
    ),
    (
        "workos",
        "fastmcp.server.plugins.auth.workos.provider",
        ("WorkOSProvider", "WorkOSTokenVerifier"),
    ),
    (
        "workos",
        "fastmcp.server.plugins.auth.authkit.provider",
        ("AuthKitProvider",),
    ),
]


@pytest.mark.parametrize(
    ("legacy_name", "canonical_module_name", "export_names"),
    AUTH_PROVIDER_SHIMS,
)
def test_deprecated_auth_provider_imports_still_work(
    legacy_name: str,
    canonical_module_name: str,
    export_names: tuple[str, ...],
):
    legacy_module_name = f"fastmcp.server.auth.providers.{legacy_name}"
    canonical_module = importlib.import_module(canonical_module_name)

    sys.modules.pop(legacy_module_name, None)

    with temporary_settings(deprecation_warnings=True):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            legacy_module = importlib.import_module(legacy_module_name)

    fastmcp_warns = [
        w for w in caught if issubclass(w.category, FastMCPDeprecationWarning)
    ]
    assert any("fastmcp.server.plugins.auth" in str(w.message) for w in fastmcp_warns)

    for export_name in export_names:
        assert getattr(legacy_module, export_name) is getattr(
            canonical_module, export_name
        )


@pytest.mark.parametrize(
    "legacy_name",
    sorted({legacy_name for legacy_name, _, _ in AUTH_PROVIDER_SHIMS}),
)
def test_deprecated_auth_provider_imports_are_silent_when_disabled(
    legacy_name: str,
):
    legacy_module_name = f"fastmcp.server.auth.providers.{legacy_name}"

    sys.modules.pop(legacy_module_name, None)

    with temporary_settings(deprecation_warnings=False):
        with warnings.catch_warnings():
            warnings.simplefilter("error", FastMCPDeprecationWarning)
            importlib.import_module(legacy_module_name)
