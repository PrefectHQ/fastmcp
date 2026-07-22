"""Tests for the stateless session-state primitives.

Covers the `SessionCodec` security core (seal/unseal, expiry, principal binding)
and the scoped-state surface on `Context` (`Scope.REQUEST` / `USER` / `SESSION`).
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken as SDKAccessToken
from mcp.server.auth.provider import principal_components
from mcp.server.request_state import RequestStateSecurity

from fastmcp.exceptions import AuthorizationError
from fastmcp.server.context import Context
from fastmcp.server.server import FastMCP
from fastmcp.server.sessions import (
    DEFAULT_SESSION_TTL,
    InvalidSessionToken,
    NoActiveSessionError,
    Scope,
    SessionCodec,
    SessionIdentity,
    bind_session_identity,
    current_principal,
    get_active_session_identity,
    session_state_key,
    user_state_key,
)

KEY_A = b"a" * 32
KEY_B = b"b" * 32


def make_token(
    *,
    client_id: str = "client-1",
    subject: str = "user-a",
    issuer: str = "https://issuer.example",
) -> SDKAccessToken:
    return SDKAccessToken(
        token="opaque",
        client_id=client_id,
        scopes=[],
        subject=subject,
        claims={"iss": issuer},
    )


def principal_string(token: SDKAccessToken) -> str:
    return json.dumps(principal_components(token), separators=(",", ":"))


@contextmanager
def as_principal(token: SDKAccessToken | None) -> Iterator[None]:
    """Set the authenticated principal for the current context."""
    if token is None:
        yield
        return
    reset = auth_context_var.set(AuthenticatedUser(token))
    try:
        yield
    finally:
        auth_context_var.reset(reset)


@pytest.fixture
def codec() -> SessionCodec:
    return SessionCodec(RequestStateSecurity(keys=[KEY_A]))


class TestSessionCodecRoundTrip:
    def test_unauthenticated_round_trip(self, codec: SessionCodec):
        token = codec.seal("sess-1", None)
        identity = codec.unseal(token, request_principal=None)
        assert identity == SessionIdentity(session_id="sess-1", principal=None)

    def test_authenticated_round_trip(self, codec: SessionCodec):
        principal = principal_string(make_token())
        token = codec.seal("sess-1", principal)
        identity = codec.unseal(token, request_principal=principal)
        assert identity.session_id == "sess-1"
        assert identity.principal == principal

    def test_from_security_none_uses_ephemeral_key(self):
        codec = SessionCodec.from_security(None)
        token = codec.seal("sess-1", None)
        assert codec.unseal(token, request_principal=None).session_id == "sess-1"

    def test_reads_principal_from_auth_context(self, codec: SessionCodec):
        token_obj = make_token()
        principal = principal_string(token_obj)
        sealed = codec.seal("sess-1", principal)
        with as_principal(token_obj):
            identity = codec.unseal(sealed)
        assert identity.principal == principal


class TestSessionCodecRejection:
    def test_tampered_token_rejected(self, codec: SessionCodec):
        token = codec.seal("sess-1", None)
        # Flip a character in the sealed body.
        tampered = token[:-2] + ("A" if token[-1] != "A" else "B")
        with pytest.raises(InvalidSessionToken):
            codec.unseal(tampered, request_principal=None)

    def test_malformed_token_rejected(self, codec: SessionCodec):
        with pytest.raises(InvalidSessionToken):
            codec.unseal("not-a-real-token", request_principal=None)

    def test_foreign_key_rejected(self):
        minted = SessionCodec(RequestStateSecurity(keys=[KEY_A])).seal("sess-1", None)
        other = SessionCodec(RequestStateSecurity(keys=[KEY_B]))
        with pytest.raises(InvalidSessionToken):
            other.unseal(minted, request_principal=None)

    def test_expired_token_rejected(self, codec: SessionCodec, monkeypatch):
        import fastmcp.server.sessions as sessions_module

        base = 1_000_000.0
        monkeypatch.setattr(sessions_module.time, "time", lambda: base)
        token = codec.seal("sess-1", None)
        monkeypatch.setattr(
            sessions_module.time, "time", lambda: base + DEFAULT_SESSION_TTL + 1
        )
        with pytest.raises(InvalidSessionToken):
            codec.unseal(token, request_principal=None)

    def test_principal_a_rejected_under_principal_b(self, codec: SessionCodec):
        principal_a = principal_string(make_token(subject="user-a"))
        principal_b = principal_string(make_token(subject="user-b"))
        token = codec.seal("sess-1", principal_a)
        with pytest.raises(InvalidSessionToken):
            codec.unseal(token, request_principal=principal_b)

    def test_bound_token_rejected_when_unauthenticated(self, codec: SessionCodec):
        principal = principal_string(make_token())
        token = codec.seal("sess-1", principal)
        with pytest.raises(InvalidSessionToken):
            codec.unseal(token, request_principal=None)

    def test_unbound_token_rejected_under_auth(self, codec: SessionCodec):
        principal = principal_string(make_token())
        token = codec.seal("sess-1", None)
        with pytest.raises(InvalidSessionToken):
            codec.unseal(token, request_principal=principal)

    def test_rejection_reason_not_on_public_message(self, codec: SessionCodec):
        try:
            codec.unseal("garbage", request_principal=None)
        except InvalidSessionToken as exc:
            assert "malformed" not in str(exc)
            assert exc.reason == "malformed"
        else:  # pragma: no cover
            pytest.fail("expected InvalidSessionToken")


class TestSessionCodecConstruction:
    @pytest.mark.parametrize("ttl", [0, -1, float("inf"), float("nan")])
    def test_invalid_ttl_rejected(self, ttl):
        with pytest.raises(ValueError):
            SessionCodec(RequestStateSecurity(keys=[KEY_A]), ttl=ttl)


class TestPrincipalHelpers:
    def test_current_principal_none_without_auth(self):
        assert current_principal() is None

    def test_current_principal_encodes_triple(self):
        token = make_token()
        with as_principal(token):
            assert current_principal() == principal_string(token)

    def test_user_and_session_keys_are_distinct(self):
        principal = principal_string(make_token())
        identity = SessionIdentity(session_id="sess-1", principal=principal)
        assert user_state_key(principal, "cart") != session_state_key(identity, "cart")
        # The principal is hashed, not embedded verbatim.
        assert principal not in user_state_key(principal, "cart")


class TestBindSessionIdentity:
    def test_bind_and_read(self):
        assert get_active_session_identity() is None
        identity = SessionIdentity(session_id="sess-1", principal=None)
        with bind_session_identity(identity):
            assert get_active_session_identity() is identity
        assert get_active_session_identity() is None


class TestScopedStateRequest:
    async def test_default_scope_is_request(self):
        server = FastMCP("test")
        async with Context(fastmcp=server, session=MagicMock()) as ctx:
            await ctx.set_state("k", "v")
            assert await ctx.get_state("k") == "v"
            # Explicit REQUEST scope resolves to the same key.
            assert await ctx.get_state("k", scope=Scope.REQUEST) == "v"

    async def test_serializable_false_still_works_for_request(self):
        server = FastMCP("test")
        async with Context(fastmcp=server, session=MagicMock()) as ctx:
            sentinel = object()
            await ctx.set_state("obj", sentinel, serializable=False)
            assert await ctx.get_state("obj") is sentinel

    async def test_delete_request_scope(self):
        server = FastMCP("test")
        async with Context(fastmcp=server, session=MagicMock()) as ctx:
            await ctx.set_state("k", "v")
            await ctx.delete_state("k")
            assert await ctx.get_state("k") is None


class TestScopedStateUser:
    async def test_user_scope_requires_auth(self):
        server = FastMCP("test")
        async with Context(fastmcp=server) as ctx:
            with pytest.raises(AuthorizationError):
                await ctx.get_state("prefs", scope=Scope.USER)
            with pytest.raises(AuthorizationError):
                await ctx.set_state("prefs", "x", scope=Scope.USER)

    async def test_user_scope_round_trip_under_auth(self):
        server = FastMCP("test")
        async with Context(fastmcp=server) as ctx:
            with as_principal(make_token(subject="user-a")):
                await ctx.set_state("prefs", {"theme": "dark"}, scope=Scope.USER)
                assert await ctx.get_state("prefs", scope=Scope.USER) == {
                    "theme": "dark"
                }

    async def test_user_scope_isolates_principals(self):
        server = FastMCP("test")
        async with Context(fastmcp=server) as ctx:
            with as_principal(make_token(subject="user-a")):
                await ctx.set_state("prefs", "a-value", scope=Scope.USER)
            with as_principal(make_token(subject="user-b")):
                assert await ctx.get_state("prefs", scope=Scope.USER) is None

    async def test_user_scope_rejects_serializable_false(self):
        server = FastMCP("test")
        async with Context(fastmcp=server) as ctx:
            with as_principal(make_token()):
                with pytest.raises(ValueError):
                    await ctx.set_state(
                        "prefs", "x", scope=Scope.USER, serializable=False
                    )


class TestScopedStateSession:
    async def test_session_scope_without_session_errors(self):
        server = FastMCP("test")
        async with Context(fastmcp=server) as ctx:
            with pytest.raises(NoActiveSessionError):
                await ctx.get_state("cart", scope=Scope.SESSION)
            with pytest.raises(NoActiveSessionError):
                await ctx.set_state("cart", [], scope=Scope.SESSION)

    async def test_session_scope_round_trip_when_bound(self):
        server = FastMCP("test")
        identity = SessionIdentity(session_id="sess-1", principal=None)
        async with Context(fastmcp=server) as ctx:
            with bind_session_identity(identity):
                await ctx.set_state("cart", ["apple"], scope=Scope.SESSION)
                assert await ctx.get_state("cart", scope=Scope.SESSION) == ["apple"]

    async def test_session_scope_isolated_by_session_id(self):
        server = FastMCP("test")
        async with Context(fastmcp=server) as ctx:
            with bind_session_identity(SessionIdentity("sess-1", None)):
                await ctx.set_state("cart", ["apple"], scope=Scope.SESSION)
            with bind_session_identity(SessionIdentity("sess-2", None)):
                assert await ctx.get_state("cart", scope=Scope.SESSION) is None
