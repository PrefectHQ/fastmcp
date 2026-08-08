from __future__ import annotations

from collections.abc import Callable
from urllib.parse import parse_qs

import httpx2
import pytest
from pydantic import SecretStr

from fastmcp.cli.deploy.horizon_client import (
    DEVICE_AUTH_CLIENT_ID,
    DEVICE_AUTH_GRANT_TYPE,
    DeviceMetadata,
    HorizonClient,
    HorizonResponseError,
    HorizonUnauthorizedError,
    normalize_api_origin,
)


def mock_transport(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> httpx2.MockTransport:
    return httpx2.MockTransport(handler)


async def test_device_authorization_uses_the_oauth_form_contract() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/v0/oauth/device/authorization"
        assert request.headers["content-type"].startswith(
            "application/x-www-form-urlencoded"
        )
        assert "authorization" not in request.headers
        assert parse_qs(request.content.decode()) == {
            "client_id": [DEVICE_AUTH_CLIENT_ID],
            "device_name": ["Avery's laptop"],
            "platform": ["darwin"],
            "architecture": ["arm64"],
            "client_version": ["4.0.0"],
        }
        return httpx2.Response(
            200,
            json={
                "device_code": "device-secret",
                "user_code": "BCDF-GHJK",
                "verification_uri": "https://horizon.prefect.io/oauth/device",
                "verification_uri_complete": "https://horizon.prefect.io/oauth/device?user_code=BCDF-GHJK",
                "expires_in": 600,
                "interval": 5,
            },
        )

    async with HorizonClient(
        transport=mock_transport(handler),
    ) as client:
        result = await client.create_device_authorization(
            DeviceMetadata(
                device_name="Avery's laptop",
                platform="darwin",
                architecture="arm64",
                client_version="4.0.0",
            )
        )

    assert result.user_code == "BCDF-GHJK"
    assert result.interval == 5


@pytest.mark.parametrize(
    "error",
    ["authorization_pending", "slow_down", "access_denied", "expired_token"],
)
async def test_device_token_exchange_returns_expected_poll_errors(error: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert parse_qs(request.content.decode()) == {
            "grant_type": [DEVICE_AUTH_GRANT_TYPE],
            "client_id": [DEVICE_AUTH_CLIENT_ID],
            "device_code": ["device-secret"],
        }
        return httpx2.Response(400, json={"error": error})

    async with HorizonClient(transport=mock_transport(handler)) as client:
        result = await client.exchange_device_authorization("device-secret")

    assert result.error == error
    assert result.access_token is None


@pytest.mark.parametrize("access_token", ["", "   "])
async def test_device_token_exchange_rejects_empty_access_tokens(
    access_token: str,
) -> None:
    async with HorizonClient(
        transport=mock_transport(
            lambda request: httpx2.Response(
                200,
                json={"access_token": access_token, "token_type": "Bearer"},
            )
        )
    ) as client:
        with pytest.raises(HorizonResponseError):
            await client.exchange_device_authorization("device-secret")


async def test_device_token_exchange_keeps_the_api_key_secret() -> None:
    async with HorizonClient(
        transport=mock_transport(
            lambda request: httpx2.Response(
                200,
                json={"access_token": "fmcp_secret", "token_type": "Bearer"},
            )
        )
    ) as client:
        result = await client.exchange_device_authorization("device-secret")

    assert isinstance(result.access_token, SecretStr)
    assert result.access_token.get_secret_value() == "fmcp_secret"
    assert "fmcp_secret" not in repr(result)


async def test_authenticated_routes_use_the_current_key_and_paginate() -> None:
    cursors: list[str | None] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["authorization"] == "Bearer fmcp_secret"
        if request.url.path == "/api/v0/me":
            return httpx2.Response(
                200,
                json={
                    "user": {
                        "id": "user-id",
                        "email": "avery@example.com",
                        "name": "Avery",
                        "workosUserId": "workos-id",
                        "createdAt": "2026-08-08T00:00:00Z",
                    }
                },
            )

        assert request.url.path == "/api/v0/me/organizations"
        cursor = request.url.params.get("cursor")
        cursors.append(cursor)
        if cursor is None:
            return httpx2.Response(
                200,
                json={
                    "items": [{"id": "org-1", "name": "First", "slug": "first"}],
                    "meta": {"nextCursor": "next-page", "limit": 100},
                },
            )
        return httpx2.Response(
            200,
            json={
                "items": [{"id": "org-2", "name": "Second", "slug": "second"}],
                "meta": {"nextCursor": None, "limit": 100},
            },
        )

    async with HorizonClient(
        api_key="fmcp_secret",
        transport=mock_transport(handler),
    ) as client:
        user = await client.get_current_user()
        organizations = await client.list_organizations()

    assert user.email == "avery@example.com"
    assert [organization.slug for organization in organizations] == ["first", "second"]
    assert cursors == [None, "next-page"]


@pytest.mark.parametrize("count", [0, 1, 3])
async def test_organization_memberships_preserve_zero_one_and_many(count: int) -> None:
    organizations = [
        {"id": f"org-{index}", "name": f"Org {index}", "slug": f"org-{index}"}
        for index in range(count)
    ]
    async with HorizonClient(
        api_key="fmcp_secret",
        transport=mock_transport(
            lambda request: httpx2.Response(
                200,
                json={
                    "items": organizations,
                    "meta": {"nextCursor": None, "limit": 100},
                },
            )
        ),
    ) as client:
        result = await client.list_organizations()

    assert len(result) == count


async def test_revoke_uses_the_current_authenticated_key() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/api/v0/me/api-key"
        assert request.headers["authorization"] == "Bearer fmcp_current"
        return httpx2.Response(204)

    async with HorizonClient(
        api_key="fmcp_current",
        transport=mock_transport(handler),
    ) as client:
        await client.revoke_current_api_key()


async def test_protected_routes_require_a_credential() -> None:
    async with HorizonClient(
        transport=mock_transport(lambda request: httpx2.Response(200))
    ) as client:
        with pytest.raises(HorizonUnauthorizedError):
            await client.get_current_user()


async def test_protected_routes_report_a_rejected_credential() -> None:
    async with HorizonClient(
        api_key="fmcp_invalid",
        transport=mock_transport(lambda request: httpx2.Response(401)),
    ) as client:
        with pytest.raises(HorizonUnauthorizedError):
            await client.get_current_user()


async def test_public_routes_do_not_report_a_missing_credential() -> None:
    async with HorizonClient(
        transport=mock_transport(lambda request: httpx2.Response(401))
    ) as client:
        with pytest.raises(HorizonResponseError):
            await client.create_device_authorization()


async def test_invalid_responses_do_not_include_response_bodies() -> None:
    secret_body = "fmcp_response_secret"
    async with HorizonClient(
        transport=mock_transport(lambda request: httpx2.Response(500, text=secret_body))
    ) as client:
        with pytest.raises(HorizonResponseError) as exc_info:
            await client.create_device_authorization()

    assert exc_info.value.status_code == 500
    assert secret_body not in str(exc_info.value)


@pytest.mark.parametrize(
    "value",
    [
        "ftp://horizon.prefect.io",
        "https://user@example.com",
        "https://horizon.prefect.io/path",
        "https://horizon.prefect.io?query=value",
        "https://horizon.prefect.io:abc",
        "https://horizon.prefect.io:99999",
    ],
)
def test_api_origin_rejects_values_that_are_not_origins(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_api_origin(value)


def test_api_origin_normalizes_one_trailing_slash() -> None:
    assert (
        normalize_api_origin("https://horizon.prefect.io/")
        == "https://horizon.prefect.io"
    )
