"""Tests that a remembered approval covers only what the consent page showed.

In ``"remember"`` mode the proxy silently approves a returning client from a
signed cookie. The remembered entry must be specific to the redirect URI
(including its query) and the scopes the user approved, so a later request for
more scopes, or for another redirect URI of the same client, is prompted.
"""

import re
import time
from urllib.parse import parse_qs, urlparse

import httpx2
import pytest
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from starlette.applications import Starlette
from starlette.testclient import TestClient

from fastmcp.server.auth.auth import AccessToken, TokenVerifier
from fastmcp.server.auth.oauth_proxy import OAuthProxy

UPSTREAM_AUTHORIZE = "https://github.com/login/oauth/authorize"
APPROVED_COOKIE = "__Host-MCP_APPROVED_CLIENTS"
CLIENT_ID = "client-remembered-key"
REDIRECT = "http://localhost:5016/callback"
REDIRECT_TENANT_A = "http://localhost:5016/callback?tenant=A"
REDIRECT_TENANT_B = "http://localhost:5016/callback?tenant=B"
REDIRECTS = [REDIRECT, REDIRECT_TENANT_A, REDIRECT_TENANT_B]


class _Verifier(TokenVerifier):
    def __init__(self):
        self.required_scopes = ["read"]

    async def verify_token(self, token: str):
        return AccessToken(
            token=token,
            client_id="c",
            scopes=self.required_scopes,
            expires_at=int(time.time() + 3600),
        )


@pytest.fixture
def proxy() -> OAuthProxy:
    """OAuthProxy in 'remember' mode."""
    return OAuthProxy(
        upstream_authorization_endpoint=UPSTREAM_AUTHORIZE,
        upstream_token_endpoint="https://github.com/login/oauth/access_token",
        upstream_client_id="client-id",
        upstream_client_secret="client-secret",
        token_verifier=_Verifier(),
        base_url="https://myserver.example",
        client_storage=MemoryStore(),
        jwt_signing_key="test-secret",
        require_authorization_consent="remember",
        valid_scopes=["read", "write"],
    )


async def _start_flow(proxy: OAuthProxy, redirect: str, scopes: list[str]) -> str:
    """Register the client (idempotent) and start a flow; returns the txn_id."""
    client = OAuthClientInformationFull(
        client_id=CLIENT_ID,
        client_secret="s",
        redirect_uris=[AnyUrl(uri) for uri in REDIRECTS],
    )
    await proxy.register_client(client)
    consent_url = await proxy.authorize(
        client,
        AuthorizationParams(
            redirect_uri=AnyUrl(redirect),
            redirect_uri_provided_explicitly=True,
            state="client-state-xyz",
            code_challenge="challenge",
            scopes=scopes,
        ),
    )
    return parse_qs(urlparse(consent_url).query)["txn_id"][0]


def _approve(client: TestClient, txn_id: str) -> None:
    """Render the explicit consent prompt, approve it and keep the cookie.

    The remembered-approval cookie is a ``__Host-`` (Secure) cookie, which the
    plain-HTTP test client would not send back, so it is copied by hand.
    """
    consent = client.get(f"/consent?txn_id={txn_id}")
    assert consent.status_code == 200
    m = re.search(r"name=\"csrf_token\"\s+value=\"([^\"]+)\"", consent.text)
    assert m
    for k, v in consent.cookies.items():
        client.cookies.set(k, v)
    response = client.post(
        "/consent",
        data={"action": "approve", "txn_id": txn_id, "csrf_token": m.group(1)},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert response.headers["location"].startswith(UPSTREAM_AUTHORIZE)
    set_cookie = ";\n".join(response.headers.get_list("set-cookie"))
    approved = re.search(rf"{APPROVED_COOKIE}=([^;]+)", set_cookie)
    assert approved, "approved cookie should be set in 'remember' mode"
    client.cookies.set(APPROVED_COOKIE, approved.group(1))


def _return_visit(client: TestClient, txn_id: str) -> httpx2.Response:
    """Visit the consent page the way a client-initiated navigation does."""
    return client.get(
        f"/consent?txn_id={txn_id}",
        headers={"Sec-Fetch-Site": "none"},
        follow_redirects=False,
    )


def _is_silent_approval(response: httpx2.Response) -> bool:
    return response.status_code in (302, 303) and response.headers.get(
        "location", ""
    ).startswith(UPSTREAM_AUTHORIZE)


class TestRememberedApprovalKey:
    async def test_wider_scopes_are_prompted(self, proxy: OAuthProxy):
        """An approval for `read` must not silently approve `read write`."""
        app = Starlette(routes=proxy.get_routes())
        with TestClient(app) as c:
            _approve(c, await _start_flow(proxy, REDIRECT, ["read"]))

            wider = _return_visit(
                c, await _start_flow(proxy, REDIRECT, ["read", "write"])
            )

        assert not _is_silent_approval(wider)
        assert wider.status_code == 200
        assert "read, write" in wider.text

    async def test_other_redirect_query_is_prompted(self, proxy: OAuthProxy):
        """An approval for `?tenant=A` must not silently approve `?tenant=B`."""
        app = Starlette(routes=proxy.get_routes())
        with TestClient(app) as c:
            _approve(c, await _start_flow(proxy, REDIRECT_TENANT_A, ["read"]))

            other = _return_visit(
                c, await _start_flow(proxy, REDIRECT_TENANT_B, ["read"])
            )

        assert not _is_silent_approval(other)
        assert other.status_code == 200
        assert "tenant=B" in other.text

    async def test_same_request_is_still_silently_approved(self, proxy: OAuthProxy):
        """The same client, redirect URI and scopes stay remembered, in any
        scope order."""
        app = Starlette(routes=proxy.get_routes())
        with TestClient(app) as c:
            _approve(c, await _start_flow(proxy, REDIRECT_TENANT_A, ["read", "write"]))

            again = _return_visit(
                c, await _start_flow(proxy, REDIRECT_TENANT_A, ["write", "read"])
            )

        assert _is_silent_approval(again)

    async def test_narrower_scopes_are_prompted(self, proxy: OAuthProxy):
        """The remembered entry is the exact scope set, so a subset of it is a
        different request and is shown to the user once."""
        app = Starlette(routes=proxy.get_routes())
        with TestClient(app) as c:
            _approve(c, await _start_flow(proxy, REDIRECT, ["read", "write"]))

            narrower = _return_visit(c, await _start_flow(proxy, REDIRECT, ["read"]))

        assert narrower.status_code == 200
