"""OAuth provider constructors should expose the ID-JAG feature of their proxy base."""

import inspect

import pytest

from fastmcp.server.auth import IdentityAssertion
from fastmcp.server.auth.providers.auth0 import Auth0Provider
from fastmcp.server.auth.providers.aws import AWSCognitoProvider
from fastmcp.server.auth.providers.azure import AzureProvider
from fastmcp.server.auth.providers.clerk import ClerkProvider
from fastmcp.server.auth.providers.discord import DiscordProvider
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.server.auth.providers.huggingface import HuggingFaceProvider
from fastmcp.server.auth.providers.oci import OCIProvider
from fastmcp.server.auth.providers.workos import WorkOSProvider


@pytest.mark.parametrize(
    "provider",
    [
        Auth0Provider,
        AWSCognitoProvider,
        AzureProvider,
        ClerkProvider,
        DiscordProvider,
        GitHubProvider,
        GoogleProvider,
        HuggingFaceProvider,
        OCIProvider,
        WorkOSProvider,
    ],
)
def test_identity_assertion_is_public_provider_parameter(provider: type) -> None:
    signature = inspect.signature(provider)
    assert "identity_assertion" in signature.parameters
    assert signature.parameters["identity_assertion"].default is None


@pytest.mark.parametrize(
    ("provider", "kwargs"),
    [
        (GoogleProvider, {"client_id": "test", "client_secret": "secret"}),
        (GitHubProvider, {"client_id": "test", "client_secret": "secret"}),
        (DiscordProvider, {"client_id": "test", "client_secret": "secret"}),
        (HuggingFaceProvider, {"client_id": "test", "client_secret": "secret"}),
        (
            ClerkProvider,
            {
                "domain": "clerk.example.test",
                "client_id": "test",
                "client_secret": "secret",
            },
        ),
        (
            WorkOSProvider,
            {
                "client_id": "test",
                "client_secret": "secret",
                "authkit_domain": "https://authkit.example.test",
            },
        ),
        (
            AzureProvider,
            {
                "client_id": "test",
                "client_secret": "secret",
                "tenant_id": "test-tenant",
                "required_scopes": ["read"],
            },
        ),
    ],
)
def test_provider_forwards_identity_assertion(provider: type, kwargs: dict) -> None:
    assertion = IdentityAssertion(trusted_issuers=["https://issuer.example.test"])
    instance = provider(
        **kwargs,
        base_url="https://resource.example.test",
        identity_assertion=assertion,
    )
    assert instance._identity_assertion is assertion
    assert instance._identity_assertion_validator is not None


def test_provider_remains_disabled_by_default() -> None:
    instance = GoogleProvider(
        client_id="test",
        client_secret="secret",
        base_url="https://resource.example.test",
    )
    assert instance._identity_assertion is None
    assert instance._identity_assertion_validator is None


@pytest.mark.parametrize(
    ("provider", "kwargs"),
    [
        (
            Auth0Provider,
            {
                "config_url": "https://auth.example.test/.well-known/openid-configuration",
                "client_id": "test",
                "client_secret": "secret",
                "audience": "test-audience",
            },
        ),
        (
            AWSCognitoProvider,
            {
                "user_pool_id": "us-east-1_test",
                "client_id": "test",
                "client_secret": "secret",
            },
        ),
        (
            OCIProvider,
            {
                "config_url": "https://auth.example.test/.well-known/openid-configuration",
                "client_id": "test",
                "client_secret": "secret",
            },
        ),
    ],
)
def test_oidc_provider_forwards_identity_assertion(
    provider: type, kwargs: dict
) -> None:
    from unittest.mock import patch

    from fastmcp.server.auth.oidc_proxy import OIDCProxy

    assertion = IdentityAssertion(trusted_issuers=["https://issuer.example.test"])
    with patch.object(OIDCProxy, "__init__", return_value=None) as base_init:
        provider(
            **kwargs,
            base_url="https://resource.example.test",
            identity_assertion=assertion,
        )
    assert base_init.call_args.kwargs["identity_assertion"] is assertion
