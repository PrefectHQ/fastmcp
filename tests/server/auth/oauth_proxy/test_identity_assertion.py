"""Tests for server-side SEP-990 identity assertion (ID-JAG) support.

These exercise the OAuthProxy token endpoint end-to-end using a locally-minted
fake IdP JWT (a keypair is generated per test). The proxy's JWKS lookup is
served via httpx_mock, so no real network calls are made.
"""

import subprocess
import sys
import time

import httpx2
import pytest
from joserfc import jwk, jwt
from key_value.aio.stores.memory import MemoryStore
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from fastmcp import FastMCP
from fastmcp.server.auth import IdentityAssertion
from fastmcp.server.auth.identity_assertion import (
    ID_JAG_GRANT_PROFILE,
    ID_JAG_TYP,
    JWT_BEARER_GRANT_TYPE,
)
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.oauth_proxy.models import ProxyDCRClient
from fastmcp.server.auth.providers.jwt import RSAKeyPair
from tests.server.auth.oauth_proxy.conftest import MockTokenVerifier
from tests.utilities.httpx2_mock import HTTPXMock

BASE_URL = "https://myserver.com"
ISSUER = "https://login.acme-corp.com"
JWKS_URI = "https://login.acme-corp.com/jwks"
RESOURCE = f"{BASE_URL}/mcp"


def _b64url_json(value: object) -> str:
    """Base64url-encode a JSON value as a JWT segment (no padding)."""
    import base64
    import json

    raw = json.dumps(value).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _idp_jwks(key_pair: RSAKeyPair) -> dict:
    """Build a JWKS document from an RSA key pair's public key."""
    public_key = jwk.import_key(key_pair.public_key, "RSA")
    data = public_key.as_dict()
    data["kid"] = "idp-key-1"
    data["alg"] = "RS256"
    return {"keys": [data]}


def _mint_id_jag(
    key_pair: RSAKeyPair,
    *,
    issuer: str = ISSUER,
    audience: str = BASE_URL,
    subject: str = "employee@acme-corp.com",
    typ: str = ID_JAG_TYP,
    jti: str = "jti-1",
    expires_in: int = 120,
    scope: str | None = None,
    include_iat: bool = True,
    nbf_offset: int | None = None,
    client_id: str | None = "mcp-client",
    resource: str | None = RESOURCE,
    claim_overrides: dict | None = None,
) -> str:
    """Mint a fake ID-JAG JWT with full control over header and claims.

    `client_id` and `resource` default to values matching the standard test
    client and this server's resource URL — SEP-990 binds the assertion to
    both, and the exchange enforces the bindings. Pass `None` to omit.
    `claim_overrides` is merged in last, for injecting malformed values.
    """
    now = int(time.time())
    header = {"alg": "RS256", "typ": typ, "kid": "idp-key-1"}
    payload: dict = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "exp": now + expires_in,
        "jti": jti,
    }
    if client_id is not None:
        payload["client_id"] = client_id
    if resource is not None:
        payload["resource"] = resource
    if include_iat:
        payload["iat"] = now
    if nbf_offset is not None:
        payload["nbf"] = now + nbf_offset
    if scope is not None:
        payload["scope"] = scope
    if claim_overrides:
        payload.update(claim_overrides)
    signing_key = jwk.import_key(key_pair.private_key.get_secret_value(), "RSA")
    return jwt.encode(header, payload, signing_key, algorithms=["RS256"])


def _make_proxy(identity_assertion: IdentityAssertion | None) -> OAuthProxy:
    return OAuthProxy(
        upstream_authorization_endpoint="https://login.acme-corp.com/authorize",
        upstream_token_endpoint="https://login.acme-corp.com/token",
        upstream_client_id="upstream-client",
        upstream_client_secret="upstream-secret",
        token_verifier=MockTokenVerifier(),
        base_url=BASE_URL,
        jwt_signing_key="test-signing-key",
        client_storage=MemoryStore(),
        identity_assertion=identity_assertion,
    )


@pytest.fixture
def idp_key(rsa_key_pair: RSAKeyPair) -> RSAKeyPair:
    return rsa_key_pair


@pytest.fixture
def config() -> IdentityAssertion:
    # Explicit jwks_uris avoids OIDC discovery so only the JWKS fetch is mocked.
    return IdentityAssertion(
        trusted_issuers=[ISSUER],
        jwks_uris={ISSUER: JWKS_URI},
    )


async def _register_client(proxy: OAuthProxy) -> None:
    """Register the MCP client so the token endpoint can authenticate it."""
    await proxy.register_client(
        OAuthClientInformationFull(
            client_id="mcp-client",
            client_secret="mcp-secret",
            redirect_uris=[AnyUrl("http://localhost/callback")],
            grant_types=[JWT_BEARER_GRANT_TYPE],
        )
    )


async def _post_token(
    proxy: OAuthProxy,
    assertion: str,
    *,
    request_scope: str | None = None,
    resource: str | None = None,
    register: bool = True,
) -> httpx2.Response:
    """POST a jwt-bearer grant to the proxy's /token endpoint via an ASGI app.

    When ``register`` is True the standard test client is registered first; pass
    ``register=False`` to exercise a client the caller has already stored.
    """
    if register:
        await _register_client(proxy)
    app = FastMCP("ID-JAG Server", auth=proxy).http_app()
    transport = httpx2.ASGITransport(app=app)
    data = {
        "grant_type": JWT_BEARER_GRANT_TYPE,
        "assertion": assertion,
        "client_id": "mcp-client",
        "client_secret": "mcp-secret",
    }
    if request_scope is not None:
        data["scope"] = request_scope
    if resource is not None:
        data["resource"] = resource
    async with httpx2.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        return await client.post("/token", data=data)


class TestIdentityAssertionConfig:
    def test_requires_trusted_issuers(self):
        with pytest.raises(ValueError):
            IdentityAssertion(trusted_issuers=[])

    def test_rejects_blank_issuer(self):
        with pytest.raises(ValueError):
            IdentityAssertion(trusted_issuers=["  "])

    @pytest.mark.parametrize("algorithm", ["ES256", "PS256", "RS384"])
    def test_accepts_asymmetric_algorithm(self, algorithm: str):
        IdentityAssertion(trusted_issuers=[ISSUER], algorithm=algorithm)

    @pytest.mark.parametrize(
        "algorithm", ["HS256", "EdDSA", "none", "", "RS999", "ES999"]
    )
    def test_rejects_incompatible_or_unsupported_algorithm(self, algorithm: str):
        # HS* has no JWKS equivalent (shared secret, not a public key); EdDSA,
        # typo'd variants like RS999, and other unimportable algorithms would
        # otherwise surface as a 500 on the first exchange rather than a clean
        # config error now. The allowlist is exactly what JWTVerifier supports.
        with pytest.raises(ValueError):
            IdentityAssertion(trusted_issuers=[ISSUER], algorithm=algorithm)

    def test_defaults(self):
        cfg = IdentityAssertion(trusted_issuers=[ISSUER])
        assert cfg.access_token_expiry_seconds == 300
        assert cfg.audience is None
        assert cfg.jwks_uris is None

    def test_per_issuer_algorithms_validated(self):
        IdentityAssertion(trusted_issuers=[ISSUER], algorithms={ISSUER: "ES256"})
        with pytest.raises(ValueError):
            IdentityAssertion(trusted_issuers=[ISSUER], algorithms={ISSUER: "HS256"})

    @pytest.mark.subprocess_heavy
    def test_lazy_reexport_does_not_import_module(self):
        # fastmcp.server.auth must not load identity_assertion (and its
        # httpx2 dependency) eagerly — the re-export is lazy via __getattr__.
        code = (
            "import sys\n"
            "import fastmcp.server.auth\n"
            "loaded = [m for m in sys.modules if 'identity_assertion' in m]\n"
            "assert not loaded, f'eagerly loaded: {loaded}'\n"
            "from fastmcp.server.auth import IdentityAssertion\n"
            "print('OK')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout


class TestMetadataAdvertisement:
    async def _metadata(self, proxy: OAuthProxy) -> dict:
        app = FastMCP("ID-JAG Server", auth=proxy).http_app()
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url=BASE_URL) as client:
            resp = await client.get("/.well-known/oauth-authorization-server")
            return resp.json()

    async def test_advertises_grant_when_enabled(self, config: IdentityAssertion):
        proxy = _make_proxy(config)
        metadata = await self._metadata(proxy)
        assert JWT_BEARER_GRANT_TYPE in metadata["grant_types_supported"]
        assert (
            ID_JAG_GRANT_PROFILE in metadata["authorization_grant_profiles_supported"]
        )

    async def test_not_advertised_when_disabled(self):
        proxy = _make_proxy(None)
        metadata = await self._metadata(proxy)
        assert JWT_BEARER_GRANT_TYPE not in metadata["grant_types_supported"]
        assert metadata.get("authorization_grant_profiles_supported") is None

    async def test_advertises_none_auth_method_without_cimd(
        self, config: IdentityAssertion
    ):
        # DCR clients are public (token_endpoint_auth_method="none"), so when
        # the jwt-bearer grant is advertised, `none` must be advertised too —
        # even with CIMD (which also adds it) disabled.
        proxy = OAuthProxy(
            upstream_authorization_endpoint="https://login.acme-corp.com/authorize",
            upstream_token_endpoint="https://login.acme-corp.com/token",
            upstream_client_id="upstream-client",
            upstream_client_secret="upstream-secret",
            token_verifier=MockTokenVerifier(),
            base_url=BASE_URL,
            jwt_signing_key="test-signing-key",
            client_storage=MemoryStore(),
            identity_assertion=config,
            enable_cimd=False,
        )
        metadata = await self._metadata(proxy)
        assert JWT_BEARER_GRANT_TYPE in metadata["grant_types_supported"]
        assert "none" in metadata["token_endpoint_auth_methods_supported"]


class TestTokenEndpoint:
    async def test_happy_path_issues_token(
        self,
        idp_key: RSAKeyPair,
        config: IdentityAssertion,
        httpx_mock: HTTPXMock,
    ):
        httpx_mock.add_response(url=JWKS_URI, json=_idp_jwks(idp_key))
        proxy = _make_proxy(config)
        assertion = _mint_id_jag(idp_key, scope="read write")

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 200
        body = resp.json()
        assert body["token_type"] == "Bearer"
        assert body["access_token"]
        # SEP-990: no refresh token is issued.
        assert body.get("refresh_token") is None
        assert body["expires_in"] == config.access_token_expiry_seconds

    async def test_issued_token_carries_subject(
        self,
        idp_key: RSAKeyPair,
        config: IdentityAssertion,
        httpx_mock: HTTPXMock,
    ):
        httpx_mock.add_response(url=JWKS_URI, json=_idp_jwks(idp_key))
        proxy = _make_proxy(config)
        proxy.set_mcp_path("/mcp")
        assertion = _mint_id_jag(idp_key, subject="alice@acme-corp.com")

        resp = await _post_token(proxy, assertion)
        access_token = resp.json()["access_token"]

        # The FastMCP-issued token validates via the proxy and exposes the subject.
        loaded = await proxy.load_access_token(access_token)
        assert loaded is not None
        assert loaded.subject == "alice@acme-corp.com"

    async def test_asserted_subject_flows_into_auth_context(
        self,
        idp_key: RSAKeyPair,
        config: IdentityAssertion,
        httpx_mock: HTTPXMock,
    ):
        """The issued token, verified via the same path the bearer-auth middleware
        uses (`verify_token` -> `load_access_token`), exposes the asserted subject —
        which is exactly what `get_access_token()` returns to a tool."""
        httpx_mock.add_response(url=JWKS_URI, json=_idp_jwks(idp_key))
        proxy = _make_proxy(config)
        proxy.set_mcp_path("/mcp")
        assertion = _mint_id_jag(
            idp_key, subject="carol@acme-corp.com", scope="read write"
        )

        resp = await _post_token(proxy, assertion)
        access_token = resp.json()["access_token"]

        verified = await proxy.verify_token(access_token)
        assert verified is not None
        assert verified.subject == "carol@acme-corp.com"
        assert "read" in verified.scopes and "write" in verified.scopes

    async def test_grant_rejected_when_not_configured(
        self,
        idp_key: RSAKeyPair,
    ):
        proxy = _make_proxy(None)
        assertion = _mint_id_jag(idp_key)

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 400
        assert resp.json()["error"] == "unsupported_grant_type"

    async def test_request_scope_cannot_widen_assertion_scope(
        self,
        idp_key: RSAKeyPair,
        config: IdentityAssertion,
        httpx_mock: HTTPXMock,
    ):
        """A client whose assertion grants only `readonly` cannot obtain `admin`
        by asking for it at the token endpoint. The request `scope` is not covered
        by the signed assertion, so it may only narrow the granted set."""
        httpx_mock.add_response(url=JWKS_URI, json=_idp_jwks(idp_key))
        proxy = _make_proxy(config)
        proxy.set_mcp_path("/mcp")
        assertion = _mint_id_jag(idp_key, scope="readonly")

        resp = await _post_token(proxy, assertion, request_scope="admin")

        assert resp.status_code == 200
        verified = await proxy.verify_token(resp.json()["access_token"])
        assert verified is not None
        assert "admin" not in verified.scopes

    async def test_request_scope_narrows_assertion_scope(
        self,
        idp_key: RSAKeyPair,
        config: IdentityAssertion,
        httpx_mock: HTTPXMock,
    ):
        """When the request `scope` is a subset of the assertion's granted scopes,
        the issued token carries only the intersection."""
        httpx_mock.add_response(url=JWKS_URI, json=_idp_jwks(idp_key))
        proxy = _make_proxy(config)
        proxy.set_mcp_path("/mcp")
        assertion = _mint_id_jag(idp_key, scope="read write")

        resp = await _post_token(proxy, assertion, request_scope="read")

        assert resp.status_code == 200
        verified = await proxy.verify_token(resp.json()["access_token"])
        assert verified is not None
        assert "read" in verified.scopes
        assert "write" not in verified.scopes

    async def test_request_narrowing_preserves_required_scope(
        self,
        idp_key: RSAKeyPair,
        httpx_mock: HTTPXMock,
    ):
        """A configured `required_scope` the assertion grants must always ride on
        the issued token; the request `scope` may only narrow the optional
        remainder. Requesting `read` must not drop the mandatory `admin`."""
        httpx_mock.add_response(url=JWKS_URI, json=_idp_jwks(idp_key))
        config = IdentityAssertion(
            trusted_issuers=[ISSUER],
            jwks_uris={ISSUER: JWKS_URI},
            required_scopes=["admin"],
        )
        proxy = _make_proxy(config)
        proxy.set_mcp_path("/mcp")
        assertion = _mint_id_jag(idp_key, scope="admin read")

        resp = await _post_token(proxy, assertion, request_scope="read")

        assert resp.status_code == 200
        verified = await proxy.verify_token(resp.json()["access_token"])
        assert verified is not None
        assert "admin" in verified.scopes
        assert "read" in verified.scopes

    async def test_request_for_required_scope_only(
        self,
        idp_key: RSAKeyPair,
        httpx_mock: HTTPXMock,
    ):
        """Requesting only the required scope keeps it and narrows away the rest."""
        httpx_mock.add_response(url=JWKS_URI, json=_idp_jwks(idp_key))
        config = IdentityAssertion(
            trusted_issuers=[ISSUER],
            jwks_uris={ISSUER: JWKS_URI},
            required_scopes=["admin"],
        )
        proxy = _make_proxy(config)
        proxy.set_mcp_path("/mcp")
        assertion = _mint_id_jag(idp_key, scope="admin read")

        resp = await _post_token(proxy, assertion, request_scope="admin")

        assert resp.status_code == 200
        verified = await proxy.verify_token(resp.json()["access_token"])
        assert verified is not None
        assert "admin" in verified.scopes
        assert "read" not in verified.scopes


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
class TestValidationMatrix:
    @pytest.fixture(autouse=True)
    def _mock_jwks(self, idp_key: RSAKeyPair, httpx_mock: HTTPXMock):
        # Optional: several matrix tests reject before any JWKS fetch happens.
        httpx_mock.add_response(url=JWKS_URI, json=_idp_jwks(idp_key), is_optional=True)

    async def test_untrusted_issuer_rejected(
        self, idp_key: RSAKeyPair, config: IdentityAssertion
    ):
        proxy = _make_proxy(config)
        assertion = _mint_id_jag(idp_key, issuer="https://evil.example.com")

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_grant"

    async def test_wrong_audience_rejected(
        self, idp_key: RSAKeyPair, config: IdentityAssertion
    ):
        proxy = _make_proxy(config)
        assertion = _mint_id_jag(idp_key, audience="https://other-server.com")

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_grant"

    async def test_wrong_typ_rejected(
        self, idp_key: RSAKeyPair, config: IdentityAssertion
    ):
        proxy = _make_proxy(config)
        assertion = _mint_id_jag(idp_key, typ="JWT")

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_grant"

    async def test_expired_rejected(
        self, idp_key: RSAKeyPair, config: IdentityAssertion
    ):
        proxy = _make_proxy(config)
        assertion = _mint_id_jag(idp_key, expires_in=-10)

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_grant"

    async def test_replayed_jti_rejected(
        self, idp_key: RSAKeyPair, config: IdentityAssertion
    ):
        proxy = _make_proxy(config)
        assertion = _mint_id_jag(idp_key, jti="replay-me")

        first = await _post_token(proxy, assertion)
        second = await _post_token(proxy, assertion)

        assert first.status_code == 200
        assert second.status_code == 401
        assert second.json()["error"] == "invalid_grant"

    async def test_wrong_signature_rejected(
        self, config: IdentityAssertion, rsa_key_pair_2: RSAKeyPair
    ):
        # Sign with a different key than the one served in the JWKS.
        other_key = rsa_key_pair_2
        proxy = _make_proxy(config)
        assertion = _mint_id_jag(other_key)

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_grant"

    async def test_missing_required_scope_rejected(self, idp_key: RSAKeyPair):
        config = IdentityAssertion(
            trusted_issuers=[ISSUER],
            jwks_uris={ISSUER: JWKS_URI},
            required_scopes=["admin"],
        )
        proxy = _make_proxy(config)
        assertion = _mint_id_jag(idp_key, scope="read")

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_grant"

    async def test_future_nbf_rejected(
        self, idp_key: RSAKeyPair, config: IdentityAssertion
    ):
        # An ID-JAG whose not-before (`nbf`) claim is in the future is not yet
        # valid and must be rejected.
        proxy = _make_proxy(config)
        assertion = _mint_id_jag(idp_key, nbf_offset=300)

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_grant"

    async def test_past_nbf_accepted(
        self, idp_key: RSAKeyPair, config: IdentityAssertion
    ):
        # An `nbf` in the past means the assertion is already valid.
        proxy = _make_proxy(config)
        assertion = _mint_id_jag(idp_key, nbf_offset=-60)

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 200
        assert resp.json()["access_token"]

    async def test_aud_matching_advertised_issuer_with_trailing_slash(
        self, idp_key: RSAKeyPair, config: IdentityAssertion
    ):
        # Metadata advertises the issuer exactly as pydantic renders base_url —
        # a bare domain gains a trailing slash. An IdP that sets `aud` to that
        # advertised value verbatim must be accepted, not rejected on the slash.
        proxy = _make_proxy(config)
        assertion = _mint_id_jag(idp_key, audience=f"{BASE_URL}/")

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 200

    @pytest.mark.parametrize("claim", ["exp", "iat", "nbf"])
    @pytest.mark.parametrize("bad_value", ["not-a-number", [], {}, True])
    async def test_non_numeric_temporal_claim_rejected(
        self,
        idp_key: RSAKeyPair,
        config: IdentityAssertion,
        claim: str,
        bad_value: object,
    ):
        # A validly-signed assertion could still carry a malformed exp/iat/nbf
        # (a misbehaving IdP); comparing against it must map to invalid_grant,
        # not an unhandled TypeError. `True`/`False` are excluded even though
        # bool subclasses int in Python.
        proxy = _make_proxy(config)
        assertion = _mint_id_jag(idp_key, claim_overrides={claim: bad_value})

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_grant"

    @pytest.mark.parametrize("bad_jti", [["a", "b"], {"x": 1}, 42])
    async def test_non_string_jti_rejected(
        self,
        idp_key: RSAKeyPair,
        config: IdentityAssertion,
        bad_jti: object,
    ):
        # An array/object jti is unhashable — the cache lookup would raise
        # TypeError (a 500) instead of a clean invalid_grant.
        proxy = _make_proxy(config)
        assertion = _mint_id_jag(idp_key, claim_overrides={"jti": bad_jti})

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_grant"

    async def test_non_object_payload_rejected(
        self, idp_key: RSAKeyPair, config: IdentityAssertion
    ):
        # A JWT with a valid typ header but a JSON-array payload must map to a
        # clean invalid_grant, not an unhandled 500 from calling `.get()` on a list.
        header = _b64url_json({"alg": "RS256", "typ": ID_JAG_TYP, "kid": "idp-key-1"})
        payload = _b64url_json([])
        assertion = f"{header}.{payload}.signature"

        resp = await _post_token(proxy=_make_proxy(config), assertion=assertion)

        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_grant"

    async def test_non_object_header_rejected(
        self, idp_key: RSAKeyPair, config: IdentityAssertion
    ):
        # Same class of bug as the payload case: a JSON-array JOSE header must
        # map to invalid_grant, not a 500 from `.get()` on a list.
        header = _b64url_json([])
        payload = _b64url_json({"iss": ISSUER, "sub": "x"})
        assertion = f"{header}.{payload}.signature"

        resp = await _post_token(proxy=_make_proxy(config), assertion=assertion)

        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_grant"

    async def test_assertion_for_other_client_rejected(
        self, idp_key: RSAKeyPair, config: IdentityAssertion
    ):
        # SEP-990: the IdP signs which client the assertion was minted for.
        # A registered client must not be able to redeem an assertion minted
        # for a different client — with public clients, this signed binding is
        # the control that stops cross-client redemption of leaked assertions.
        assertion = _mint_id_jag(idp_key, client_id="some-other-client")

        resp = await _post_token(proxy=_make_proxy(config), assertion=assertion)

        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_grant"

    async def test_wrong_client_binding_does_not_consume_jti(
        self, idp_key: RSAKeyPair, config: IdentityAssertion
    ):
        # An assertion presented by the wrong client must be rejected WITHOUT
        # its jti being recorded as consumed -- otherwise the client it
        # actually belongs to would find that same jti already "replayed"
        # when it (or a retry) presents a correctly-bound assertion. Two
        # distinct, validly-signed tokens sharing one jti value is exactly
        # the scenario jti replay tracking cares about, regardless of what
        # else differs between them.
        proxy = _make_proxy(config)
        shared_jti = "jti-shared-client"
        wrong_client = _mint_id_jag(
            idp_key, client_id="some-other-client", jti=shared_jti
        )

        rejected = await _post_token(proxy, wrong_client)
        assert rejected.status_code == 401
        assert rejected.json()["error"] == "invalid_grant"

        correct_client = _mint_id_jag(idp_key, client_id="mcp-client", jti=shared_jti)
        accepted = await _post_token(proxy, correct_client, register=False)
        assert accepted.status_code == 200

    async def test_wrong_resource_binding_does_not_consume_jti(
        self, idp_key: RSAKeyPair, config: IdentityAssertion
    ):
        proxy = _make_proxy(config)
        shared_jti = "jti-shared-resource"
        wrong_resource = _mint_id_jag(
            idp_key,
            resource="https://other-server.example.com/mcp",
            jti=shared_jti,
        )

        rejected = await _post_token(proxy, wrong_resource)
        assert rejected.status_code == 401
        assert rejected.json()["error"] == "invalid_grant"

        correct_resource = _mint_id_jag(idp_key, jti=shared_jti)  # default = RESOURCE
        accepted = await _post_token(proxy, correct_resource, register=False)
        assert accepted.status_code == 200

    async def test_assertion_without_client_id_rejected(
        self, idp_key: RSAKeyPair, config: IdentityAssertion
    ):
        assertion = _mint_id_jag(idp_key, client_id=None)

        resp = await _post_token(proxy=_make_proxy(config), assertion=assertion)

        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_grant"

    async def test_assertion_for_other_resource_rejected(
        self, idp_key: RSAKeyPair, config: IdentityAssertion
    ):
        # The signed resource claim governs: an assertion minted for server A
        # must not be redeemable at server B behind the same IdP.
        assertion = _mint_id_jag(
            idp_key, resource="https://other-server.example.com/mcp"
        )

        resp = await _post_token(proxy=_make_proxy(config), assertion=assertion)

        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_grant"

    async def test_assertion_without_resource_rejected(
        self, idp_key: RSAKeyPair, config: IdentityAssertion
    ):
        # When the proxy knows its resource URL, an assertion that names no
        # resource cannot be audience-restricted per SEP-990 and is rejected.
        assertion = _mint_id_jag(idp_key, resource=None)

        resp = await _post_token(proxy=_make_proxy(config), assertion=assertion)

        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_grant"

    async def test_resource_mismatch_rejected(
        self,
        idp_key: RSAKeyPair,
        config: IdentityAssertion,
    ):
        # RFC 8707: a token request naming a different resource must get
        # invalid_target (mirrors the authorize() invariant), not a token
        # for this server. Rejection happens before any JWKS fetch.
        proxy = _make_proxy(config)
        assertion = _mint_id_jag(idp_key)

        resp = await _post_token(
            proxy, assertion, resource="https://other-server.example.com/mcp"
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_target"

    async def test_matching_resource_accepted(
        self,
        idp_key: RSAKeyPair,
        config: IdentityAssertion,
    ):
        # JWKS served by the class's autouse _mock_jwks fixture.
        proxy = _make_proxy(config)
        assertion = _mint_id_jag(idp_key)

        resp = await _post_token(proxy, assertion, resource=f"{BASE_URL}/mcp")

        assert resp.status_code == 200

    async def test_jti_cache_does_not_grow_past_capacity(
        self, idp_key: RSAKeyPair, config: IdentityAssertion
    ):
        # Once the JTI cache is full of still-valid entries, further fresh
        # assertions are rejected as overloaded WITHOUT being inserted, so the
        # cache never grows beyond its cap.
        proxy = _make_proxy(config)
        validator = proxy._identity_assertion_validator
        assert validator is not None
        validator._jti_cache_max_size = 2
        future = time.time() + 120
        validator._jti_cache = {"filler-a": future, "filler-b": future}

        for i in range(3):
            assertion = _mint_id_jag(idp_key, jti=f"fresh-{i}")
            resp = await _post_token(proxy, assertion)
            assert resp.status_code == 401
            assert resp.json()["error"] == "invalid_grant"

        assert len(validator._jti_cache) == 2


class TestAlgorithmConfig:
    """Non-RS256 issuers work when `algorithm` is configured.

    Lives outside TestValidationMatrix because that class's autouse
    `_mock_jwks` fixture pre-registers an RSA JWKS for the same URL, and
    pytest-httpx serves first-registered responses first.
    """

    async def test_es256_issuer_supported_via_algorithm_config(
        self, httpx_mock: HTTPXMock
    ):
        ec_key = jwk.ECKey.generate_key("P-256")
        jwks_entry = ec_key.as_dict(private=False)
        jwks_entry["kid"] = "idp-ec-1"
        jwks_entry["alg"] = "ES256"
        httpx_mock.add_response(url=JWKS_URI, json={"keys": [jwks_entry]})

        now = int(time.time())
        header = {"alg": "ES256", "typ": ID_JAG_TYP, "kid": "idp-ec-1"}
        payload = {
            "iss": ISSUER,
            "aud": BASE_URL,
            "sub": "employee@acme-corp.com",
            "exp": now + 120,
            "iat": now,
            "jti": "jti-es256-1",
            "client_id": "mcp-client",
            "resource": RESOURCE,
        }
        assertion = jwt.encode(header, payload, ec_key, algorithms=["ES256"])

        es_config = IdentityAssertion(
            trusted_issuers=[ISSUER],
            jwks_uris={ISSUER: JWKS_URI},
            algorithm="ES256",
        )
        resp = await _post_token(proxy=_make_proxy(es_config), assertion=assertion)
        assert resp.status_code == 200


class TestIssuerKeyDiscovery:
    async def test_jwks_discovered_via_oidc(
        self, idp_key: RSAKeyPair, httpx_mock: HTTPXMock
    ):
        # No explicit jwks_uris: the validator must discover the JWKS URI from
        # the issuer's OIDC configuration document.
        oidc_config_url = ISSUER.rstrip("/") + "/.well-known/openid-configuration"
        httpx_mock.add_response(url=oidc_config_url, json={"jwks_uri": JWKS_URI})
        httpx_mock.add_response(url=JWKS_URI, json=_idp_jwks(idp_key))

        proxy = _make_proxy(IdentityAssertion(trusted_issuers=[ISSUER]))
        assertion = _mint_id_jag(idp_key)

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 200
        assert resp.json()["access_token"]

    async def test_non_object_discovery_body_rejected(
        self, idp_key: RSAKeyPair, httpx_mock: HTTPXMock
    ):
        # A discovery endpoint returning valid JSON that isn't an object (e.g.
        # a bare array) must map to invalid_grant, not a 500 on `.get()`.
        oidc_config_url = ISSUER.rstrip("/") + "/.well-known/openid-configuration"
        httpx_mock.add_response(url=oidc_config_url, json=[])

        proxy = _make_proxy(IdentityAssertion(trusted_issuers=[ISSUER]))
        assertion = _mint_id_jag(idp_key)

        resp = await _post_token(proxy, assertion)

        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_grant"

    async def test_failed_discovery_backs_off(
        self, idp_key: RSAKeyPair, httpx_mock: HTTPXMock
    ):
        # Discovery runs before signature verification, so repeated garbage
        # with a trusted iss must not turn into an outbound HTTP call per
        # request: after a failure, subsequent requests fast-fail without
        # fetching until the cooldown elapses.
        oidc_config_url = ISSUER.rstrip("/") + "/.well-known/openid-configuration"
        httpx_mock.add_exception(
            httpx2.ConnectError("connection refused"), url=oidc_config_url
        )

        proxy = _make_proxy(IdentityAssertion(trusted_issuers=[ISSUER]))

        first = await _post_token(proxy, _mint_id_jag(idp_key, jti="jti-d1"))
        assert first.status_code == 401

        second = await _post_token(
            proxy, _mint_id_jag(idp_key, jti="jti-d2"), register=False
        )
        assert second.status_code == 401
        # Only the FIRST request hit the network; the second fast-failed
        # inside the cooldown window.
        assert len(httpx_mock.get_requests()) == 1


class TestRevocation:
    async def test_revoked_id_jag_token_rejected(
        self,
        idp_key: RSAKeyPair,
        httpx_mock: HTTPXMock,
    ):
        # ID-JAG access tokens are self-contained — nothing upstream knows
        # them, so revocation must be tracked locally. After revoke_token,
        # load_access_token rejects the token for its remaining lifetime.
        httpx_mock.add_response(url=JWKS_URI, json=_idp_jwks(idp_key))
        proxy = _make_proxy(
            IdentityAssertion(trusted_issuers=[ISSUER], jwks_uris={ISSUER: JWKS_URI})
        )
        resp = await _post_token(proxy, _mint_id_jag(idp_key))
        assert resp.status_code == 200
        issued = resp.json()["access_token"]

        loaded = await proxy.load_access_token(issued)
        assert loaded is not None

        await proxy.revoke_token(loaded)

        assert await proxy.load_access_token(issued) is None


class TestGrantTypeEnforcement:
    """The proxy dispatches the jwt-bearer grant itself, so it must enforce the
    registered-grant-type constraint the SDK would otherwise apply: only clients
    registered for the jwt-bearer grant may present an ID-JAG. DCR adds the grant
    to registered clients when identity assertion is enabled."""

    async def test_client_not_registered_for_jwt_bearer_rejected(
        self,
        idp_key: RSAKeyPair,
        config: IdentityAssertion,
    ):
        # The grant-type check rejects before any assertion validation, so no
        # JWKS fetch occurs.
        proxy = _make_proxy(config)
        # A client registered only for the standard grants (e.g. before identity
        # assertion was enabled). Stored directly so the DCR enabled-path does not
        # add jwt-bearer for us.
        await proxy._client_store.put(
            key="mcp-client",
            value=ProxyDCRClient(
                client_id="mcp-client",
                client_secret=None,
                redirect_uris=[AnyUrl("http://localhost/callback")],
                grant_types=["authorization_code", "refresh_token"],
                token_endpoint_auth_method="none",
            ),
        )
        assertion = _mint_id_jag(idp_key, scope="read")

        resp = await _post_token(proxy, assertion, register=False)

        assert resp.status_code == 400
        assert resp.json()["error"] == "unsupported_grant_type"

    async def test_dcr_adds_jwt_bearer_when_enabled(self, config: IdentityAssertion):
        proxy = _make_proxy(config)
        await proxy.register_client(
            OAuthClientInformationFull(
                client_id="dcr-client",
                redirect_uris=[AnyUrl("http://localhost/callback")],
                grant_types=["authorization_code", "refresh_token"],
            )
        )

        client = await proxy.get_client("dcr-client")

        assert client is not None
        assert JWT_BEARER_GRANT_TYPE in client.grant_types

    async def test_dcr_does_not_add_jwt_bearer_when_disabled(self):
        proxy = _make_proxy(None)
        await proxy.register_client(
            OAuthClientInformationFull(
                client_id="dcr-client",
                redirect_uris=[AnyUrl("http://localhost/callback")],
                grant_types=["authorization_code", "refresh_token"],
            )
        )

        client = await proxy.get_client("dcr-client")

        assert client is not None
        assert JWT_BEARER_GRANT_TYPE not in client.grant_types

    async def test_dcr_registered_client_can_exchange(
        self,
        idp_key: RSAKeyPair,
        config: IdentityAssertion,
        httpx_mock: HTTPXMock,
    ):
        """A client that registers via DCR without the jwt-bearer grant can still
        exchange an ID-JAG, because the enabled-path adds the grant on registration."""
        httpx_mock.add_response(url=JWKS_URI, json=_idp_jwks(idp_key))
        proxy = _make_proxy(config)
        await proxy.register_client(
            OAuthClientInformationFull(
                client_id="mcp-client",
                redirect_uris=[AnyUrl("http://localhost/callback")],
                grant_types=["authorization_code", "refresh_token"],
            )
        )
        assertion = _mint_id_jag(idp_key, scope="read")

        resp = await _post_token(proxy, assertion, register=False)

        assert resp.status_code == 200
        assert resp.json()["access_token"]
