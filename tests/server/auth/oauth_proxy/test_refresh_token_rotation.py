import asyncio
import secrets
import time
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import httpx2
import pytest
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.provider import TokenError
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from starlette.applications import Starlette

from fastmcp import FastMCP
from fastmcp.server.auth.auth import AccessToken, TokenVerifier
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.oauth_proxy.models import (
    JTIMapping,
    RefreshTokenMetadata,
    UpstreamTokenSet,
    _hash_token,
)


class AcceptingVerifier(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        return AccessToken(
            token=token,
            client_id="upstream-client",
            scopes=["read"],
            expires_at=int(time.time()) + 3600,
        )


@dataclass
class RotationSetup:
    proxy: OAuthProxy
    app: Starlette
    refresh_token: str
    upstream_client: AsyncMock


async def build_rotation_setup(
    *, grace_period: int = 0, upstream_delay: float = 0
) -> RotationSetup:
    proxy = OAuthProxy(
        upstream_authorization_endpoint="https://idp.example.com/authorize",
        upstream_token_endpoint="https://idp.example.com/token",
        upstream_client_id="upstream-client",
        upstream_client_secret="upstream-secret",
        token_verifier=AcceptingVerifier(),
        base_url="https://proxy.example.com",
        jwt_signing_key="test-only-signing-key",
        client_storage=MemoryStore(),
        refresh_token_grace_period_seconds=grace_period,
    )
    app = FastMCP("refresh rotation", auth=proxy).http_app()
    await proxy.register_client(
        OAuthClientInformationFull(
            client_id="mcp-client",
            client_secret="mcp-secret",
            redirect_uris=[AnyUrl("http://localhost/callback")],
            grant_types=["authorization_code", "refresh_token"],
        )
    )

    now = time.time()
    ttl = 3600
    upstream_token_id = secrets.token_urlsafe(16)
    refresh_jti = secrets.token_urlsafe(16)
    refresh_token = proxy.jwt_issuer.issue_refresh_token(
        client_id="mcp-client",
        scopes=["read"],
        jti=refresh_jti,
        expires_in=ttl,
    )
    await proxy._upstream_token_store.put(
        key=upstream_token_id,
        value=UpstreamTokenSet(
            upstream_token_id=upstream_token_id,
            access_token="upstream-access",
            refresh_token="upstream-refresh",
            refresh_token_expires_at=now + ttl,
            expires_at=now + ttl,
            token_type="Bearer",
            scope="read",
            client_id="mcp-client",
            created_at=now,
        ),
        ttl=ttl,
    )
    await proxy._jti_mapping_store.put(
        key=refresh_jti,
        value=JTIMapping(
            jti=refresh_jti,
            upstream_token_id=upstream_token_id,
            created_at=now,
        ),
        ttl=ttl,
    )
    await proxy._refresh_token_store.put(
        key=_hash_token(refresh_token),
        value=RefreshTokenMetadata(
            client_id="mcp-client",
            scopes=["read"],
            expires_at=int(now) + ttl,
            created_at=now,
        ),
        ttl=ttl,
    )

    async def refresh_upstream(*args, **kwargs):
        if upstream_delay:
            await asyncio.sleep(upstream_delay)
        return {
            "access_token": "refreshed-upstream-access",
            "refresh_token": "upstream-refresh",
            "expires_in": ttl,
            "token_type": "Bearer",
            "scope": "read",
        }

    upstream_client = AsyncMock()
    upstream_client.refresh_token = AsyncMock(side_effect=refresh_upstream)
    upstream_client.aclose = AsyncMock()
    return RotationSetup(
        proxy=proxy,
        app=app,
        refresh_token=refresh_token,
        upstream_client=upstream_client,
    )


async def post_refresh(
    setup: RotationSetup, refresh_token: str, *, scope: str = "read"
) -> httpx2.Response:
    transport = httpx2.ASGITransport(app=setup.app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="https://proxy.example.com"
    ) as client:
        return await client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": "mcp-client",
                "client_secret": "mcp-secret",
                "scope": scope,
            },
        )


async def test_refresh_retry_is_rejected_by_default():
    setup = await build_rotation_setup()
    with patch(
        "fastmcp.server.auth.oauth_proxy.proxy.AsyncOAuth2Client",
        return_value=setup.upstream_client,
    ):
        first = await post_refresh(setup, setup.refresh_token)
        retry = await post_refresh(setup, setup.refresh_token)

    assert first.status_code == 200
    assert retry.status_code == 401
    assert retry.json() == {
        "error": "invalid_grant",
        "error_description": "refresh token does not exist",
    }
    setup.upstream_client.refresh_token.assert_awaited_once()


async def test_refresh_retry_replays_same_response_during_grace_period():
    setup = await build_rotation_setup(grace_period=30)
    with patch(
        "fastmcp.server.auth.oauth_proxy.proxy.AsyncOAuth2Client",
        return_value=setup.upstream_client,
    ):
        first = await post_refresh(setup, setup.refresh_token)
        retry = await post_refresh(setup, setup.refresh_token)

    assert first.status_code == retry.status_code == 200
    assert first.json() == retry.json()
    setup.upstream_client.refresh_token.assert_awaited_once()


async def test_concurrent_refresh_replays_same_response():
    setup = await build_rotation_setup(grace_period=30, upstream_delay=0.05)
    with patch(
        "fastmcp.server.auth.oauth_proxy.proxy.AsyncOAuth2Client",
        return_value=setup.upstream_client,
    ):
        first, second = await asyncio.gather(
            post_refresh(setup, setup.refresh_token),
            post_refresh(setup, setup.refresh_token),
        )

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    setup.upstream_client.refresh_token.assert_awaited_once()


async def test_grace_does_not_replay_for_different_scopes():
    setup = await build_rotation_setup(grace_period=30)
    with patch(
        "fastmcp.server.auth.oauth_proxy.proxy.AsyncOAuth2Client",
        return_value=setup.upstream_client,
    ):
        first = await post_refresh(setup, setup.refresh_token)
        loaded = await setup.proxy.load_refresh_token(
            OAuthClientInformationFull(
                client_id="mcp-client",
                client_secret="mcp-secret",
                redirect_uris=[AnyUrl("http://localhost/callback")],
            ),
            setup.refresh_token,
        )
        assert loaded is not None
        with pytest.raises(TokenError, match="Refresh token mapping not found"):
            await setup.proxy.exchange_refresh_token(
                OAuthClientInformationFull(
                    client_id="mcp-client",
                    client_secret="mcp-secret",
                    redirect_uris=[AnyUrl("http://localhost/callback")],
                ),
                loaded,
                ["read", "write"],
            )

    assert first.status_code == 200
    setup.upstream_client.refresh_token.assert_awaited_once()


async def test_grace_does_not_replay_for_another_client():
    setup = await build_rotation_setup(grace_period=30)
    other_client = OAuthClientInformationFull(
        client_id="other-client",
        client_secret="other-secret",
        redirect_uris=[AnyUrl("http://localhost/callback")],
    )
    with patch(
        "fastmcp.server.auth.oauth_proxy.proxy.AsyncOAuth2Client",
        return_value=setup.upstream_client,
    ):
        first = await post_refresh(setup, setup.refresh_token)
        replay = await setup.proxy.load_refresh_token(other_client, setup.refresh_token)

    assert first.status_code == 200
    assert replay is None


async def test_revoking_predecessor_removes_rotation_response():
    setup = await build_rotation_setup(grace_period=30)
    client = OAuthClientInformationFull(
        client_id="mcp-client",
        client_secret="mcp-secret",
        redirect_uris=[AnyUrl("http://localhost/callback")],
    )
    with patch(
        "fastmcp.server.auth.oauth_proxy.proxy.AsyncOAuth2Client",
        return_value=setup.upstream_client,
    ):
        first = await post_refresh(setup, setup.refresh_token)
        predecessor = await setup.proxy.load_refresh_token(client, setup.refresh_token)
        assert predecessor is not None
        await setup.proxy.revoke_token(predecessor)
        replay = await setup.proxy.load_refresh_token(client, setup.refresh_token)

    assert first.status_code == 200
    assert replay is None


async def test_grace_does_not_replay_superseded_successor():
    setup = await build_rotation_setup(grace_period=30)
    with patch(
        "fastmcp.server.auth.oauth_proxy.proxy.AsyncOAuth2Client",
        return_value=setup.upstream_client,
    ):
        first = await post_refresh(setup, setup.refresh_token)
        successor = first.json()["refresh_token"]
        second = await post_refresh(setup, successor)
        replay = await post_refresh(setup, setup.refresh_token)

    assert first.status_code == second.status_code == 200
    assert replay.status_code == 401
    assert setup.upstream_client.refresh_token.await_count == 2


@pytest.mark.parametrize("grace_period", [-1, 61])
def test_refresh_grace_period_is_bounded(grace_period: int):
    with pytest.raises(
        ValueError,
        match="refresh_token_grace_period_seconds must be between 0 and 60",
    ):
        OAuthProxy(
            upstream_authorization_endpoint="https://idp.example.com/authorize",
            upstream_token_endpoint="https://idp.example.com/token",
            upstream_client_id="upstream-client",
            upstream_client_secret="upstream-secret",
            token_verifier=AcceptingVerifier(),
            base_url="https://proxy.example.com",
            jwt_signing_key="test-only-signing-key",
            client_storage=MemoryStore(),
            refresh_token_grace_period_seconds=grace_period,
        )
