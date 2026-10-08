"""JWKS lookups share refreshes while keeping cached keys available."""

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from joserfc import jwk

from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair


@pytest.fixture
def key_data(rsa_key_pair: RSAKeyPair) -> dict[str, Any]:
    return {
        **jwk.import_key(rsa_key_pair.public_key, "RSA").as_dict(),
        "kid": "current",
    }


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    values = [100.0]
    monkeypatch.setattr(
        "fastmcp.server.auth.providers.jwt.time",
        SimpleNamespace(time=lambda: values[0], monotonic=lambda: values[0]),
    )
    return values


async def test_distinct_unknown_ids_share_recent_refresh(key_data: dict[str, Any]):
    requests: list[httpx2.Request] = []

    async def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"keys": [key_data]})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        verifier = JWTVerifier(
            jwks_uri="https://issuer.example/keys", http_client=client
        )
        original = await verifier._get_jwks_key("current")
        for index in range(8):
            with pytest.raises(ValueError):
                await verifier._get_jwks_key(f"other-{index}")
        assert await verifier._get_jwks_key("current") == original
    assert len(requests) == 1


async def test_parallel_initial_lookups_share_one_refresh(key_data: dict[str, Any]):
    requests: list[httpx2.Request] = []

    async def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        await asyncio.sleep(0)
        return httpx2.Response(200, json={"keys": [key_data]})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        verifier = JWTVerifier(
            jwks_uri="https://issuer.example/keys", http_client=client
        )
        keys = await asyncio.gather(
            *(verifier._get_jwks_key("current") for _ in range(8))
        )
    assert len(set(keys)) == 1
    assert len(requests) == 1


async def test_rotated_key_resolves_after_refresh_interval(
    key_data: dict[str, Any], clock: list[float]
):
    documents = [{"keys": [key_data]}, {"keys": [{**key_data, "kid": "next"}]}]

    async def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=documents.pop(0))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        verifier = JWTVerifier(
            jwks_uri="https://issuer.example/keys", http_client=client
        )
        await verifier._get_jwks_key("current")
        clock[0] += 31
        assert await verifier._get_jwks_key("next")
    assert documents == []


async def test_failed_refresh_is_shared_and_keeps_fresh_keys(
    key_data: dict[str, Any], clock: list[float]
):
    requests: list[httpx2.Request] = []

    async def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx2.Response(200, json={"keys": [key_data]})
        return httpx2.Response(503)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        verifier = JWTVerifier(
            jwks_uri="https://issuer.example/keys", http_client=client
        )
        original = await verifier._get_jwks_key("current")
        clock[0] += 31
        for index in range(8):
            with pytest.raises(ValueError):
                await verifier._get_jwks_key(f"other-{index}")
        assert await verifier._get_jwks_key("current") == original
    assert len(requests) == 2


@pytest.mark.parametrize("keys_present", [False, True])
async def test_no_id_lookup_shares_recent_refresh(
    key_data: dict[str, Any], keys_present: bool
):
    requests: list[httpx2.Request] = []
    keys = [key_data, {**key_data, "kid": "second"}] if keys_present else []

    async def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"keys": keys})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        verifier = JWTVerifier(
            jwks_uri="https://issuer.example/keys", http_client=client
        )
        for _ in range(3):
            with pytest.raises(ValueError):
                await verifier._get_jwks_key(None)
    assert len(requests) == 1


async def test_zero_interval_allows_immediate_rotation(key_data: dict[str, Any]):
    documents = [{"keys": [key_data]}, {"keys": [{**key_data, "kid": "next"}]}]

    async def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=documents.pop(0))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        verifier = JWTVerifier(
            jwks_uri="https://issuer.example/keys",
            http_client=client,
            jwks_refresh_interval=0,
        )
        await verifier._get_jwks_key("current")
        assert await verifier._get_jwks_key("next")
    assert documents == []


@pytest.mark.parametrize("interval", [-1, float("inf"), float("nan")])
def test_refresh_interval_requires_finite_nonnegative_value(interval: float):
    with pytest.raises(ValueError, match="jwks_refresh_interval"):
        JWTVerifier(
            jwks_uri="https://issuer.example/keys", jwks_refresh_interval=interval
        )


async def test_signature_rejections_leave_cached_key_available(
    key_data: dict[str, Any], rsa_key_pair: RSAKeyPair
):
    requests: list[httpx2.Request] = []
    other = RSAKeyPair.generate()
    good = rsa_key_pair.create_token(subject="reader", kid="current")
    bad = other.create_token(subject="reader", kid="current")

    async def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"keys": [key_data]})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        verifier = JWTVerifier(
            jwks_uri="https://issuer.example/keys", http_client=client
        )
        assert await verifier.load_access_token(good) is not None
        for _ in range(3):
            assert await verifier.load_access_token(bad) is None
        assert await verifier.load_access_token(good) is not None
    assert len(requests) == 1


async def test_expired_keys_wait_for_successful_refresh(
    key_data: dict[str, Any], clock: list[float]
):
    requests: list[httpx2.Request] = []

    async def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"keys": [key_data]})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        verifier = JWTVerifier(
            jwks_uri="https://issuer.example/keys", http_client=client
        )
        original = await verifier._get_jwks_key("current")
        clock[0] += 3601
        assert await verifier._get_jwks_key("current") == original
    assert len(requests) == 2


async def test_failed_refresh_does_not_reuse_expired_keys(
    key_data: dict[str, Any], clock: list[float]
):
    requests: list[httpx2.Request] = []

    async def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if len(requests) == 2:
            return httpx2.Response(503)
        return httpx2.Response(200, json={"keys": [key_data]})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        verifier = JWTVerifier(
            jwks_uri="https://issuer.example/keys", http_client=client
        )
        original = await verifier._get_jwks_key("current")
        clock[0] += 3601
        with pytest.raises(ValueError):
            await verifier._get_jwks_key("current")
        with pytest.raises(ValueError):
            await verifier._get_jwks_key("current")
        assert len(requests) == 2
        clock[0] += 31
        assert await verifier._get_jwks_key("current") == original
    assert len(requests) == 3
