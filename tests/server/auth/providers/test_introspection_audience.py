"""Audience selection for introspected tokens."""

from typing import Any

import pytest
from pydantic import AnyHttpUrl

from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.introspection import IntrospectionTokenVerifier
from tests.utilities.httpx2_mock import HTTPXMock


@pytest.mark.parametrize(
    ("expected", "claim", "accepted"),
    [
        ("calendar", "calendar", True),
        ("calendar", ["notes", "calendar"], True),
        (["calendar", "notes"], "notes", True),
        (["calendar", "notes"], ["tasks", "notes"], True),
        ("calendar", "calendar-extra", False),
        ("calendar", "notes", False),
        ("calendar", ["notes"], False),
        (["calendar", "notes"], ["tasks"], False),
        ("calendar", None, False),
        ("calendar", [], False),
        ("calendar", 1, False),
        ("calendar", True, False),
        ("calendar", {"calendar": True}, False),
        ("calendar", ["calendar", 1], False),
        ("calendar", ["calendar", ""], False),
    ],
)
async def test_configured_audiences(
    expected: str | list[str], claim: Any, accepted: bool, httpx_mock: HTTPXMock
):
    verifier = IntrospectionTokenVerifier(
        introspection_url="https://auth.example.com/introspect",
        client_id="calendar-client",
        client_secret="secret",
        audience=expected,
    )
    response = {"active": True, "client_id": "user-1"}
    if claim is not None:
        response["aud"] = claim
    httpx_mock.add_response(json=response)

    result = await verifier.verify_token("token")

    assert (result is not None) is accepted
    if result is not None:
        assert result.claims["aud"] == claim
        assert result.client_id == "user-1"


@pytest.mark.parametrize("claim", [None, "notes", 1, ["notes", 1]])
async def test_default_uses_introspection_decision(claim: Any, httpx_mock: HTTPXMock):
    verifier = IntrospectionTokenVerifier(
        introspection_url="https://auth.example.com/introspect",
        client_id="calendar-client",
        client_secret="secret",
    )
    response = {"active": True}
    if claim is not None:
        response["aud"] = claim
    httpx_mock.add_response(json=response)

    assert await verifier.verify_token("token") is not None


async def test_audience_mismatch_is_retried(httpx_mock: HTTPXMock):
    verifier = IntrospectionTokenVerifier(
        introspection_url="https://auth.example.com/introspect",
        client_id="calendar-client",
        client_secret="secret",
        audience="calendar",
        cache_ttl_seconds=300,
    )
    httpx_mock.add_response(json={"active": True, "aud": "notes"})
    httpx_mock.add_response(json={"active": True, "aud": "calendar"})

    assert await verifier.verify_token("token") is None
    result = await verifier.verify_token("token")
    assert result is not None
    assert await verifier.verify_token("token") == result
    assert len(httpx_mock.get_requests()) == 2


async def test_matching_audience_still_requires_scopes(httpx_mock: HTTPXMock):
    verifier = IntrospectionTokenVerifier(
        introspection_url="https://auth.example.com/introspect",
        client_id="calendar-client",
        client_secret="secret",
        audience="calendar",
        required_scopes=["read"],
    )
    httpx_mock.add_response(json={"active": True, "aud": "calendar", "scope": "write"})

    assert await verifier.verify_token("token") is None


@pytest.mark.parametrize("audience", ["", [], [""], ["calendar", ""]])
def test_audience_configuration_requires_nonempty_values(audience: str | list[str]):
    with pytest.raises(ValueError, match="audience"):
        IntrospectionTokenVerifier(
            introspection_url="https://auth.example.com/introspect",
            client_id="calendar-client",
            client_secret="secret",
            audience=audience,
        )


async def test_remote_provider_uses_configured_audience(httpx_mock: HTTPXMock):
    verifier = IntrospectionTokenVerifier(
        introspection_url="https://auth.example.com/introspect",
        client_id="calendar-client",
        client_secret="secret",
        audience="calendar",
    )
    auth = RemoteAuthProvider(
        token_verifier=verifier,
        authorization_servers=[AnyHttpUrl("https://auth.example.com")],
        base_url="https://calendar.example.com",
    )
    httpx_mock.add_response(json={"active": True, "aud": "notes"})
    httpx_mock.add_response(json={"active": True, "aud": ["calendar", "notes"]})

    assert await auth.verify_token("notes-token") is None
    assert await auth.verify_token("calendar-token") is not None
