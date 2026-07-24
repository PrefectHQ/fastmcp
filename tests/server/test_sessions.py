"""Unit tests for the stateless session-state primitives.

Covers the principal helpers, the `(principal, session_id)` key scheme, and the
`Session` object's read-modify-write behavior against a real server store.
"""

import functools
import json
from collections.abc import Iterator
from contextlib import contextmanager

from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken as SDKAccessToken
from mcp.server.auth.provider import principal_components

from fastmcp.server.server import FastMCP
from fastmcp.server.sessions import (
    Session,
    SessionId,
    current_principal,
    session_id_parameter_names,
    session_storage_key,
)


def make_token(
    *, subject: str = "user-a", client_id: str = "client-1"
) -> SDKAccessToken:
    return SDKAccessToken(
        token="opaque",
        client_id=client_id,
        scopes=[],
        subject=subject,
        claims={"iss": "https://issuer.example"},
    )


def principal_string(token: SDKAccessToken) -> str:
    return json.dumps(principal_components(token), separators=(",", ":"))


@contextmanager
def as_principal(token: SDKAccessToken | None) -> Iterator[None]:
    if token is None:
        yield
        return
    reset = auth_context_var.set(AuthenticatedUser(token))
    try:
        yield
    finally:
        auth_context_var.reset(reset)


def make_session(server: FastMCP, principal: str | None, session_id: str) -> Session:
    return Session(
        store=server._state_store, principal=principal, session_id=session_id
    )


class TestPrincipalHelpers:
    def test_current_principal_none_without_auth(self):
        assert current_principal() is None

    def test_current_principal_encodes_triple(self):
        token = make_token()
        with as_principal(token):
            assert current_principal() == principal_string(token)


class TestStorageKey:
    def test_principal_is_the_isolation_wall(self):
        principal_a = principal_string(make_token(subject="user-a"))
        principal_b = principal_string(make_token(subject="user-b"))
        # Same session id, different principals -> different keys.
        assert session_storage_key(principal_a, "s1") != session_storage_key(
            principal_b, "s1"
        )

    def test_id_organizes_within_a_principal(self):
        principal = principal_string(make_token())
        assert session_storage_key(principal, "s1") != session_storage_key(
            principal, "s2"
        )

    def test_unauthenticated_collapses_to_shared_namespace(self):
        assert session_storage_key(None, "s1").startswith("session:anon:")

    def test_principal_not_embedded_verbatim(self):
        principal = principal_string(make_token())
        # The principal is hashed into a fixed segment, never embedded raw.
        assert principal not in session_storage_key(principal, "s1")


class TestSessionRoundTrip:
    async def test_set_get_delete(self):
        server = FastMCP("test")
        session = make_session(server, None, "s1")
        assert await session.get("missing") is None
        assert await session.get("missing", default=[]) == []

        await session.set("cart", ["apple"])
        assert await session.get("cart") == ["apple"]

        await session.delete("cart")
        assert await session.get("cart") is None

    async def test_multiple_keys_share_one_dict(self):
        server = FastMCP("test")
        session = make_session(server, None, "s1")
        await session.set("a", 1)
        await session.set("b", 2)
        assert await session.get("a") == 1
        assert await session.get("b") == 2

    async def test_clear_removes_everything(self):
        server = FastMCP("test")
        session = make_session(server, None, "s1")
        await session.set("a", 1)
        await session.set("b", 2)
        await session.clear()
        assert await session.get("a") is None
        assert await session.get("b") is None

    async def test_delete_missing_key_is_a_noop(self):
        server = FastMCP("test")
        session = make_session(server, None, "s1")
        await session.delete("nope")  # does not raise
        assert await session.get("nope") is None


class TestSessionIdProperty:
    def test_id_is_none_without_a_public_id(self):
        # An injected `UserSession` is built this way — no distinct public id.
        server = FastMCP("test")
        assert make_session(server, None, "s1").id is None

    def test_id_returns_the_public_id(self):
        server = FastMCP("test")
        session = Session(
            store=server._state_store,
            principal=None,
            session_id="s1",
            public_id="s1",
        )
        assert session.id == "s1"


class TestSessionIsolation:
    async def test_distinct_ids_are_isolated(self):
        server = FastMCP("test")
        principal = principal_string(make_token())
        await make_session(server, principal, "s1").set("cart", ["apple"])
        assert await make_session(server, principal, "s2").get("cart") is None

    async def test_same_id_different_principals_are_isolated(self):
        server = FastMCP("test")
        principal_a = principal_string(make_token(subject="user-a"))
        principal_b = principal_string(make_token(subject="user-b"))
        await make_session(server, principal_a, "shared-id").set("cart", ["a-item"])
        # B passes the *same* session id but reaches its own empty bucket.
        assert await make_session(server, principal_b, "shared-id").get("cart") is None
        # A still sees its own data.
        assert await make_session(server, principal_a, "shared-id").get("cart") == [
            "a-item"
        ]


class TestSharedStore:
    async def test_sessions_share_the_one_server_store(self):
        """A second Session for the same key sees the first's writes."""
        server = FastMCP("test")
        await make_session(server, None, "s1").set("x", 42)
        # A freshly constructed handle for the same (principal, id) reads it back.
        assert await make_session(server, None, "s1").get("x") == 42


class TestFalsyValues:
    async def test_stored_falsy_value_is_not_treated_as_missing(self):
        server = FastMCP("test")
        session = make_session(server, None, "s1")
        await session.set("count", 0)
        await session.set("flag", False)
        assert await session.get("count", default=99) == 0
        assert await session.get("flag", default=True) is False


class TestLifecycleMarker:
    async def test_uncreated_session_does_not_exist(self):
        server = FastMCP("test")
        session = make_session(server, None, "s1")
        assert await session._exists() is False

    async def test_created_session_exists(self):
        server = FastMCP("test")
        session = make_session(server, None, "s1")
        await session._create()
        assert await session._exists() is True

    async def test_writing_state_does_not_clobber_the_marker(self):
        server = FastMCP("test")
        session = make_session(server, None, "s1")
        await session._create()
        # A user key literally named like the marker cannot collide with it,
        # because user state lives in a namespaced sub-dict.
        await session.set("_created", "user-value")
        await session.set("cart", ["apple"])
        await session.delete("cart")
        assert await session._exists() is True
        assert await session.get("_created") == "user-value"

    async def test_clear_keeps_the_session_but_empties_state(self):
        server = FastMCP("test")
        session = make_session(server, None, "s1")
        await session._create()
        await session.set("cart", ["apple"])
        await session.clear()
        assert await session._exists() is True
        assert await session.get("cart") is None

    async def test_end_removes_the_session_entirely(self):
        server = FastMCP("test")
        session = make_session(server, None, "s1")
        await session._create()
        await session.set("cart", ["apple"])
        await session.end()
        assert await session._exists() is False
        assert await session.get("cart") is None


class TestSessionIdParameterNames:
    def test_detects_plain_parameter(self):
        def tool(item: str, session_id: SessionId) -> None: ...

        assert session_id_parameter_names(tool) == ("session_id",)

    def test_none_when_absent(self):
        def tool(item: str) -> None: ...

        assert session_id_parameter_names(tool) == ()

    def test_partial_positional_binding_is_dropped(self):
        # A positionally bound leading argument is no longer part of the tool's
        # argument surface; the `session_id` that remains is still detected.
        def tool(item: str, session_id: SessionId) -> None: ...

        bound = functools.partial(tool, "apple")
        assert session_id_parameter_names(bound) == ("session_id",)

    def test_partial_binding_the_session_id_positionally_drops_it(self):
        def tool(session_id: SessionId, item: str) -> None: ...

        bound = functools.partial(tool, "s1")
        assert session_id_parameter_names(bound) == ()

    def test_partial_keyword_binding_stays_detected(self):
        # A keyword-bound partial argument remains overridable by the caller, so
        # it is still in the tool's input schema — detection tracks the schema
        # and keeps populating its description.
        def tool(item: str, session_id: SessionId) -> None: ...

        bound = functools.partial(tool, session_id="s1")
        assert session_id_parameter_names(bound) == ("session_id",)
