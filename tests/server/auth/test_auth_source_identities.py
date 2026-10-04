"""Configured authentication sources retain stable principal identities."""

import json
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import parse_qs

import httpx2
import pytest
from fastmcp_tasks.context import (
    TaskContextSnapshot,
    _apply_snapshot_to_context,
    get_task_scope,
)
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import (
    AuthenticatedUser,
    authorization_context,
)
from mcp.server.auth.provider import AccessToken as SDKAccessToken
from mcp.server.auth.provider import principal_components
from mcp.server.context import ServerRequestContext
from mcp.server.request_state import authenticated_principal
from mcp_types import InputRequiredResult
from starlette.testclient import TestClient

from fastmcp import Context, FastMCP
from fastmcp.server.auth import AccessToken, MultiAuth, TokenVerifier
from fastmcp.server.auth.providers.introspection import IntrospectionTokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.sessions import UserSession, current_principal
from fastmcp.utilities.tests import asgi_server
from tests.server.http.test_session_owner_enforcement import (
    INITIALIZE_REQUEST,
    MCP_HEADERS,
    TOOLS_LIST_REQUEST,
)
from tests.server.test_mrtr_guards import _ask, _elicit


class StoredVerifier(TokenVerifier):
    def __init__(self, bearer: str) -> None:
        super().__init__()
        self.bearer = bearer
        self.result = AccessToken(
            token=bearer,
            client_id="shared",
            subject="reader",
            scopes=[],
            claims={"iss": "same", "sub": "reader"},
            original_client_id="supplied",
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        return self.result if token == self.bearer else None


async def test_named_sources_preserve_raw_fields_and_source_token() -> None:
    first, second = StoredVerifier("one"), StoredVerifier("two")
    auth = MultiAuth(verifiers={"company": first, "partner": second})
    a, b = await auth.verify_token("one"), await auth.verify_token("two")
    assert a is not None and b is not None
    assert a is not first.result
    assert first.result.client_id == "shared"
    assert first.result.original_client_id == "supplied"
    assert json.loads(a.client_id) == ["company", "shared", "same", "reader"]
    assert json.loads(b.client_id) == ["partner", "shared", "same", "reader"]
    assert a.original_client_id == b.original_client_id == "shared"
    assert principal_components(a) != principal_components(b)
    assert a.claims == first.result.claims
    assert a.subject == first.result.subject
    assert a.token == first.result.token


async def test_source_order_and_reconstruction_keep_identities() -> None:
    first, second = StoredVerifier("one"), StoredVerifier("two")
    left = MultiAuth(verifiers={"company": first, "partner": second})
    right = MultiAuth(verifiers={"partner": second, "company": first})
    a, b = await left.verify_token("one"), await right.verify_token("one")
    assert a is not None and b is not None
    assert principal_components(a) == principal_components(b)


async def test_nested_sources_keep_the_inner_namespace() -> None:
    inner = MultiAuth(verifiers={"company": StoredVerifier("one")})
    outer = MultiAuth(server=inner, server_source_id="interactive")
    result = await outer.verify_token("one")
    assert result is not None
    outer_name, inner_id, issuer, subject = json.loads(result.client_id)
    assert subject == "reader"
    assert issuer == "same"
    assert outer_name == "interactive"
    assert json.loads(inner_id) == ["company", "shared", "same", "reader"]
    assert result.original_client_id == "shared"


@pytest.mark.parametrize("name", ["", " ", " company"])
def test_names_are_nonempty_and_unambiguous(name: str) -> None:
    with pytest.raises(ValueError, match="source IDs"):
        MultiAuth(verifiers={name: StoredVerifier("one")})


def test_opaque_sources_are_stable_and_duplicates_require_names() -> None:
    first = MultiAuth(verifiers=StoredVerifier("one"))
    restarted = MultiAuth(verifiers=StoredVerifier("new-credentials"))
    assert first._source_ids == restarted._source_ids
    with pytest.raises(ValueError, match="unique"):
        MultiAuth(verifiers=[StoredVerifier("one"), StoredVerifier("two")])


def test_configured_source_ids_are_stable_and_duplicates_require_names() -> None:
    first = JWTVerifier(
        jwks_uri="https://one.example/keys", issuer="https://one.example"
    )
    second = JWTVerifier(
        jwks_uri="https://two.example/keys", issuer="https://two.example"
    )
    a, b = MultiAuth(verifiers=[first, second]), MultiAuth(verifiers=[second, first])
    assert a._source_ids == list(reversed(b._source_ids))
    assert MultiAuth(verifiers=first)._source_ids == a._source_ids[:1]
    with pytest.raises(ValueError, match="unique"):
        MultiAuth(verifiers=[first, first])


async def test_ownership_consumers_agree_and_snapshot_preserves_raw_token() -> None:
    auth = MultiAuth(
        verifiers={"company": StoredVerifier("one"), "partner": StoredVerifier("two")}
    )
    tokens = [await auth.verify_token(t) for t in ["one", "two"]]
    contexts = []
    identities = []
    task_scopes = []
    for token in tokens:
        assert token is not None
        marker = auth_context_var.set(AuthenticatedUser(token))
        try:
            assert get_access_token() is token
            identities.append(current_principal())
            assert current_principal() == authenticated_principal(
                cast(ServerRequestContext[Any, Any], SimpleNamespace())
            )
            task_scopes.append(get_task_scope())
            contexts.append(authorization_context(AuthenticatedUser(token)))
            restored = AccessToken.model_validate_json(token.model_dump_json())
            assert principal_components(restored) == principal_components(token)
            snapshot = TaskContextSnapshot(access_token_json=token.model_dump_json())
            _apply_snapshot_to_context(snapshot)
            assert current_principal() == identities[-1]
            restored_access_token = get_access_token()
            assert restored_access_token is not None
            assert restored_access_token.claims == token.claims
            assert restored_access_token.original_client_id == "shared"
            assert get_task_scope() == task_scopes[-1]
        finally:
            auth_context_var.reset(marker)
    assert identities[0] != identities[1]
    assert contexts[0] != contexts[1]
    assert task_scopes[0] != task_scopes[1]


def test_plain_token_identity_retains_legacy_representation() -> None:
    token = AccessToken(
        token="one",
        client_id="client",
        subject="reader",
        scopes=[],
        claims={"iss": "issuer", "sub": "reader"},
    )
    assert principal_components(token) == ("client", "issuer", "reader")
    marker = auth_context_var.set(AuthenticatedUser(token))
    try:
        assert get_task_scope() == "client|reader"
        assert authorization_context(AuthenticatedUser(token)) == {
            "client_id": "client",
            "issuer": "issuer",
            "subject": "reader",
        }
    finally:
        auth_context_var.reset(marker)


async def test_introspection_endpoints_keep_session_and_transport_ownership_separate() -> (
    None
):
    def respond(request: httpx2.Request) -> httpx2.Response:
        active = (
            request.url.host == "one.example" and b"token=one" in request.content
        ) or (request.url.host == "two.example" and b"token=two" in request.content)
        return httpx2.Response(
            200, json={"active": active, "client_id": "shared", "scope": ""}
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        sources = [
            IntrospectionTokenVerifier(
                introspection_url="https://" + host + "/introspect",
                client_id="application",
                client_secret="synthetic",
                http_client=http,
            )
            for host in ["one.example", "two.example"]
        ]
        auth = MultiAuth(verifiers=sources)
        server = FastMCP("Configured sources", auth=auth)

        @server.tool
        async def store(value: str, session: UserSession) -> str:
            await session.set("value", value)
            return value

        @server.tool
        async def read(session: UserSession) -> str | None:
            return await session.get("value")

        app = server.http_app(stateless_http=True, json_response=True)
        with TestClient(app, base_url="http://127.0.0.1") as client:

            def call(token: str, name: str, args: dict[str, Any]) -> Any:
                response = client.post(
                    "/mcp",
                    headers={**MCP_HEADERS, "authorization": "Bearer " + token},
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": args},
                    },
                )
                assert response.status_code == 200
                return response.json()["result"]["structuredContent"]["result"]

            assert call("one", "store", {"value": "saved"}) == "saved"
            assert call("two", "read", {}) is None
            assert call("two", "store", {"value": "other"}) == "other"
            assert call("one", "read", {}) == "saved"

        app = server.http_app(stateless_http=False, json_response=True)
        with TestClient(app, base_url="http://127.0.0.1") as client:
            initialized = client.post(
                "/mcp",
                headers={**MCP_HEADERS, "authorization": "Bearer one"},
                json=INITIALIZE_REQUEST,
            )
            assert initialized.status_code == 200
            session_id = initialized.headers["mcp-session-id"]
            response = client.post(
                "/mcp",
                headers={
                    **MCP_HEADERS,
                    "authorization": "Bearer two",
                    "mcp-session-id": session_id,
                    "mcp-protocol-version": "2024-11-05",
                },
                json=TOOLS_LIST_REQUEST,
            )
            assert response.status_code == 404


async def test_claims_only_subjects_keep_user_ownership_separate() -> None:
    verifier = StoredVerifier("one")
    verifier.result.subject = None
    verifier.result.claims["sub"] = "alice"
    auth = MultiAuth(verifiers={"company": verifier})
    alice = await auth.verify_token("one")
    verifier.result.claims = {**verifier.result.claims, "sub": "bob"}
    bob = await auth.verify_token("one")
    assert alice is not None and bob is not None
    assert alice.subject is None and bob.subject is None
    scopes = []
    for token in (alice, bob):
        marker = auth_context_var.set(AuthenticatedUser(token))
        try:
            scopes.append(get_task_scope())
        finally:
            auth_context_var.reset(marker)
    assert scopes[0] != scopes[1]


@pytest.fixture
async def configured_auth() -> AsyncGenerator[MultiAuth, None]:
    def respond(request: httpx2.Request) -> httpx2.Response:
        bearer = parse_qs(request.content.decode())["token"][0]
        source = (request.url.host or "").split(".")[0]
        return httpx2.Response(
            200,
            json={
                "active": bearer == source,
                "client_id": "shared",
                "scope": "",
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        yield MultiAuth(
            verifiers=[
                IntrospectionTokenVerifier(
                    introspection_url=f"https://{name}.example/introspect",
                    client_id="application",
                    client_secret="synthetic",
                    http_client=http,
                )
                for name in ("one", "two")
            ]
        )


async def test_configured_sources_keep_sse_message_ownership_separate(
    configured_auth: MultiAuth,
) -> None:
    server = FastMCP("SSE identities", auth=configured_auth)
    async with asgi_server(server, transport="sse") as running:
        async with running.http_client() as http:
            async with http.stream(
                "GET",
                running.url,
                headers={"accept": "text/event-stream", "authorization": "Bearer one"},
            ) as response:
                assert response.status_code == 200
                message_path = None
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        message_path = line.removeprefix("data: ")
                        break
                assert message_path is not None
                message_url = str(httpx2.URL(running.url).join(message_path))
                other = await http.post(
                    message_url,
                    headers={"authorization": "Bearer two"},
                    json=INITIALIZE_REQUEST,
                )
                assert other.status_code == 404
                same = await http.post(
                    message_url,
                    headers={"authorization": "Bearer one"},
                    json=INITIALIZE_REQUEST,
                )
                assert same.status_code == 202


async def test_configured_sources_keep_saved_request_state_separate(
    configured_auth: MultiAuth,
) -> None:
    server = FastMCP("Continued requests", auth=configured_auth)

    @server.tool
    async def continue_work(ctx: Context) -> str | InputRequiredResult:
        if ctx.request_state is not None:
            return ctx.request_state
        return _ask(
            _elicit("confirmation", "Continue?", "answer"), "confirmation", "saved"
        )

    app = server.http_app(stateless_http=True, json_response=True)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = {
            **MCP_HEADERS,
            "mcp-protocol-version": "2026-07-28",
            "mcp-method": "tools/call",
            "mcp-name": "continue_work",
        }
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "continue_work",
                "arguments": {},
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {
                        "elicitation": {"form": {}}
                    },
                },
            },
        }
        first = client.post(
            "/mcp", headers={**headers, "authorization": "Bearer one"}, json=request
        )
        assert first.status_code == 200, first.text
        saved = first.json()["result"]["requestState"]
        assert saved != "saved"
        request["params"]["requestState"] = saved
        other = client.post(
            "/mcp", headers={**headers, "authorization": "Bearer two"}, json=request
        )
        assert "error" in other.json()
        same = client.post(
            "/mcp", headers={**headers, "authorization": "Bearer one"}, json=request
        )
        assert same.json()["result"]["content"][0]["text"] == "saved"


async def test_sdk_verifier_tokens_retain_original_client_id_after_serialization() -> (
    None
):
    sdk_result = SDKAccessToken(token="one", client_id="client", scopes=[])

    class SDKVerifier(TokenVerifier):
        async def verify_token(self, token: str) -> AccessToken | None:
            return cast(AccessToken, sdk_result)

    auth = MultiAuth(verifiers=SDKVerifier())
    token = await auth.verify_token("one")
    assert token is not None
    assert sdk_result.client_id == "client"
    restored = AccessToken.model_validate_json(token.model_dump_json())
    assert restored.original_client_id == "client"
    assert restored.client_id == token.client_id


async def test_source_token_subclass_fields_survive_qualification() -> None:
    class DetailedToken(AccessToken):
        tenant: str

    verifier = StoredVerifier("one")
    verifier.result = DetailedToken(
        token="one", client_id="client", scopes=[], tenant="company"
    )
    auth = MultiAuth(verifiers=verifier)
    token = await auth.verify_token("one")
    assert isinstance(token, DetailedToken)
    assert token.tenant == "company"
    assert verifier.result.client_id == "client"
    assert verifier.result.original_client_id is None
    assert token.original_client_id == "client"


async def test_configured_issuer_list_keeps_task_ownership_distinct(
    rsa_key_pair: RSAKeyPair,
) -> None:
    issuers = ["https://company.example", "https://partner.example"]
    verifier = JWTVerifier(public_key=rsa_key_pair.public_key, issuer=issuers)
    auth = MultiAuth(verifiers=verifier)
    scopes = []
    for issuer in issuers:
        bearer = rsa_key_pair.create_token(
            issuer=issuer, subject="reader", additional_claims={"client_id": "shared"}
        )
        token = await auth.verify_token(bearer)
        assert token is not None
        assert token.original_client_id == "shared"
        assert token.claims["iss"] == issuer
        marker = auth_context_var.set(AuthenticatedUser(token))
        try:
            scopes.append(get_task_scope())
        finally:
            auth_context_var.reset(marker)
    assert scopes[0] != scopes[1]


async def test_explicit_subjects_keep_task_ownership_distinct() -> None:
    verifier = StoredVerifier("one")
    verifier.result.claims = {"iss": "same"}
    auth = MultiAuth(verifiers={"company": verifier})
    scopes = []
    for subject in ("alice", "bob"):
        verifier.result.subject = subject
        token = await auth.verify_token("one")
        assert token is not None
        assert token.subject == subject
        assert "sub" not in token.claims
        marker = auth_context_var.set(AuthenticatedUser(token))
        try:
            scopes.append(get_task_scope())
        finally:
            auth_context_var.reset(marker)
    assert scopes[0] != scopes[1]
