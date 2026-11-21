"""Authorization codes and refresh tokens are redeemable once, even concurrently."""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.provider import AuthorizationCode, TokenError
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from fastmcp.server.auth.auth import RefreshToken
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.oauth_proxy.models import ClientCode, _hash_token
from fastmcp.server.auth.oauth_proxy.upstream import OAuthError

CODE = "one-time-code"


def make_proxy(jwt_verifier) -> OAuthProxy:
    proxy = OAuthProxy(
        upstream_authorization_endpoint="https://idp.example.com/authorize",
        upstream_token_endpoint="https://idp.example.com/token",
        upstream_client_id="test-client",
        upstream_client_secret="test-secret",
        token_verifier=jwt_verifier,
        base_url="https://proxy.example.com",
        jwt_signing_key="test-secret-key",
        client_storage=MemoryStore(),
    )
    proxy.set_mcp_path("/mcp")
    return proxy


async def register_client(proxy: OAuthProxy) -> OAuthClientInformationFull:
    client = OAuthClientInformationFull(
        client_id="test-client",
        client_secret="test-secret",
        redirect_uris=[AnyUrl("http://localhost:12345/callback")],
    )
    await proxy.register_client(client)
    return client


async def store_code(proxy: OAuthProxy) -> AuthorizationCode:
    now = time.time()
    await proxy._code_store.put(
        key=CODE,
        value=ClientCode(
            code=CODE,
            client_id="test-client",
            redirect_uri="http://localhost:12345/callback",
            code_challenge="test-challenge",
            code_challenge_method="S256",
            scopes=["read"],
            idp_tokens={
                "access_token": "upstream-access",
                "refresh_token": "upstream-refresh",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
            expires_at=now + 300,
            created_at=now,
        ),
    )
    return AuthorizationCode(
        code=CODE,
        scopes=["read"],
        expires_at=now + 300,
        client_id="test-client",
        code_challenge="test-challenge",
        redirect_uri=AnyUrl("http://localhost:12345/callback"),
        redirect_uri_provided_explicitly=True,
    )


def as_refresh_token(token: str) -> RefreshToken:
    return RefreshToken(token=token, client_id="test-client", scopes=["read"])


def non_rotating_upstream(barrier: asyncio.Barrier | None = None) -> AsyncMock:
    """Upstream that keeps the same refresh token, like Cognito."""

    async def refresh_token(**_):
        if barrier is not None:
            await barrier.wait()
        return {
            "access_token": "refreshed-upstream-access",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": "upstream-refresh",
        }

    client = AsyncMock()
    client.refresh_token = AsyncMock(side_effect=refresh_token)
    return client


class TestAuthorizationCodeSingleUse:
    async def test_concurrent_exchanges_redeem_code_once(self, jwt_verifier):
        proxy = make_proxy(jwt_verifier)
        client = await register_client(proxy)
        code = await store_code(proxy)

        # Hold both requests after they read the code so they overlap.
        barrier = asyncio.Barrier(2)
        real_get = proxy._code_store.get

        async def get_then_wait(*args, **kwargs):
            value = await real_get(*args, **kwargs)
            await barrier.wait()
            return value

        with patch.object(proxy._code_store, "get", side_effect=get_then_wait):
            results = await asyncio.gather(
                proxy.exchange_authorization_code(client, code),
                proxy.exchange_authorization_code(client, code),
                return_exceptions=True,
            )

        issued = [r for r in results if not isinstance(r, BaseException)]
        rejected = [r for r in results if isinstance(r, TokenError)]
        assert len(issued) == 1
        assert len(rejected) == 1
        assert rejected[0].error == "invalid_grant"

    async def test_code_is_rejected_after_redemption(self, jwt_verifier):
        proxy = make_proxy(jwt_verifier)
        client = await register_client(proxy)
        code = await store_code(proxy)

        await proxy.exchange_authorization_code(client, code)

        with pytest.raises(TokenError) as exc_info:
            await proxy.exchange_authorization_code(client, code)
        assert exc_info.value.error == "invalid_grant"


class TestRefreshTokenSingleUse:
    async def _issue(self, proxy: OAuthProxy) -> tuple[OAuthClientInformationFull, str]:
        client = await register_client(proxy)
        tokens = await proxy.exchange_authorization_code(
            client, await store_code(proxy)
        )
        assert tokens.refresh_token is not None
        return client, tokens.refresh_token

    async def test_concurrent_refreshes_rotate_once(self, jwt_verifier):
        proxy = make_proxy(jwt_verifier)
        client, refresh_token = await self._issue(proxy)
        upstream = non_rotating_upstream(asyncio.Barrier(2))

        with patch.object(
            proxy, "_create_upstream_oauth_client", return_value=upstream
        ):
            results = await asyncio.gather(
                proxy.exchange_refresh_token(
                    client, as_refresh_token(refresh_token), ["read"]
                ),
                proxy.exchange_refresh_token(
                    client, as_refresh_token(refresh_token), ["read"]
                ),
                return_exceptions=True,
            )

        issued = [r for r in results if not isinstance(r, BaseException)]
        rejected = [r for r in results if isinstance(r, TokenError)]
        assert len(issued) == 1
        assert len(rejected) == 1
        assert rejected[0].error == "invalid_grant"

        # Only the winner's successor refresh token is redeemable.
        assert issued[0].refresh_token is not None
        assert await proxy.load_refresh_token(client, issued[0].refresh_token)
        assert await proxy.load_refresh_token(client, refresh_token) is None

    async def test_refresh_token_is_rejected_after_rotation(self, jwt_verifier):
        proxy = make_proxy(jwt_verifier)
        client, refresh_token = await self._issue(proxy)

        with patch.object(
            proxy,
            "_create_upstream_oauth_client",
            return_value=non_rotating_upstream(),
        ):
            await proxy.exchange_refresh_token(
                client, as_refresh_token(refresh_token), ["read"]
            )
            with pytest.raises(TokenError) as exc_info:
                await proxy.exchange_refresh_token(
                    client, as_refresh_token(refresh_token), ["read"]
                )
        assert exc_info.value.error == "invalid_grant"

    async def test_failed_upstream_refresh_keeps_refresh_token(self, jwt_verifier):
        """A transient upstream failure issues nothing, so the client can retry."""
        proxy = make_proxy(jwt_verifier)
        client, refresh_token = await self._issue(proxy)
        failing = AsyncMock()
        failing.refresh_token = AsyncMock(
            side_effect=OAuthError(error="temporarily_unavailable")
        )

        with (
            patch.object(proxy, "_create_upstream_oauth_client", return_value=failing),
            pytest.raises(OAuthError),
        ):
            await proxy.exchange_refresh_token(
                client, as_refresh_token(refresh_token), ["read"]
            )

        assert await proxy._refresh_token_store.get(key=_hash_token(refresh_token))
        with patch.object(
            proxy,
            "_create_upstream_oauth_client",
            return_value=non_rotating_upstream(),
        ):
            retried = await proxy.exchange_refresh_token(
                client, as_refresh_token(refresh_token), ["read"]
            )
        assert retried.refresh_token is not None
