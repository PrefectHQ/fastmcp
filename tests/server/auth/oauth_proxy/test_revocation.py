"""Revocation ownership must not replace the identity used for authentication."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs

import httpx2
import pytest
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.provider import AccessToken, TokenError
from mcp.server.transport_security import DEFAULT_MAX_REQUEST_BODY_SIZE
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from starlette.applications import Starlette

from fastmcp.server.auth.auth import RefreshToken, TokenVerificationError
from fastmcp.server.auth.identity_assertion import IdentityAssertion
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.oauth_proxy.models import (
    EndedGrant,
    JTIMapping,
    RefreshTokenMetadata,
    UpstreamTokenSet,
    _hash_token,
)
from fastmcp.server.auth.oidc_proxy import OIDCConfiguration, OIDCProxy
from tests.utilities.httpx2_mock import HTTPXMock


@pytest.fixture(params=["oauth", "oidc"])
async def proxy(jwt_verifier, request: pytest.FixtureRequest) -> OAuthProxy:
    if request.param == "oidc":
        configuration = OIDCConfiguration(
            issuer="https://idp.example.com",
            authorization_endpoint="https://idp.example.com/authorize",
            token_endpoint="https://idp.example.com/token",
            revocation_endpoint="https://idp.example.com/revoke",
            jwks_uri="https://idp.example.com/jwks",
            response_types_supported=["code"],
            subject_types_supported=["public"],
            id_token_signing_alg_values_supported=["RS256"],
        )
        with patch.object(
            OIDCConfiguration, "get_oidc_configuration", return_value=configuration
        ):
            proxy = OIDCProxy(
                config_url="https://idp.example.com/.well-known/openid-configuration",
                client_id="upstream-app",
                client_secret="test-secret",
                token_verifier=jwt_verifier,
                verify_id_token=True,
                base_url="https://proxy.example.com",
                jwt_signing_key="test-signing-key",
                client_storage=MemoryStore(),
            )
    else:
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://idp.example.com/authorize",
            upstream_token_endpoint="https://idp.example.com/token",
            upstream_revocation_endpoint="https://idp.example.com/revoke",
            upstream_client_id="upstream-app",
            upstream_client_secret="test-secret",
            token_verifier=jwt_verifier,
            base_url="https://proxy.example.com",
            jwt_signing_key="test-signing-key",
            client_storage=MemoryStore(),
        )
    proxy.set_mcp_path("/mcp")
    for client_id in ("dcr-client", "other-client", "alice"):
        await proxy.register_client(
            OAuthClientInformationFull(
                client_id=client_id,
                redirect_uris=[AnyUrl("https://client.example.com/callback")],
                token_endpoint_auth_method="none",
            )
        )
    return proxy


@pytest.fixture
async def issue_token(
    proxy: OAuthProxy, jwt_verifier
) -> Callable[[str], Awaitable[str]]:
    validated_tokens: dict[str, AccessToken] = {}
    jwt_verifier.verify_token = AsyncMock(side_effect=validated_tokens.get)

    async def issue(user: str) -> str:
        now = time.time()
        upstream_token = f"upstream-{user}"
        verification_token = (
            f"id-{user}" if isinstance(proxy, OIDCProxy) else upstream_token
        )
        validated_tokens[verification_token] = AccessToken(
            token=verification_token,
            client_id=user,
            scopes=["read"],
            expires_at=int(now + 3600),
            subject=user,
            claims={"sub": user},
        )
        await proxy._upstream_token_store.put(
            key=user,
            value=UpstreamTokenSet(
                upstream_token_id=user,
                access_token=upstream_token,
                refresh_token=None,
                refresh_token_expires_at=None,
                expires_at=now + 3600,
                token_type="Bearer",
                scope="read",
                client_id="dcr-client",
                created_at=now,
                raw_token_data={"id_token": verification_token},
            ),
            ttl=3600,
        )
        await proxy._jti_mapping_store.put(
            key=user,
            value=JTIMapping(jti=user, upstream_token_id=user, created_at=now),
            ttl=3600,
        )
        return proxy.jwt_issuer.issue_access_token(
            client_id="dcr-client", scopes=["read"], jti=user
        )

    return issue


async def test_authentication_preserves_two_users_with_one_dcr_client(
    proxy: OAuthProxy, issue_token: Callable[[str], Awaitable[str]]
) -> None:
    """The bearer verifier must preserve user separation with revocation enabled."""
    for user in ("alice", "bob"):
        token = await issue_token(user)
        for load in (proxy.verify_token, proxy.load_access_token):
            verified = await load(token)
            assert verified is not None
            assert verified.client_id == user
            assert verified.subject == user
            assert verified.claims == {"sub": user}


@pytest.mark.parametrize("hint", [None, "access_token", "refresh_token"])
async def test_owner_revokes_upstream_access_token(
    proxy: OAuthProxy,
    jwt_verifier,
    issue_token: Callable[[str], Awaitable[str]],
    httpx_mock: HTTPXMock,
    hint: str | None,
) -> None:
    request_token = "upstream-alice"
    token = await issue_token("alice")
    before = await proxy.verify_token(token)
    httpx_mock.add_response(url="https://idp.example.com/revoke", method="POST")
    data = {"token": token, "client_id": "dcr-client", "client_secret": ""}
    if hint:
        data["token_type_hint"] = hint
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=Starlette(routes=proxy.get_routes("/mcp"))),
        base_url="https://proxy.example.com",
    ) as client:
        response = await client.post("/revoke", data=data)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    request = httpx_mock.get_request()
    assert request is not None
    assert parse_qs(request.content.decode()) == {"token": ["upstream-alice"]}
    # Verifiers may cache their result; a revocation-only copy must not mutate it.
    assert before is not None
    assert before.client_id == "alice"
    verification_token = "id-alice" if isinstance(proxy, OIDCProxy) else request_token
    cached = await jwt_verifier.verify_token(verification_token)
    assert cached is not None
    assert cached.client_id == "alice"
    # The revoked token is retired locally, so it stays revoked even when the
    # grant's upstream record later holds a fresh upstream token.
    assert await proxy._jti_mapping_store.get(key="alice") is None
    assert await proxy._upstream_token_store.get(key="alice") is not None
    assert await proxy.verify_token(token) is None


async def test_revoking_one_access_token_keeps_other_grants(
    proxy: OAuthProxy,
    issue_token: Callable[[str], Awaitable[str]],
    httpx_mock: HTTPXMock,
) -> None:
    alice = await issue_token("alice")
    bob = await issue_token("bob")
    httpx_mock.add_response(url="https://idp.example.com/revoke", method="POST")
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=Starlette(routes=proxy.get_routes("/mcp"))),
        base_url="https://proxy.example.com",
    ) as client:
        response = await client.post(
            "/revoke",
            data={"token": alice, "client_id": "dcr-client", "client_secret": ""},
        )
    assert response.status_code == 200
    assert await proxy.verify_token(alice) is None
    assert await proxy.verify_token(bob) is not None


async def test_revoking_a_token_whose_upstream_is_gone_still_retires_it(
    proxy: OAuthProxy,
    jwt_verifier,
    issue_token: Callable[[str], Awaitable[str]],
    httpx_mock: HTTPXMock,
) -> None:
    token = await issue_token("alice")
    # The grant's upstream token already fails validation (a sibling's
    # revocation revoked it), so nothing upstream is left to revoke.
    jwt_verifier.verify_token = AsyncMock(return_value=None)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=Starlette(routes=proxy.get_routes("/mcp"))),
        base_url="https://proxy.example.com",
    ) as client:
        response = await client.post(
            "/revoke",
            data={"token": token, "client_id": "dcr-client", "client_secret": ""},
        )
    assert response.status_code == 200
    assert httpx_mock.get_requests() == []
    assert await proxy._jti_mapping_store.get(key="alice") is None


@pytest.mark.parametrize("client_id", ["other-client", "alice"])
async def test_non_owner_cannot_revoke_access_token(
    proxy: OAuthProxy,
    issue_token: Callable[[str], Awaitable[str]],
    httpx_mock: HTTPXMock,
    client_id: str,
) -> None:
    token = await issue_token("alice")
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=Starlette(routes=proxy.get_routes("/mcp"))),
        base_url="https://proxy.example.com",
    ) as client:
        response = await client.post(
            "/revoke",
            data={"token": token, "client_id": client_id, "client_secret": ""},
        )
    assert response.status_code == 200
    assert httpx_mock.get_requests() == []


async def test_invalid_access_token_is_not_revoked(
    proxy: OAuthProxy, jwt_verifier, httpx_mock: HTTPXMock
) -> None:
    jwt_verifier.verify_token = AsyncMock()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=Starlette(routes=proxy.get_routes("/mcp"))),
        base_url="https://proxy.example.com",
    ) as client:
        response = await client.post(
            "/revoke",
            data={
                "token": "invalid-jwt",
                "client_id": "dcr-client",
                "client_secret": "",
            },
        )
    assert response.status_code == 200
    assert httpx_mock.get_requests() == []
    jwt_verifier.verify_token.assert_not_awaited()


async def test_revocation_keeps_body_limit_and_cors(
    proxy: OAuthProxy, jwt_verifier, httpx_mock: HTTPXMock
) -> None:
    jwt_verifier.verify_token = AsyncMock()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=Starlette(routes=proxy.get_routes("/mcp"))),
        base_url="https://proxy.example.com",
    ) as client:
        preflight = await client.options(
            "/revoke",
            headers={
                "origin": "https://client.example.com",
                "access-control-request-method": "POST",
            },
        )
        response = await client.post(
            "/revoke",
            content=b"token=oversized",
            headers={
                "origin": "https://client.example.com",
                "content-type": "application/x-www-form-urlencoded",
                "content-length": str(DEFAULT_MAX_REQUEST_BODY_SIZE + 1),
            },
        )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "*"
    assert response.status_code == 413
    assert response.headers["access-control-allow-origin"] == "*"
    assert httpx_mock.get_requests() == []
    jwt_verifier.verify_token.assert_not_awaited()


@pytest.mark.parametrize("hint", [None, "access_token", "refresh_token"])
async def test_owner_revokes_upstream_refresh_token(
    proxy: OAuthProxy,
    issue_token: Callable[[str], Awaitable[str]],
    httpx_mock: HTTPXMock,
    hint: str | None,
) -> None:
    await issue_token("alice")
    upstream = await proxy._upstream_token_store.get(key="alice")
    assert upstream is not None
    await proxy._upstream_token_store.put(
        key="alice",
        value=upstream.model_copy(update={"refresh_token": "upstream-refresh"}),
        ttl=3600,
    )
    token = proxy.jwt_issuer.issue_refresh_token(
        client_id="dcr-client", scopes=["read"], jti="alice", expires_in=3600
    )
    await proxy._refresh_token_store.put(
        key=_hash_token(token),
        value=RefreshTokenMetadata(
            client_id="dcr-client",
            scopes=["read"],
            expires_at=int(time.time()) + 3600,
            created_at=time.time(),
        ),
        ttl=3600,
    )
    httpx_mock.add_response(url="https://idp.example.com/revoke", method="POST")
    data = {"token": token, "client_id": "dcr-client", "client_secret": ""}
    if hint:
        data["token_type_hint"] = hint
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=Starlette(routes=proxy.get_routes("/mcp"))),
        base_url="https://proxy.example.com",
    ) as client:
        response = await client.post("/revoke", data=data)
    assert response.status_code == 200
    request = httpx_mock.get_request()
    assert request is not None
    assert parse_qs(request.content.decode()) == {"token": ["upstream-refresh"]}
    assert await proxy._refresh_token_store.get(key=_hash_token(token)) is None


async def _issue_refresh_token(
    proxy: OAuthProxy, user: str, *, names_grant: bool = True
) -> str:
    """A refresh token on `user`'s grant, with its own jti like a real exchange.

    `names_grant=False` mints one without the `grant` claim, as tokens issued
    before the claim existed are.
    """
    upstream = await proxy._upstream_token_store.get(key=user)
    assert upstream is not None
    await proxy._upstream_token_store.put(
        key=user,
        value=upstream.model_copy(update={"refresh_token": f"upstream-refresh-{user}"}),
        ttl=3600,
    )
    jti = f"{user}-refresh"
    await proxy._jti_mapping_store.put(
        key=jti,
        value=JTIMapping(jti=jti, upstream_token_id=user, created_at=time.time()),
        ttl=3600,
    )
    token = proxy.jwt_issuer.issue_refresh_token(
        client_id="dcr-client",
        scopes=["read"],
        jti=jti,
        expires_in=3600,
        grant_id=user if names_grant else None,
    )
    await proxy._refresh_token_store.put(
        key=_hash_token(token),
        value=RefreshTokenMetadata(
            client_id="dcr-client",
            scopes=["read"],
            expires_at=int(time.time()) + 3600,
            created_at=time.time(),
        ),
        ttl=3600,
    )
    return token


async def test_revoking_refresh_token_ends_its_access_tokens(
    proxy: OAuthProxy,
    issue_token: Callable[[str], Awaitable[str]],
    httpx_mock: HTTPXMock,
) -> None:
    alice = await issue_token("alice")
    bob = await issue_token("bob")
    refresh = await _issue_refresh_token(proxy, "alice")
    httpx_mock.add_response(url="https://idp.example.com/revoke", method="POST")
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=Starlette(routes=proxy.get_routes("/mcp"))),
        base_url="https://proxy.example.com",
    ) as client:
        response = await client.post(
            "/revoke",
            data={"token": refresh, "client_id": "dcr-client", "client_secret": ""},
        )
    assert response.status_code == 200
    request = httpx_mock.get_request()
    assert request is not None
    assert parse_qs(request.content.decode()) == {"token": ["upstream-refresh-alice"]}
    assert await proxy.verify_token(alice) is None
    assert await proxy.verify_token(bob) is not None


async def test_revoking_refresh_token_without_grant_claim_ends_its_grant(
    proxy: OAuthProxy,
    issue_token: Callable[[str], Awaitable[str]],
    httpx_mock: HTTPXMock,
) -> None:
    """A token issued before the `grant` claim is resolved by its JTI mapping."""
    alice = await issue_token("alice")
    refresh = await _issue_refresh_token(proxy, "alice", names_grant=False)
    httpx_mock.add_response(url="https://idp.example.com/revoke", method="POST")
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=Starlette(routes=proxy.get_routes("/mcp"))),
        base_url="https://proxy.example.com",
    ) as client:
        response = await client.post(
            "/revoke",
            data={"token": refresh, "client_id": "dcr-client", "client_secret": ""},
        )
    assert response.status_code == 200
    assert await proxy.verify_token(alice) is None


async def test_revoking_refresh_token_ends_grant_without_upstream_endpoint(
    proxy: OAuthProxy,
    issue_token: Callable[[str], Awaitable[str]],
    httpx_mock: HTTPXMock,
) -> None:
    alice = await issue_token("alice")
    refresh = await _issue_refresh_token(proxy, "alice")
    proxy._upstream_revocation_endpoint = None

    await proxy.revoke_token(
        RefreshToken(token=refresh, client_id="dcr-client", scopes=["read"])
    )

    assert httpx_mock.get_requests() == []
    assert await proxy.verify_token(alice) is None


async def test_revocation_answers_when_upstream_check_is_unavailable(
    proxy: OAuthProxy,
    jwt_verifier,
    issue_token: Callable[[str], Awaitable[str]],
    httpx_mock: HTTPXMock,
) -> None:
    token = await issue_token("alice")
    jwt_verifier.verify_token = AsyncMock(
        side_effect=TokenVerificationError("introspection unavailable")
    )
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=Starlette(routes=proxy.get_routes("/mcp"))),
        base_url="https://proxy.example.com",
    ) as client:
        response = await client.post(
            "/revoke",
            data={"token": token, "client_id": "dcr-client", "client_secret": ""},
        )
    assert response.status_code == 200
    assert await proxy._jti_mapping_store.get(key="alice") is None


async def test_id_jag_revocation_remains_local(
    proxy: OAuthProxy, httpx_mock: HTTPXMock
) -> None:
    proxy._identity_assertion = IdentityAssertion(
        trusted_issuers=["https://idp.example.com"]
    )
    token = proxy.jwt_issuer.issue_access_token(
        client_id="dcr-client",
        scopes=["read"],
        jti="id-jag",
        subject="alice",
        extra_claims={"fastmcp_grant": "id_jag"},
    )
    assert await proxy.verify_token(token) is not None
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=Starlette(routes=proxy.get_routes("/mcp"))),
        base_url="https://proxy.example.com",
    ) as client:
        response = await client.post(
            "/revoke",
            data={"token": token, "client_id": "dcr-client", "client_secret": ""},
        )
    assert response.status_code == 200
    assert await proxy.verify_token(token) is None
    assert httpx_mock.get_requests() == []


async def test_revoking_refresh_token_mid_rotation_ends_grant(
    proxy: OAuthProxy,
    jwt_verifier,
    issue_token: Callable[[str], Awaitable[str]],
    httpx_mock: HTTPXMock,
) -> None:
    """Revoking a refresh token after refresh claims it still ends the grant."""
    alice_access = await issue_token("alice")
    old_refresh = await _issue_refresh_token(proxy, "alice")
    client_info = await proxy.get_client("dcr-client")
    assert client_info is not None

    refreshed_upstream = AsyncMock()
    refreshed_upstream.client_secret = "test-secret"
    refreshed_upstream.refresh_token = AsyncMock(
        return_value={
            "access_token": "upstream-alice",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": "upstream-refresh-alice-2",
        }
    )

    # Pause exchange_refresh_token right after it consumes the old refresh token
    # and begins storing the new access JTI mapping.
    entered = asyncio.Event()
    release = asyncio.Event()
    original_jti_put = proxy._jti_mapping_store.put
    paused_once = False

    async def pause_on_first_jti_put(*args, **kwargs):
        nonlocal paused_once
        if not paused_once:
            paused_once = True
            entered.set()
            await release.wait()
        return await original_jti_put(*args, **kwargs)

    httpx_mock.add_response(url="https://idp.example.com/revoke", method="POST")

    with (
        patch.object(
            proxy, "_create_upstream_oauth_client", return_value=refreshed_upstream
        ),
        patch.object(
            proxy._jti_mapping_store, "put", side_effect=pause_on_first_jti_put
        ),
    ):
        refresh_task = asyncio.create_task(
            proxy.exchange_refresh_token(
                client_info,
                RefreshToken(
                    token=old_refresh, client_id="dcr-client", scopes=["read"]
                ),
                ["read"],
            )
        )
        await entered.wait()

        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(
                app=Starlette(routes=proxy.get_routes("/mcp"))
            ),
            base_url="https://proxy.example.com",
        ) as http_client:
            response = await http_client.post(
                "/revoke",
                data={
                    "token": old_refresh,
                    "client_id": "dcr-client",
                    "client_secret": "",
                },
            )
        assert response.status_code == 200

        release.set()
        try:
            rotated = await refresh_task
        except TokenError:
            rotated = None

    assert await proxy.verify_token(alice_access) is None
    if rotated is not None:
        assert await proxy.verify_token(rotated.access_token) is None
        assert rotated.refresh_token is not None
        with patch.object(
            proxy, "_create_upstream_oauth_client", return_value=refreshed_upstream
        ):
            with pytest.raises(TokenError) as exc_info:
                await proxy.exchange_refresh_token(
                    client_info,
                    RefreshToken(
                        token=rotated.refresh_token,
                        client_id="dcr-client",
                        scopes=["read"],
                    ),
                    ["read"],
                )
        assert exc_info.value.error == "invalid_grant"


async def test_revoking_recently_rotated_refresh_token_ignores_grace_period(
    proxy: OAuthProxy,
    issue_token: Callable[[str], Awaitable[str]],
    httpx_mock: HTTPXMock,
) -> None:
    """Explicit revocation of a rotated refresh token ends the grant even within grace."""
    await issue_token("alice")
    old_refresh = await _issue_refresh_token(proxy, "alice")
    client_info = await proxy.get_client("dcr-client")
    assert client_info is not None

    refreshed_upstream = AsyncMock()
    refreshed_upstream.client_secret = "test-secret"
    refreshed_upstream.refresh_token = AsyncMock(
        return_value={
            "access_token": "upstream-alice",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": "upstream-refresh-alice-2",
        }
    )
    with patch.object(
        proxy, "_create_upstream_oauth_client", return_value=refreshed_upstream
    ):
        rotated = await proxy.exchange_refresh_token(
            client_info,
            RefreshToken(token=old_refresh, client_id="dcr-client", scopes=["read"]),
            ["read"],
        )
    assert await proxy.verify_token(rotated.access_token) is not None

    # Another client must not be able to revoke the rotated refresh token.
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=Starlette(routes=proxy.get_routes("/mcp"))),
        base_url="https://proxy.example.com",
    ) as http_client:
        non_owner_resp = await http_client.post(
            "/revoke",
            data={
                "token": old_refresh,
                "client_id": "other-client",
                "client_secret": "",
            },
        )
    assert non_owner_resp.status_code == 200
    assert await proxy.verify_token(rotated.access_token) is not None

    # The owning client explicitly revoking the old refresh token immediately ends the grant.
    httpx_mock.add_response(url="https://idp.example.com/revoke", method="POST")
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=Starlette(routes=proxy.get_routes("/mcp"))),
        base_url="https://proxy.example.com",
    ) as http_client:
        owner_resp = await http_client.post(
            "/revoke",
            data={
                "token": old_refresh,
                "client_id": "dcr-client",
                "client_secret": "",
            },
        )
    assert owner_resp.status_code == 200
    assert await proxy.verify_token(rotated.access_token) is None


async def test_failed_refresh_revocation_can_be_completed_on_retry(
    proxy: OAuthProxy,
    issue_token: Callable[[str], Awaitable[str]],
    httpx_mock: HTTPXMock,
) -> None:
    """If a store call fails mid-revocation, retrying /revoke still ends the grant."""
    alice_access = await issue_token("alice")
    refresh = await _issue_refresh_token(proxy, "alice")
    httpx_mock.add_response(url="https://idp.example.com/revoke", method="POST")
    httpx_mock.add_response(url="https://idp.example.com/revoke", method="POST")

    original_end_grant_put = proxy._ended_grant_store.put
    failed_once = False

    async def fail_once_on_end_grant(*args, **kwargs):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("transient store error")
        return await original_end_grant_put(*args, **kwargs)

    app = Starlette(routes=proxy.get_routes("/mcp"))
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://proxy.example.com",
    ) as http_client:
        with patch.object(
            proxy._ended_grant_store, "put", side_effect=fail_once_on_end_grant
        ):
            first = await http_client.post(
                "/revoke",
                data={
                    "token": refresh,
                    "client_id": "dcr-client",
                    "client_secret": "",
                },
            )
            assert first.status_code == 500
            # Retry after transient failure must still resolve the token and end the grant.
            retry = await http_client.post(
                "/revoke",
                data={
                    "token": refresh,
                    "client_id": "dcr-client",
                    "client_secret": "",
                },
            )
            assert retry.status_code == 200

    assert await proxy.verify_token(alice_access) is None


async def test_ended_grant_marker_rejects_lingering_upstream_record(
    proxy: OAuthProxy,
    issue_token: Callable[[str], Awaitable[str]],
) -> None:
    """Access validation rejects an ended grant even if its upstream record lingers."""
    alice_access = await issue_token("alice")
    assert await proxy.verify_token(alice_access) is not None

    # Simulate _store_upstream_token_set leaving the upstream record behind
    # after the ended-grant marker was written (e.g. delete failed or raced).
    await proxy._ended_grant_store.put(
        key="alice",
        value=EndedGrant(ended_at=time.time()),
        ttl=3600,
    )
    assert await proxy._upstream_token_store.get(key="alice") is not None
    assert await proxy.verify_token(alice_access) is None
