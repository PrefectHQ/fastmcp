"""Exercise private_key_jwt authentication through the real token route."""

from collections.abc import Iterator
from typing import TypedDict
from unittest.mock import AsyncMock, patch

import pytest
from joserfc.jwk import RSAKey
from starlette.applications import Starlette
from starlette.testclient import TestClient

from fastmcp.server.auth import OAuthProxy
from fastmcp.server.auth.auth import JWT_BEARER_ASSERTION_TYPE
from fastmcp.server.auth.cimd import CIMDDocument
from fastmcp.server.auth.providers.jwt import RSAKeyPair, StaticTokenVerifier

CLIENT_ID = "https://client.example.com/metadata.json"
TOKEN_ENDPOINT = "https://mcp.example.com/token"


class AssertionOverrides(TypedDict, total=False):
    issuer: str
    audience: str
    expires_in_seconds: int


@pytest.fixture
def token_client(
    rsa_key_pair: RSAKeyPair, request: pytest.FixtureRequest
) -> Iterator[TestClient]:
    proxy = OAuthProxy(
        upstream_authorization_endpoint="https://idp.example.com/authorize",
        upstream_token_endpoint="https://idp.example.com/token",
        upstream_client_id="upstream-id",
        upstream_client_secret="upstream-secret",
        token_verifier=StaticTokenVerifier(tokens={}),
        base_url="https://mcp.example.com",
    )
    document = CIMDDocument(
        client_id=CLIENT_ID,
        redirect_uris=["http://localhost/callback"],
        token_endpoint_auth_method=getattr(request, "param", "private_key_jwt"),
        grant_types=["authorization_code", "refresh_token"],
        jwks={"keys": [RSAKey.import_key(rsa_key_pair.public_key).as_dict()]},
    )
    assert proxy._cimd_manager is not None
    with patch.object(
        proxy._cimd_manager._fetcher, "fetch", AsyncMock(return_value=document)
    ):
        with TestClient(Starlette(routes=proxy.get_routes("/mcp"))) as client:
            yield client


def assertion(
    key: RSAKeyPair,
    *,
    subject: str = CLIENT_ID,
    issuer: str = CLIENT_ID,
    audience: str = TOKEN_ENDPOINT,
    expires_in_seconds: int = 60,
) -> str:
    return key.create_token(
        subject=subject,
        issuer=issuer,
        audience=audience,
        expires_in_seconds=expires_in_seconds,
        additional_claims={"jti": "test-assertion"},
    )


def form(token: str, include_client_id: bool = False) -> dict[str, str]:
    result = {
        "grant_type": "refresh_token",
        "refresh_token": "nonexistent-refresh-token",
        "client_assertion_type": JWT_BEARER_ASSERTION_TYPE,
        "client_assertion": token,
    }
    if include_client_id:
        result["client_id"] = CLIENT_ID
    return result


@pytest.mark.parametrize("include_client_id", [False, True])
@pytest.mark.parametrize("grant_type", ["authorization_code", "refresh_token"])
def test_signed_assertion_reaches_refresh_grant(
    token_client: TestClient,
    rsa_key_pair: RSAKeyPair,
    include_client_id: bool,
    grant_type: str,
):
    data = form(assertion(rsa_key_pair), include_client_id)
    data.update(
        grant_type=grant_type, code="nonexistent-code", code_verifier="verifier"
    )
    response = token_client.post("/token", data=data)
    # Authentication succeeded; the intentionally nonexistent refresh token is
    # rejected by the real grant handler, after request-model validation.
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_grant", response.text


@pytest.mark.parametrize(
    "overrides",
    [
        {"issuer": "https://other.example.com"},
        {"audience": "https://other.example.com/token"},
        {"expires_in_seconds": -60},
    ],
)
def test_omitted_client_id_keeps_claim_validation(
    token_client: TestClient, rsa_key_pair: RSAKeyPair, overrides: AssertionOverrides
):
    response = token_client.post(
        "/token", data=form(assertion(rsa_key_pair, **overrides))
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


def test_omitted_client_id_keeps_signature_validation(
    token_client: TestClient, rsa_key_pair_2: RSAKeyPair
):
    response = token_client.post("/token", data=form(assertion(rsa_key_pair_2)))
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


def test_omitted_client_id_keeps_replay_validation(
    token_client: TestClient, rsa_key_pair: RSAKeyPair
):
    data = form(assertion(rsa_key_pair))
    assert token_client.post("/token", data=data).json()["error"] == "invalid_grant"
    response = token_client.post("/token", data=data)
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


@pytest.mark.parametrize(
    "token",
    [
        "malformed",
        "e30.W10.signature",
        # Non-object headers must fail before reaching JWKS key selection.
        "WyJhbGciXQ.eyJzdWIiOiAiaHR0cHM6Ly9jbGllbnQuZXhhbXBsZS5jb20vbWV0YWRhdGEuanNvbiJ9.c2ln",
        "ImFsZyI.eyJzdWIiOiAiaHR0cHM6Ly9jbGllbnQuZXhhbXBsZS5jb20vbWV0YWRhdGEuanNvbiJ9.c2ln",
        "WyJhbGciLCAiYjY0Il0.eyJzdWIiOiAiaHR0cHM6Ly9jbGllbnQuZXhhbXBsZS5jb20vbWV0YWRhdGEuanNvbiJ9.c2ln",
    ],
)
def test_malformed_assertion_returns_oauth_error(token_client: TestClient, token: str):
    response = token_client.post("/token", data=form(token))
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


def test_explicit_client_id_must_match_assertion_subject(
    token_client: TestClient, rsa_key_pair: RSAKeyPair
):
    data = form(assertion(rsa_key_pair, subject="https://other.example.com"), True)
    response = token_client.post("/token", data=data)
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


@pytest.mark.parametrize("token_client", ["none"], indirect=True)
def test_public_client_keeps_standard_authentication(token_client: TestClient):
    response = token_client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": "nonexistent-refresh-token",
            "client_id": CLIENT_ID,
        },
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_grant"


@pytest.mark.parametrize("token_client", ["none"], indirect=True)
def test_assertion_cannot_infer_public_client(
    token_client: TestClient, rsa_key_pair: RSAKeyPair
):
    response = token_client.post("/token", data=form(assertion(rsa_key_pair)))
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"
