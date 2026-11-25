"""Dynamic client registrations can expire when unused (#6036)."""

from unittest.mock import patch

import pytest
from key_value.aio.stores.memory import MemoryStore

from fastmcp.server.auth.oauth_proxy import OAuthProxy

from .test_single_use import (
    as_refresh_token,
    non_rotating_upstream,
    register_client,
    store_code,
)


def make_proxy(jwt_verifier, ttl: int | None) -> OAuthProxy:
    proxy = OAuthProxy(
        upstream_authorization_endpoint="https://idp.example.com/authorize",
        upstream_token_endpoint="https://idp.example.com/token",
        upstream_client_id="test-client",
        upstream_client_secret="test-secret",
        token_verifier=jwt_verifier,
        base_url="https://proxy.example.com",
        jwt_signing_key="test-secret-key",
        client_storage=MemoryStore(),
        client_registration_ttl_seconds=ttl,
    )
    proxy.set_mcp_path("/mcp")
    return proxy


def _client_puts(proxy: OAuthProxy):
    return patch.object(proxy._client_store, "put", wraps=proxy._client_store.put)


class TestClientRegistrationTTL:
    async def test_default_registration_never_expires(self, jwt_verifier):
        proxy = make_proxy(jwt_verifier, None)
        with _client_puts(proxy) as put:
            await register_client(proxy)
        assert put.call_args.kwargs["ttl"] is None

    async def test_registration_is_stored_with_ttl(self, jwt_verifier):
        proxy = make_proxy(jwt_verifier, 3600)
        with _client_puts(proxy) as put:
            await register_client(proxy)
        assert put.call_args.kwargs["ttl"] == 3600

    async def test_token_exchange_and_refresh_renew_registration(self, jwt_verifier):
        proxy = make_proxy(jwt_verifier, 3600)
        client = await register_client(proxy)

        with _client_puts(proxy) as put:
            tokens = await proxy.exchange_authorization_code(
                client, await store_code(proxy)
            )
        assert put.call_count == 1
        assert put.call_args.kwargs["key"] == "test-client"
        assert put.call_args.kwargs["ttl"] == 3600

        assert tokens.refresh_token is not None
        with (
            _client_puts(proxy) as put,
            patch.object(
                proxy,
                "_create_upstream_oauth_client",
                return_value=non_rotating_upstream(),
            ),
        ):
            await proxy.exchange_refresh_token(
                client, as_refresh_token(tokens.refresh_token), ["read"]
            )
        assert put.call_count == 1
        assert put.call_args.kwargs["ttl"] == 3600

    async def test_no_renewal_without_ttl(self, jwt_verifier):
        proxy = make_proxy(jwt_verifier, None)
        client = await register_client(proxy)
        with _client_puts(proxy) as put:
            await proxy.exchange_authorization_code(client, await store_code(proxy))
        assert put.call_count == 0

    @pytest.mark.parametrize("ttl", [0, -1])
    def test_ttl_must_be_positive(self, jwt_verifier, ttl):
        with pytest.raises(ValueError):
            make_proxy(jwt_verifier, ttl)
