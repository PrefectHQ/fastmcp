"""Tests for the AT Protocol OAuth provider.

The AT Protocol side (handle, DID document, PDS, authorization server) is a
fake network installed in place of the SSRF-safe fetch functions, so every
request the provider makes is observed and validated: DPoP proofs are verified
against their embedded keys, PKCE verifiers against their challenges.
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
from joserfc import jws, jwt
from joserfc.jwk import ECKey
from key_value.aio.stores.memory import MemoryStore
from starlette.testclient import TestClient

from fastmcp import FastMCP
from fastmcp.experimental.auth.atproto import (
    ATProtoIdentityVerifier,
    ATProtoProvider,
)
from fastmcp.experimental.auth.atproto import identity as identity_module
from fastmcp.experimental.auth.atproto import oauth as oauth_module
from fastmcp.experimental.auth.atproto.identity import (
    ATProtoError,
    discover_authorization_server,
    normalize_identifier,
    resolve_did,
    resolve_handle,
)
from fastmcp.server.auth.ssrf import SSRFFetchError, SSRFFetchResponse

DID = "did:plc:abcdefghijklmnopqrstuvwx"
OTHER_DID = "did:plc:zyxwvutsrqponmlkjihgfedc"
HANDLE = "alice.pds.test"
PDS = "https://pds.test"
ISSUER = "https://auth.test"
SERVER = "https://mcp.test"
CLIENT_REDIRECT = "https://claude.example/api/mcp/auth_callback"


def _s256(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )


def _thumbprint(jwk: dict[str, Any]) -> str:
    canonical = json.dumps(
        {k: jwk[k] for k in ("crv", "kty", "x", "y")}, separators=(",", ":")
    )
    return _s256(canonical)


@dataclass
class FakeATProtoNetwork:
    """A handle host, PLC directory, PDS and authorization server."""

    sub: str = DID
    nonce: str = "nonce-1"
    well_known_did: str | None = DID
    as_issuer: str = ISSUER
    requests: list[tuple[str, str]] = field(default_factory=list)
    pars: dict[str, dict[str, str]] = field(default_factory=dict)
    codes: dict[str, str] = field(default_factory=dict)
    revoked: list[str] = field(default_factory=list)
    par_attempts: int = 0

    def documents(self) -> dict[str, dict[str, Any]]:
        return {
            f"https://plc.directory/{DID}": {
                "id": DID,
                "alsoKnownAs": [f"at://{HANDLE}"],
                "service": [
                    {
                        "id": "#atproto_pds",
                        "type": "AtprotoPersonalDataServer",
                        "serviceEndpoint": PDS,
                    }
                ],
            },
            f"{PDS}/.well-known/oauth-protected-resource": {
                "resource": PDS,
                "authorization_servers": [ISSUER],
            },
            f"{ISSUER}/.well-known/oauth-authorization-server": {
                "issuer": self.as_issuer,
                "scopes_supported": ["atproto", "transition:generic"],
                "dpop_signing_alg_values_supported": ["ES256"],
                "pushed_authorization_request_endpoint": f"{ISSUER}/oauth/par",
                "authorization_endpoint": f"{ISSUER}/oauth/authorize",
                "token_endpoint": f"{ISSUER}/oauth/token",
                "revocation_endpoint": f"{ISSUER}/oauth/revoke",
            },
        }

    async def fetch(self, url: str, **_: Any) -> bytes:
        self.requests.append(("GET", url))
        if url == f"https://{HANDLE}/.well-known/atproto-did":
            if self.well_known_did is None:
                raise SSRFFetchError("HTTP 404")
            return self.well_known_did.encode()
        if url.startswith("https://public.api.bsky.app/xrpc/"):
            raise SSRFFetchError("HTTP 400")
        document = self.documents().get(url)
        if document is None:
            raise SSRFFetchError(f"HTTP 404 fetching {url}")
        return json.dumps(document).encode()

    def _verify_dpop(self, proof: str, url: str) -> str:
        header = jws.extract_compact(proof.encode()).headers()
        assert header["typ"] == "dpop+jwt"
        assert "d" not in header["jwk"]
        claims = jwt.decode(
            proof, ECKey.import_key(header["jwk"]), algorithms=["ES256"]
        ).claims
        assert claims["htm"] == "POST"
        assert claims["htu"] == url
        assert claims["jti"]
        return _thumbprint(header["jwk"]) if claims.get("nonce") == self.nonce else ""

    async def fetch_response(
        self,
        url: str,
        *,
        method: str = "GET",
        content: bytes | None = None,
        request_headers: dict[str, str] | None = None,
        **_: Any,
    ) -> SSRFFetchResponse:
        self.requests.append((method, url))
        assert method == "POST"
        assert request_headers is not None
        form = {k: v[0] for k, v in parse_qs((content or b"").decode()).items()}
        thumbprint = self._verify_dpop(request_headers["DPoP"], url)
        if not thumbprint:
            return self._json(
                400, {"error": "use_dpop_nonce"}, {"DPoP-Nonce": self.nonce}
            )

        if url == f"{ISSUER}/oauth/par":
            self.par_attempts += 1
            request_uri = f"urn:ietf:params:oauth:request_uri:{secrets.token_hex(8)}"
            self.pars[form["state"]] = {**form, "jkt": thumbprint}
            return self._json(201, {"request_uri": request_uri, "expires_in": 60})

        if url == f"{ISSUER}/oauth/token":
            state = self.codes.pop(form["code"], None)
            par = self.pars.get(state or "")
            if (
                par is None
                or _s256(form["code_verifier"]) != par["code_challenge"]
                or par["jkt"] != thumbprint
                or form["redirect_uri"] != par["redirect_uri"]
                or form["client_id"] != par["client_id"]
            ):
                return self._json(400, {"error": "invalid_grant"})
            return self._json(
                200,
                {
                    "access_token": "pds-access",
                    "refresh_token": "pds-refresh",
                    "token_type": "DPoP",
                    "scope": "atproto",
                    "sub": self.sub,
                    "expires_in": 300,
                },
            )

        if url == f"{ISSUER}/oauth/revoke":
            self.revoked.append(form["token"])
            return self._json(200, {})

        raise AssertionError(f"Unexpected POST {url}")

    def _json(
        self, status: int, body: dict[str, Any], headers: dict[str, str] | None = None
    ) -> SSRFFetchResponse:
        return SSRFFetchResponse(
            content=json.dumps(body).encode(),
            status_code=status,
            headers={"content-type": "application/json", **(headers or {})},
        )

    def approve(self, state: str) -> str:
        code = secrets.token_urlsafe(16)
        self.codes[code] = state
        return code


@pytest.fixture
def network(monkeypatch: pytest.MonkeyPatch) -> FakeATProtoNetwork:
    fake = FakeATProtoNetwork()
    monkeypatch.setattr(identity_module, "ssrf_safe_fetch", fake.fetch)
    monkeypatch.setattr(oauth_module, "ssrf_safe_fetch_response", fake.fetch_response)
    return fake


def _provider(**kwargs: Any) -> ATProtoProvider:
    options: dict[str, Any] = {
        "base_url": SERVER,
        "jwt_signing_key": "test-signing-key",
        "allowed_dids": [DID],
        "client_storage": MemoryStore(),
    }
    options.update(kwargs)
    return ATProtoProvider(**options)


@dataclass
class SignIn:
    http: TestClient
    client_id: str
    code_verifier: str
    txn_id: str


def _begin(http: TestClient) -> SignIn:
    """Register an MCP client, authorize, and approve consent."""
    registration = http.post(
        "/register",
        json={
            "redirect_uris": [CLIENT_REDIRECT],
            "client_name": "Claude",
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )
    assert registration.status_code == 201, registration.text
    client_id = registration.json()["client_id"]
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
    assert authorize.status_code == 302, authorize.text
    consent = http.get(authorize.headers["location"])
    match = re.search(r'name="csrf_token" value="([^"]+)"', consent.text)
    assert match is not None
    txn_id = parse_qs(urlparse(authorize.headers["location"]).query)["txn_id"][0]
    approved = http.post(
        "/consent",
        data={"txn_id": txn_id, "csrf_token": match.group(1), "action": "approve"},
        follow_redirects=False,
    )
    assert approved.status_code == 302
    assert approved.headers["location"] == (
        f"{SERVER}/atproto/login?{urlencode({'txn_id': txn_id})}"
    )
    return SignIn(http, client_id, code_verifier, txn_id)


def _submit_handle(sign_in: SignIn, identifier: str) -> Any:
    return sign_in.http.post(
        "/atproto/login",
        data={"txn_id": sign_in.txn_id, "identifier": identifier},
        follow_redirects=False,
    )


def _callback(sign_in: SignIn, **params: str) -> Any:
    return sign_in.http.get("/auth/callback", params=params, follow_redirects=False)


def _app(provider: ATProtoProvider) -> Any:
    mcp = FastMCP("Media Picker", auth=provider)

    @mcp.tool
    def whoami() -> str:
        return "ok"

    return mcp.http_app()


def _initialize(http: TestClient, token: str) -> Any:
    return http.post(
        "/mcp",
        headers={
            "Authorization": f"Bearer {token}",
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


class TestSignIn:
    def test_full_flow_issues_tokens_bound_to_the_did(
        self, network: FakeATProtoNetwork
    ) -> None:
        provider = _provider(allowed_dids=[DID, OTHER_DID])
        with TestClient(_app(provider), base_url=SERVER) as http:
            sign_in = _begin(http)

            page = http.get(f"/atproto/login?txn_id={sign_in.txn_id}")
            assert page.status_code == 200
            assert "Sign in with your handle" in page.text
            assert "to connect Claude to Media Picker" in page.text

            started = _submit_handle(sign_in, "  @Alice.PDS.test ")
            assert started.status_code == 303
            location = urlparse(started.headers["location"])
            assert f"{location.scheme}://{location.netloc}{location.path}" == (
                f"{ISSUER}/oauth/authorize"
            )
            query = parse_qs(location.query)
            assert query["client_id"] == [f"{SERVER}/oauth-client-metadata.json"]

            assert network.par_attempts == 1
            [(state, par)] = network.pars.items()
            assert par["scope"] == "atproto"
            assert par["login_hint"] == HANDLE
            assert par["redirect_uri"] == f"{SERVER}/auth/callback"
            assert par["code_challenge_method"] == "S256"

            finished = _callback(
                sign_in, code=network.approve(state), state=state, iss=ISSUER
            )
            assert finished.status_code == 302, finished.text
            redirect = urlparse(finished.headers["location"])
            assert redirect.geturl().startswith(CLIENT_REDIRECT)
            redirect_query = parse_qs(redirect.query)
            assert redirect_query["state"] == ["client-state"]
            assert redirect_query["iss"] == [f"{SERVER}/"]
            assert network.revoked == ["pds-refresh"]

            tokens = http.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": redirect_query["code"][0],
                    "redirect_uri": CLIENT_REDIRECT,
                    "client_id": sign_in.client_id,
                    "code_verifier": sign_in.code_verifier,
                },
            )
            assert tokens.status_code == 200, tokens.text
            body = tokens.json()
            assert body["refresh_token"]
            access = body["access_token"]

            claims = json.loads(jws.extract_compact(access.encode()).payload)
            assert claims["upstream_claims"] == {"did": DID, "handle": HANDLE}
            assert "pds-access" not in access

            assert _initialize(http, access).status_code == 200
            assert _initialize(http, "not-a-token").status_code == 401

    def test_single_allowed_did_goes_straight_to_its_pds(
        self, network: FakeATProtoNetwork
    ) -> None:
        with TestClient(_app(_provider()), base_url=SERVER) as http:
            sign_in = _begin(http)
            started = http.get(
                f"/atproto/login?txn_id={sign_in.txn_id}", follow_redirects=False
            )
            assert started.status_code == 303
            assert started.headers["location"].startswith(f"{ISSUER}/oauth/authorize?")
            [(state, par)] = network.pars.items()
            assert par["login_hint"] == HANDLE
            assert ("GET", f"https://{HANDLE}/.well-known/atproto-did") not in (
                network.requests
            )

            cancelled = http.get(
                f"/atproto/login?txn_id={sign_in.txn_id}&error=denied",
                follow_redirects=False,
            )
            assert cancelled.status_code == 200
            assert "Sign-in was cancelled." in cancelled.text
            assert network.par_attempts == 1

            finished = _callback(
                sign_in, code=network.approve(state), state=state, iss=ISSUER
            )
            assert finished.status_code == 302
            assert finished.headers["location"].startswith(CLIENT_REDIRECT)

    def test_single_allowed_did_shows_the_page_when_its_pds_fails(
        self, network: FakeATProtoNetwork
    ) -> None:
        network.as_issuer = "https://someone-else.test"
        with TestClient(_app(_provider()), base_url=SERVER) as http:
            sign_in = _begin(http)
            page = http.get(f"/atproto/login?txn_id={sign_in.txn_id}")
        assert page.status_code == 400
        assert "couldn't start sign-in" in html.unescape(page.text)
        assert network.par_attempts == 0

    def test_page_offers_typeahead_and_scrubs_errors(
        self, network: FakeATProtoNetwork
    ) -> None:
        with TestClient(_app(_provider()), base_url=SERVER) as http:
            sign_in = _begin(http)
            page = http.get(f"/atproto/login?txn_id={sign_in.txn_id}&error=denied")
        assert "Sign-in was cancelled." in page.text
        assert 'data-endpoint="https://typeahead.waow.tech/xrpc/' in page.text
        assert "connect-src https://typeahead.waow.tech" in page.text
        assert "form-action" not in page.text
        assert page.headers["x-frame-options"] == "DENY"

    def test_disallowed_account_is_refused_before_redirect(
        self, network: FakeATProtoNetwork
    ) -> None:
        provider = _provider(allowed_dids=[OTHER_DID])
        with TestClient(_app(provider), base_url=SERVER) as http:
            sign_in = _begin(http)
            response = _submit_handle(sign_in, HANDLE)
        assert response.status_code == 400
        assert "That account isn't allowed to use this server." in html.unescape(
            response.text
        )
        assert network.par_attempts == 0
        assert not any(url.startswith(ISSUER) for _, url in network.requests)

    def test_unknown_handle_is_reported_on_the_page(
        self, network: FakeATProtoNetwork
    ) -> None:
        network.well_known_did = None
        with TestClient(_app(_provider()), base_url=SERVER) as http:
            sign_in = _begin(http)
            response = _submit_handle(sign_in, HANDLE)
        assert response.status_code == 400
        assert "Couldn't find that handle." in html.unescape(response.text)
        assert f'value="{HANDLE}"' in response.text

    def test_login_requires_the_browser_that_approved_consent(
        self, network: FakeATProtoNetwork
    ) -> None:
        app = _app(_provider())
        with TestClient(app, base_url=SERVER) as http:
            sign_in = _begin(http)
        with TestClient(app, base_url=SERVER) as other_browser:
            page = other_browser.get(f"/atproto/login?txn_id={sign_in.txn_id}")
            posted = other_browser.post(
                "/atproto/login",
                data={"txn_id": sign_in.txn_id, "identifier": HANDLE},
                follow_redirects=False,
            )
        assert page.status_code == 403
        assert posted.status_code == 403
        assert network.par_attempts == 0

    def test_callback_rejects_wrong_issuer(self, network: FakeATProtoNetwork) -> None:
        with TestClient(_app(_provider()), base_url=SERVER) as http:
            sign_in = _begin(http)
            _submit_handle(sign_in, HANDLE)
            [state] = network.pars
            response = _callback(
                sign_in,
                code=network.approve(state),
                state=state,
                iss="https://evil.test",
            )
        assert response.status_code == 303
        assert "error=server_unavailable" in response.headers["location"]
        assert ("POST", f"{ISSUER}/oauth/token") not in network.requests

    def test_callback_rejects_a_different_subject(
        self, network: FakeATProtoNetwork
    ) -> None:
        network.sub = OTHER_DID
        with TestClient(_app(_provider()), base_url=SERVER) as http:
            sign_in = _begin(http)
            _submit_handle(sign_in, HANDLE)
            [state] = network.pars
            response = _callback(
                sign_in, code=network.approve(state), state=state, iss=ISSUER
            )
        assert response.status_code == 303
        assert "error=identity_unavailable" in response.headers["location"]
        assert network.revoked == ["pds-refresh"]

    def test_cancelled_sign_in_returns_to_the_login_page(
        self, network: FakeATProtoNetwork
    ) -> None:
        with TestClient(_app(_provider()), base_url=SERVER) as http:
            sign_in = _begin(http)
            _submit_handle(sign_in, HANDLE)
            [state] = network.pars
            response = _callback(
                sign_in, error="access_denied", state=state, iss=ISSUER
            )
            assert response.status_code == 303
            page = http.get(response.headers["location"])
        assert "Sign-in was cancelled." in page.text

    def test_callback_state_is_single_use(self, network: FakeATProtoNetwork) -> None:
        with TestClient(_app(_provider()), base_url=SERVER) as http:
            sign_in = _begin(http)
            _submit_handle(sign_in, HANDLE)
            [state] = network.pars
            code = network.approve(state)
            assert (
                _callback(sign_in, code=code, state=state, iss=ISSUER).status_code
                == 302
            )
            replay = _callback(sign_in, code=code, state=state, iss=ISSUER)
        assert replay.status_code == 400
        assert "expired" in replay.text


class TestIdentityTokens:
    async def test_refresh_mints_a_new_token_and_rechecks_the_allowlist(self) -> None:
        provider = _provider()
        tokens = provider._mint_identity_tokens(
            did=DID, handle=HANDLE, client_id="client", scopes=[]
        )
        refreshed = provider._refresh_identity(tokens["refresh_token"])
        assert refreshed["refresh_token"] == tokens["refresh_token"]
        verified = await provider._token_validator.verify_token(
            refreshed["access_token"]
        )
        assert verified is not None
        assert verified.subject == DID
        assert verified.claims["handle"] == HANDLE

        with pytest.raises(Exception, match="invalid_grant"):
            provider._refresh_identity(tokens["access_token"])

        locked_out = _provider(allowed_dids=[OTHER_DID])
        with pytest.raises(Exception, match="invalid_grant"):
            locked_out._refresh_identity(
                locked_out._mint_identity_tokens(
                    did=DID, handle=HANDLE, client_id="client", scopes=[]
                )["refresh_token"]
            )

    async def test_verifier_rechecks_allowlist_on_every_request(self) -> None:
        provider = _provider()
        token = provider._mint_identity_tokens(
            did=DID, handle=HANDLE, client_id="client", scopes=[]
        )["access_token"]
        allowed = provider._token_validator
        assert isinstance(allowed, ATProtoIdentityVerifier)
        assert await allowed.verify_token(token) is not None

        provider._allowed_dids._dids = frozenset({OTHER_DID})
        assert await allowed.verify_token(token) is None

    async def test_refresh_tokens_are_not_access_tokens(self) -> None:
        provider = _provider()
        refresh = provider._mint_identity_tokens(
            did=DID, handle=HANDLE, client_id="client", scopes=[]
        )["refresh_token"]
        assert await provider._token_validator.verify_token(refresh) is None


class TestConfiguration:
    def test_client_metadata_document(self) -> None:
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

    def test_loopback_uses_a_virtual_client_id(self) -> None:
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
    def test_rejects_unusable_base_urls(self, base_url: str) -> None:
        with pytest.raises(ValueError):
            _provider(base_url=base_url)

    def test_allowed_dids_must_be_dids(self) -> None:
        with pytest.raises(ValueError, match="allowed_dids must contain DIDs"):
            _provider(allowed_dids=[HANDLE])


class TestIdentityResolution:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("@Alice.Bsky.Social", "alice.bsky.social"),
            ("at://alice.bsky.social/", "alice.bsky.social"),
            ("  did:web:Example.com ", "did:web:Example.com"),
        ],
    )
    def test_normalize_identifier(self, raw: str, expected: str) -> None:
        assert normalize_identifier(raw) == expected

    async def test_resolves_handle_and_did_document(
        self, network: FakeATProtoNetwork
    ) -> None:
        did = await resolve_handle(HANDLE, handle_resolver_url=None)
        identity = await resolve_did(did, plc_directory_url="https://plc.directory")
        assert (identity.did, identity.handle, identity.pds_url) == (DID, HANDLE, PDS)

    async def test_unresolvable_handle(self, network: FakeATProtoNetwork) -> None:
        network.well_known_did = None
        with pytest.raises(ATProtoError) as excinfo:
            await resolve_handle(
                HANDLE, handle_resolver_url="https://public.api.bsky.app"
            )
        assert excinfo.value.reason == "handle_not_found"

    async def test_rejects_authorization_server_issuer_mismatch(
        self, network: FakeATProtoNetwork
    ) -> None:
        network.as_issuer = "https://someone-else.test"
        with pytest.raises(ATProtoError, match="issuer mismatch"):
            await discover_authorization_server(PDS)
