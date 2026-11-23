"""A rotated refresh token presented again ends its grant (OAuth 2.1 §4.3.1)."""

import time
from unittest.mock import patch

import pytest
from mcp.server.auth.provider import TokenError

from fastmcp.server.auth.oauth_proxy import proxy as proxy_module
from fastmcp.server.auth.oauth_proxy.models import _hash_token

from .test_single_use import (
    as_refresh_token,
    make_proxy,
    non_rotating_upstream,
    register_client,
    store_code,
)


async def _rotate_once(proxy):
    client = await register_client(proxy)
    tokens = await proxy.exchange_authorization_code(client, await store_code(proxy))
    assert tokens.refresh_token is not None
    with patch.object(
        proxy, "_create_upstream_oauth_client", return_value=non_rotating_upstream()
    ):
        rotated = await proxy.exchange_refresh_token(
            client, as_refresh_token(tokens.refresh_token), ["read"]
        )
    return client, tokens.refresh_token, rotated


async def _grant_alive(proxy, access_token: str) -> bool:
    """Whether the grant behind a FastMCP access token still exists."""
    jti = proxy.jwt_issuer.verify_token(access_token)["jti"]
    mapping = await proxy._jti_mapping_store.get(key=jti)
    if mapping is None:
        return False
    return (
        await proxy._upstream_token_store.get(key=mapping.upstream_token_id) is not None
    )


def _age_rotation(seconds: float):
    """Pretend the rotation happened `seconds` ago."""
    real_time = time.time

    return patch.object(
        proxy_module.time, "time", side_effect=lambda: real_time() + seconds
    )


class TestRefreshTokenReuse:
    async def test_reuse_after_grace_ends_the_grant(self, jwt_verifier):
        proxy = make_proxy(jwt_verifier)
        client, old_refresh, rotated = await _rotate_once(proxy)
        assert rotated.refresh_token is not None
        assert await _grant_alive(proxy, rotated.access_token)

        with _age_rotation(proxy_module._REFRESH_REUSE_GRACE_SECONDS + 1):
            assert await proxy.load_refresh_token(client, old_refresh) is None

        # The successors issued from the stolen token no longer work.
        assert not await _grant_alive(proxy, rotated.access_token)
        with patch.object(
            proxy, "_create_upstream_oauth_client", return_value=non_rotating_upstream()
        ):
            with pytest.raises(TokenError) as exc_info:
                await proxy.exchange_refresh_token(
                    client, as_refresh_token(rotated.refresh_token), ["read"]
                )
        assert exc_info.value.error == "invalid_grant"

    async def test_reuse_within_grace_is_only_rejected(self, jwt_verifier):
        proxy = make_proxy(jwt_verifier)
        client, old_refresh, rotated = await _rotate_once(proxy)
        assert rotated.refresh_token is not None

        assert await proxy.load_refresh_token(client, old_refresh) is None

        assert await _grant_alive(proxy, rotated.access_token)
        assert await proxy.load_refresh_token(client, rotated.refresh_token)

    async def test_reuse_by_another_client_does_not_end_the_grant(self, jwt_verifier):
        proxy = make_proxy(jwt_verifier)
        client, old_refresh, rotated = await _rotate_once(proxy)
        other = client.model_copy(update={"client_id": "other-client"})

        with _age_rotation(proxy_module._REFRESH_REUSE_GRACE_SECONDS + 1):
            assert await proxy.load_refresh_token(other, old_refresh) is None

        assert await _grant_alive(proxy, rotated.access_token)
        assert await proxy._rotated_refresh_store.get(key=_hash_token(old_refresh))

    async def test_unknown_refresh_token_is_just_rejected(self, jwt_verifier):
        proxy = make_proxy(jwt_verifier)
        client, _, rotated = await _rotate_once(proxy)

        assert await proxy.load_refresh_token(client, "never-issued") is None
        assert await _grant_alive(proxy, rotated.access_token)
