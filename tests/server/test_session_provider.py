"""End-to-end tests for the `SessionProvider` and the `Session()` annotation.

Covers the lifecycle tools (`create_session` / `end_session`) and the annotation
boundary that unseals a sealed handle, binds its identity for the call, and
gates `Scope.SESSION` state. The happy paths run through an in-memory `Client`;
the auth-dependent cross-principal case drives the tool boundary directly under a
simulated authenticated principal.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken as SDKAccessToken

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.server.dependencies import get_context
from fastmcp.server.sessions import (
    InvalidSessionToken,
    Scope,
    Session,
    SessionProvider,
    current_principal,
)


def make_token(*, subject: str = "user-a") -> SDKAccessToken:
    return SDKAccessToken(
        token="opaque",
        client_id="client-1",
        scopes=[],
        subject=subject,
        claims={"iss": "https://issuer.example"},
    )


@contextmanager
def as_principal(token: SDKAccessToken) -> Iterator[None]:
    """Set the authenticated principal for the current context."""
    reset = auth_context_var.set(AuthenticatedUser(token))
    try:
        yield
    finally:
        auth_context_var.reset(reset)


def build_shop(**provider_kwargs: float) -> FastMCP:
    """A server with a session provider and two session-scoped cart tools."""
    server = FastMCP("shop", session_provider=SessionProvider(**provider_kwargs))

    @server.tool
    async def add_to_cart(item: str, session: Annotated[str, Session()]) -> int:
        ctx = get_context()
        cart = await ctx.get_state("cart", scope=Scope.SESSION) or []
        cart.append(item)
        await ctx.set_state("cart", cart, scope=Scope.SESSION)
        return len(cart)

    @server.tool
    async def view_cart(session: Annotated[str, Session()]) -> list[str]:
        ctx = get_context()
        return await ctx.get_state("cart", scope=Scope.SESSION) or []

    return server


class TestSessionLifecycle:
    async def test_state_survives_across_calls(self):
        server = build_shop()
        async with Client(server) as client:
            token = (await client.call_tool("create_session", {})).data
            assert isinstance(token, str) and token

            await client.call_tool("add_to_cart", {"item": "apple", "session": token})
            second = await client.call_tool(
                "add_to_cart", {"item": "banana", "session": token}
            )
            assert second.data == 2

            view = await client.call_tool("view_cart", {"session": token})
            assert view.data == ["apple", "banana"]

    async def test_end_session_clears_state(self):
        server = build_shop()
        async with Client(server) as client:
            token = (await client.call_tool("create_session", {})).data
            await client.call_tool("add_to_cart", {"item": "apple", "session": token})

            await client.call_tool("end_session", {"session": token})

            view = await client.call_tool("view_cart", {"session": token})
            assert view.data == []

    async def test_distinct_sessions_are_isolated(self):
        server = build_shop()
        async with Client(server) as client:
            token_a = (await client.call_tool("create_session", {})).data
            token_b = (await client.call_tool("create_session", {})).data
            assert token_a != token_b

            await client.call_tool("add_to_cart", {"item": "apple", "session": token_a})

            view_b = await client.call_tool("view_cart", {"session": token_b})
            assert view_b.data == []


class TestSessionAnnotationSchema:
    async def test_lifecycle_tools_registered(self):
        server = build_shop()
        async with Client(server) as client:
            names = {tool.name for tool in await client.list_tools()}
        assert {"create_session", "end_session"} <= names

    async def test_session_parameter_is_a_string_in_schema(self):
        server = build_shop()
        async with Client(server) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
        schema = tools["view_cart"].input_schema
        assert schema["properties"]["session"]["type"] == "string"
        assert "session" in schema["required"]

    async def test_body_receives_raw_token_string(self):
        server = FastMCP("shop", session_provider=SessionProvider())
        observed: dict[str, str] = {}

        @server.tool
        async def echo_token(session: Annotated[str, Session()]) -> str:
            observed["token"] = session
            return session

        async with Client(server) as client:
            token = (await client.call_tool("create_session", {})).data
            result = await client.call_tool("echo_token", {"session": token})

        assert result.data == token
        assert observed["token"] == token

    def test_multiple_session_parameters_rejected(self):
        server = FastMCP("shop")
        with pytest.raises(ValueError, match="at most one"):

            @server.tool
            def two_sessions(
                a: Annotated[str, Session()],
                b: Annotated[str, Session()],
            ) -> str:
                return "nope"


class TestInvalidHandles:
    @pytest.mark.parametrize("bad_token", ["garbage", "v1.tampered.payload", ""])
    async def test_invalid_token_fails_the_call(self, bad_token: str):
        server = build_shop()
        async with Client(server) as client:
            with pytest.raises(ToolError):
                await client.call_tool("view_cart", {"session": bad_token})

    async def test_foreign_key_token_fails(self):
        """A handle sealed by a different server (different key) is rejected."""
        minting_server = build_shop()
        async with Client(minting_server) as client:
            foreign_token = (await client.call_tool("create_session", {})).data

        verifying_server = build_shop()
        async with Client(verifying_server) as client:
            with pytest.raises(ToolError):
                await client.call_tool("view_cart", {"session": foreign_token})


class TestCrossPrincipalIsolation:
    async def test_token_for_a_rejected_under_b(self):
        server = build_shop()
        tool = await server.get_tool("view_cart")

        async with Context(fastmcp=server):
            with as_principal(make_token(subject="user-a")):
                token = server.session_codec.seal("sess-a", current_principal())

            with as_principal(make_token(subject="user-b")):
                with pytest.raises(InvalidSessionToken):
                    await tool.run({"session": token})

    async def test_token_for_a_accepted_under_a(self):
        server = build_shop()
        add_tool = await server.get_tool("add_to_cart")
        view_tool = await server.get_tool("view_cart")

        async with Context(fastmcp=server):
            with as_principal(make_token(subject="user-a")):
                token = server.session_codec.seal("sess-a", current_principal())
                await add_tool.run({"item": "apple", "session": token})
                result = await view_tool.run({"session": token})

        assert json.loads(result.content[0].text) == ["apple"]


class TestExpiry:
    async def test_expired_token_rejected_at_boundary(self, monkeypatch):
        import fastmcp.server.sessions as sessions_mod

        server = build_shop(ttl=60)
        view_tool = await server.get_tool("view_cart")

        base = 1_000_000.0
        monkeypatch.setattr(sessions_mod.time, "time", lambda: base)
        token = server.session_codec.seal("sess-1", None)
        monkeypatch.setattr(sessions_mod.time, "time", lambda: base + 61)

        async with Context(fastmcp=server):
            with pytest.raises(InvalidSessionToken):
                await view_tool.run({"session": token})
