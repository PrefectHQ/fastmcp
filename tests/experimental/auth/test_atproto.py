"""Tests for the AT Protocol provider.

`atproto-oauth` runs the AT Protocol side and has its own tests, so these
replace its client and identity resolver with fakes and exercise what the
provider adds: the handle page, the allowlist, and identity tokens issued
through `OAuthProxy`.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import secrets
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from atproto_oauth.exceptions import OAuthStateError
from joserfc import jws
from key_value.aio.stores.memory import MemoryStore
from starlette.testclient import TestClient

from fastmcp import FastMCP
from fastmcp.experimental.auth.atproto import ATProtoIdentityVerifier, ATProtoProvider
from fastmcp.experimental.auth.atproto import provider as provider_module

DID = "did:plc:abcdefghijklmnopqrstuvwx"
OTHER_DID = "did:plc:zyxwvutsrqponmlkjihgfedc"
HANDLE = "alice.example.com"
ISSUER = "https://auth.example.com"
SERVER = "https://mcp.example.com"
CLIENT_REDIRECT = "https://claude.example/api/mcp/auth_callback"


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


@dataclass
class Session:
    did: str
    handle: str


@dataclass
class FakeATProto:
    """Stands in for `atproto_oauth.OAuthClient` and `AsyncIdResolver`."""

    handles: dict[str, str] = field(default_factory=lambda: {HANDLE: DID})
    sub: str = DID
    session_handle: str = HANDLE
    start_error: Exception | None = None
    started: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    revoked: list[str] = field(default_factory=list)

    async def start_authorization(self, handle_or_did: str) -> tuple[str, str]:
        if self.start_error is not None:
            raise self.start_error
        state = secrets.token_urlsafe(8)
        self.started.append(handle_or_did)
        self.states.append(state)
        return f"{ISSUER}/oauth/authorize?request_uri=urn:{state}", state

    async def handle_callback(self, code: str, state: str, iss: str) -> Session:
        if iss != ISSUER or state not in self.states:
            raise OAuthStateError("Invalid or expired state parameter")
        return Session(did=self.sub, handle=self.session_handle)

    async def revoke_session(self, session: Session) -> None:
        self.revoked.append(session.did)

    async def resolve(self, handle: str) -> str | None:
        return self.handles.get(handle)


@pytest.fixture
def atproto(monkeypatch: pytest.MonkeyPatch) -> FakeATProto:
    fake = FakeATProto()

    class Resolver:
        def __init__(self) -> None:
            self.handle = fake

    monkeypatch.setattr(provider_module, "OAuthClient", lambda **_: fake)
    monkeypatch.setattr(provider_module, "AsyncIdResolver", Resolver)
    return fake


def _provider(**kwargs: Any) -> ATProtoProvider:
    options: dict[str, Any] = {
        "base_url": SERVER,
        "jwt_signing_key": "test-signing-key",
        "allowed_dids": [DID, OTHER_DID],
        "client_storage": MemoryStore(),
    }
    options.update(kwargs)
    return ATProtoProvider(**options)


def _app(provider: ATProtoProvider) -> Any:
    mcp = FastMCP("Media Picker", auth=provider)

    @mcp.tool
    def whoami() -> str:
        return "ok"

    return mcp.http_app()


@dataclass
class SignIn:
    http: TestClient
    client_id: str
    code_verifier: str
    txn_id: str


def _begin(http: TestClient) -> SignIn:
    """Register an MCP client, authorize, and approve consent."""
    client_id = http.post(
        "/register",
        json={
            "redirect_uris": [CLIENT_REDIRECT],
            "client_name": "Claude",
            "token_endpoint_auth_method": "none",
        },
    ).json()["client_id"]
    code_verifier = secrets.token_urlsafe(48)
    authorize = http.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": CLIENT_REDIRECT,
            "code_challenge": _s256(code_verifier),
            "code_challenge_method": "S256",
            "state": "client-state",
        },
        follow_redirects=False,
    )
    txn_id = parse_qs(urlparse(authorize.headers["location"]).query)["txn_id"][0]
    consent = http.get(authorize.headers["location"])
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', consent.text)
    assert csrf is not None
    approved = http.post(
        "/consent",
        data={"txn_id": txn_id, "csrf_token": csrf.group(1), "action": "approve"},
        follow_redirects=False,
    )
    assert approved.headers["location"] == (
        f"{SERVER}/atproto/login?{urlencode({'txn_id': txn_id})}"
    )
    return SignIn(http, client_id, code_verifier, txn_id)


def _submit(sign_in: SignIn, identifier: str) -> Any:
    return sign_in.http.post(
        "/atproto/login",
        data={"txn_id": sign_in.txn_id, "identifier": identifier},
        follow_redirects=False,
    )


def _callback(sign_in: SignIn, state: str, iss: str = ISSUER) -> Any:
    return sign_in.http.get(
        "/auth/callback",
        params={"code": "pds-code", "state": state, "iss": iss},
        follow_redirects=False,
    )


def _tokens(sign_in: SignIn, redirect: str) -> dict[str, Any]:
    code = parse_qs(urlparse(redirect).query)["code"][0]
    response = sign_in.http.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CLIENT_REDIRECT,
            "client_id": sign_in.client_id,
            "code_verifier": sign_in.code_verifier,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _claims(token: str) -> dict[str, Any]:
    return json.loads(jws.extract_compact(token.encode()).payload)


class TestSignIn:
    def test_handle_sign_in_issues_tokens_for_the_did(
        self, atproto: FakeATProto
    ) -> None:
        with TestClient(_app(_provider()), base_url=SERVER) as http:
            sign_in = _begin(http)
            page = http.get(f"/atproto/login?txn_id={sign_in.txn_id}")
            assert "to connect Claude to Media Picker" in page.text

            started = _submit(sign_in, "  @Alice.Example.com ")
            assert started.status_code == 303
            assert started.headers["location"].startswith(f"{ISSUER}/oauth/authorize")
            assert atproto.started == [DID]

            finished = _callback(sign_in, atproto.states[0])
            assert finished.status_code == 302, finished.text
            redirect = finished.headers["location"]
            assert redirect.startswith(CLIENT_REDIRECT)
            assert parse_qs(urlparse(redirect).query)["state"] == ["client-state"]
            assert atproto.revoked == [DID]

            access = _tokens(sign_in, redirect)["access_token"]
            assert _claims(access)["upstream_claims"] == {"did": DID, "handle": HANDLE}
            initialize = http.post(
                "/mcp",
                headers={
                    "Authorization": f"Bearer {access}",
                    "Accept": "application/json, text/event-stream",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"},
                    },
                },
            )
            assert initialize.status_code == 200

    def test_single_allowed_did_goes_straight_to_its_authorization_server(
        self, atproto: FakeATProto
    ) -> None:
        with TestClient(_app(_provider(allowed_dids=[DID])), base_url=SERVER) as http:
            sign_in = _begin(http)
            started = http.get(
                f"/atproto/login?txn_id={sign_in.txn_id}", follow_redirects=False
            )
        assert started.status_code == 303
        assert atproto.started == [DID]

    def test_disallowed_account_is_refused_before_authorization(
        self, atproto: FakeATProto
    ) -> None:
        atproto.handles["mallory.example.com"] = "did:plc:mmmmmmmmmmmmmmmmmmmmmmmm"
        with TestClient(_app(_provider()), base_url=SERVER) as http:
            response = _submit(_begin(http), "mallory.example.com")
        assert response.status_code == 400
        assert "isn't allowed" in html.unescape(response.text)
        assert atproto.started == []

    def test_unknown_handle_is_reported_on_the_page(self, atproto: FakeATProto) -> None:
        with TestClient(_app(_provider()), base_url=SERVER) as http:
            response = _submit(_begin(http), "nobody.example.com")
        assert response.status_code == 400
        assert "Couldn't find that handle." in html.unescape(response.text)

    def test_unreachable_authorization_server_is_reported(
        self, atproto: FakeATProto
    ) -> None:
        atproto.start_error = RuntimeError("PAR failed")
        with TestClient(_app(_provider()), base_url=SERVER) as http:
            response = _submit(_begin(http), HANDLE)
        assert response.status_code == 400
        assert "couldn't start sign-in" in html.unescape(response.text)

    def test_callback_rejected_by_the_client_issues_no_code(
        self, atproto: FakeATProto
    ) -> None:
        with TestClient(_app(_provider()), base_url=SERVER) as http:
            sign_in = _begin(http)
            _submit(sign_in, HANDLE)
            response = _callback(sign_in, atproto.states[0], iss="https://evil.test")
        assert response.status_code == 500
        assert "location" not in response.headers

    def test_subject_outside_the_allowlist_is_refused_and_revoked(
        self, atproto: FakeATProto
    ) -> None:
        atproto.sub = "did:plc:mmmmmmmmmmmmmmmmmmmmmmmm"
        with TestClient(_app(_provider()), base_url=SERVER) as http:
            sign_in = _begin(http)
            _submit(sign_in, HANDLE)
            response = _callback(sign_in, atproto.states[0])
        assert response.status_code == 500
        assert "isn't allowed" in html.unescape(response.text)
        assert atproto.revoked == ["did:plc:mmmmmmmmmmmmmmmmmmmmmmmm"]

    def test_unknown_callback_state_is_rejected(self, atproto: FakeATProto) -> None:
        with TestClient(_app(_provider()), base_url=SERVER) as http:
            response = _callback(_begin(http), "never-issued")
        assert response.status_code == 400

    def test_handle_claim_requires_the_handle_to_point_back(
        self, atproto: FakeATProto
    ) -> None:
        atproto.session_handle = "impostor.example.com"
        atproto.handles["impostor.example.com"] = OTHER_DID
        with TestClient(_app(_provider()), base_url=SERVER) as http:
            sign_in = _begin(http)
            _submit(sign_in, HANDLE)
            finished = _callback(sign_in, atproto.states[0])
            access = _tokens(sign_in, finished.headers["location"])["access_token"]
        assert _claims(access)["upstream_claims"] == {"did": DID, "handle": None}


class TestIdentityTokens:
    async def test_refresh_rechecks_the_allowlist(self, atproto: FakeATProto) -> None:
        provider = _provider()
        tokens = provider._mint_identity_tokens(
            did=DID, handle=HANDLE, client_id="client", scopes=[]
        )
        refreshed = provider._refresh_identity(tokens["refresh_token"])
        verified = await provider._token_validator.verify_token(
            refreshed["access_token"]
        )
        assert verified is not None and verified.subject == DID

        with pytest.raises(Exception, match="invalid_grant"):
            provider._refresh_identity(tokens["access_token"])

        locked_out = _provider(allowed_dids=[OTHER_DID])
        with pytest.raises(Exception, match="invalid_grant"):
            locked_out._refresh_identity(
                locked_out._mint_identity_tokens(
                    did=DID, handle=None, client_id="client", scopes=[]
                )["refresh_token"]
            )

    async def test_verifier_rechecks_the_allowlist(self, atproto: FakeATProto) -> None:
        provider = _provider()
        token = provider._mint_identity_tokens(
            did=DID, handle=HANDLE, client_id="client", scopes=[]
        )["access_token"]
        verifier = provider._token_validator
        assert isinstance(verifier, ATProtoIdentityVerifier)
        assert await verifier.verify_token(token) is not None
        provider._allowed_dids._dids = frozenset({OTHER_DID})
        assert await verifier.verify_token(token) is None


class TestConfiguration:
    def test_client_metadata_document(self, atproto: FakeATProto) -> None:
        with TestClient(_app(_provider()), base_url=SERVER) as http:
            metadata = http.get("/oauth-client-metadata.json").json()
        assert metadata == {
            "client_id": f"{SERVER}/oauth-client-metadata.json",
            "client_name": "Media Picker",
            "client_uri": SERVER,
            "redirect_uris": [f"{SERVER}/auth/callback"],
            "scope": "atproto",
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "application_type": "web",
            "dpop_bound_access_tokens": True,
        }

    def test_loopback_uses_a_virtual_client_id(self, atproto: FakeATProto) -> None:
        provider = _provider(base_url="http://127.0.0.1:8000")
        assert provider._upstream_client_id == "http://localhost?" + urlencode(
            {"redirect_uri": "http://127.0.0.1:8000/auth/callback", "scope": "atproto"}
        )
        assert "/oauth-client-metadata.json" not in [
            route.path for route in provider.get_routes("/mcp")
        ]

    @pytest.mark.parametrize(
        "base_url", ["http://localhost:8000", "http://mcp.example.com"]
    )
    def test_rejects_unusable_base_urls(
        self, atproto: FakeATProto, base_url: str
    ) -> None:
        with pytest.raises(ValueError):
            _provider(base_url=base_url)

    def test_allowed_dids_must_be_dids(self, atproto: FakeATProto) -> None:
        with pytest.raises(ValueError, match="allowed_dids must contain DIDs"):
            _provider(allowed_dids=[HANDLE])
