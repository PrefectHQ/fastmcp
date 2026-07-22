"""End-to-end tests for the two session-state patterns and `SessionProvider`.

Covers the injected `session: UserSession` per-user pattern, the explicit
`session_id: SessionId` argument pattern, and the `SessionProvider` lifecycle
tools (registered explicitly and auto-wired). The schema, registration, and
unauthenticated paths run through an in-memory `Client`; the principal-isolation
cases drive the tool through its full injection + storage path under a simulated
authenticated principal.
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken as SDKAccessToken

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.server.dependencies import get_context
from fastmcp.server.sessions import (
    SESSION_ID_DESCRIPTION,
    SessionId,
    SessionProvider,
    UserSession,
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
def as_principal(token: SDKAccessToken | None) -> Iterator[None]:
    if token is None:
        yield
        return
    reset = auth_context_var.set(AuthenticatedUser(token))
    try:
        yield
    finally:
        auth_context_var.reset(reset)


def build_injected_server() -> FastMCP:
    """Server whose cart tools inject a per-user `session: UserSession`."""
    server = FastMCP("shop")

    @server.tool
    async def add_to_cart(item: str, session: UserSession) -> int:
        cart = await session.get("cart", default=[])
        cart.append(item)
        await session.set("cart", cart)
        return len(cart)

    @server.tool
    async def view_cart(session: UserSession) -> list[str]:
        return await session.get("cart", default=[])

    return server


def build_id_server() -> FastMCP:
    """Server whose cart tools take an explicit `session_id: SessionId`."""
    server = FastMCP("shop")
    server.add_provider(SessionProvider())

    @server.tool
    async def add_to_cart(item: str, session_id: SessionId) -> int:
        session = get_context().get_session(session_id)
        cart = await session.get("cart", default=[])
        cart.append(item)
        await session.set("cart", cart)
        return len(cart)

    @server.tool
    async def view_cart(session_id: SessionId) -> list[str]:
        return await get_context().get_session(session_id).get("cart", default=[])

    return server


# ---------------------------------------------------------------------------
# Injected `session: UserSession`
# ---------------------------------------------------------------------------


class TestInjectedSession:
    async def test_not_in_input_schema(self):
        server = build_injected_server()
        async with Client(server) as client:
            tools = {t.name: t for t in await client.list_tools()}
        schema = tools["add_to_cart"].input_schema
        assert "session" not in schema["properties"]
        assert "item" in schema["properties"]

    async def test_errors_without_auth(self):
        server = build_injected_server()
        async with Client(server) as client:
            with pytest.raises(ToolError):
                await client.call_tool("view_cart", {})

    async def test_state_survives_across_calls_per_user(self):
        server = build_injected_server()
        add_tool = await server.get_tool("add_to_cart")
        view_tool = await server.get_tool("view_cart")

        async with Context(fastmcp=server):
            with as_principal(make_token(subject="user-a")):
                await add_tool.run({"item": "apple"})
                second = await add_tool.run({"item": "banana"})
                view = await view_tool.run({})

        assert second.structured_content["result"] == 2
        assert view.structured_content["result"] == ["apple", "banana"]

    async def test_two_principals_get_isolated_buckets(self):
        server = build_injected_server()
        add_tool = await server.get_tool("add_to_cart")
        view_tool = await server.get_tool("view_cart")

        async with Context(fastmcp=server):
            with as_principal(make_token(subject="user-a")):
                await add_tool.run({"item": "apple"})
            with as_principal(make_token(subject="user-b")):
                view_b = await view_tool.run({})

        assert view_b.structured_content["result"] == []


# ---------------------------------------------------------------------------
# Explicit `session_id: SessionId`
# ---------------------------------------------------------------------------


class TestSessionIdArgument:
    async def test_session_id_is_a_required_string_with_contract_description(self):
        server = build_id_server()
        async with Client(server) as client:
            tools = {t.name: t for t in await client.list_tools()}
        schema = tools["view_cart"].input_schema
        prop = schema["properties"]["session_id"]
        assert prop["type"] == "string"
        assert "session_id" in schema["required"]
        assert prop["description"] == SESSION_ID_DESCRIPTION

    async def test_state_survives_across_calls_under_an_id(self):
        server = build_id_server()
        async with Client(server) as client:
            session_id = (await client.call_tool("create_session", {})).data

            await client.call_tool(
                "add_to_cart", {"item": "apple", "session_id": session_id}
            )
            second = await client.call_tool(
                "add_to_cart", {"item": "banana", "session_id": session_id}
            )
            assert second.data == 2

            view = await client.call_tool("view_cart", {"session_id": session_id})
            assert view.data == ["apple", "banana"]

    async def test_distinct_ids_isolated(self):
        server = build_id_server()
        async with Client(server) as client:
            id_a = (await client.call_tool("create_session", {})).data
            id_b = (await client.call_tool("create_session", {})).data
            assert id_a != id_b

            await client.call_tool("add_to_cart", {"item": "apple", "session_id": id_a})
            view_b = await client.call_tool("view_cart", {"session_id": id_b})
            assert view_b.data == []

    async def test_end_session_clears_state(self):
        server = build_id_server()
        async with Client(server) as client:
            session_id = (await client.call_tool("create_session", {})).data
            await client.call_tool(
                "add_to_cart", {"item": "apple", "session_id": session_id}
            )

            await client.call_tool("end_session", {"session_id": session_id})

            view = await client.call_tool("view_cart", {"session_id": session_id})
            assert view.data == []

    async def test_two_principals_same_id_are_isolated(self):
        server = build_id_server()
        add_tool = await server.get_tool("add_to_cart")
        view_tool = await server.get_tool("view_cart")

        async with Context(fastmcp=server):
            with as_principal(make_token(subject="user-a")):
                await add_tool.run({"item": "apple", "session_id": "shared"})
            with as_principal(make_token(subject="user-b")):
                view_b = await view_tool.run({"session_id": "shared"})
                # B's own bucket under the same id is empty.
                assert view_b.structured_content["result"] == []
            with as_principal(make_token(subject="user-a")):
                view_a = await view_tool.run({"session_id": "shared"})

        assert view_a.structured_content["result"] == ["apple"]


# ---------------------------------------------------------------------------
# SessionProvider
# ---------------------------------------------------------------------------


class TestSessionProvider:
    async def test_lifecycle_tools_registered_via_add_provider(self):
        server = FastMCP("s")
        server.add_provider(SessionProvider())
        async with Client(server) as client:
            names = {t.name for t in await client.list_tools()}
        assert {"create_session", "end_session"} <= names

    async def test_create_session_returns_a_uuid_string(self):
        server = FastMCP("s")
        server.add_provider(SessionProvider())
        async with Client(server) as client:
            session_id = (await client.call_tool("create_session", {})).data
        assert isinstance(session_id, str)
        # Parses as a uuid4 and is unguessable (not a fixed/empty value).
        assert str(UUID(session_id)) == session_id

    async def test_create_session_ids_are_distinct(self):
        server = FastMCP("s")
        server.add_provider(SessionProvider())
        async with Client(server) as client:
            first = (await client.call_tool("create_session", {})).data
            second = (await client.call_tool("create_session", {})).data
        assert first != second

    async def test_end_session_declares_session_id_contract(self):
        server = FastMCP("s")
        server.add_provider(SessionProvider())
        async with Client(server) as client:
            tools = {t.name: t for t in await client.list_tools()}
        prop = tools["end_session"].input_schema["properties"]["session_id"]
        assert prop["type"] == "string"
        assert SESSION_ID_DESCRIPTION in prop["description"]


class TestAutoRegisteredSessionProvider:
    async def test_session_id_tool_auto_wires_the_provider(self):
        """Declaring `session_id: SessionId` makes create/end available with no
        explicit add_provider call."""
        server = FastMCP("shop")

        @server.tool
        async def add_to_cart(item: str, session_id: SessionId) -> int:
            session = get_context().get_session(session_id)
            cart = await session.get("cart", default=[])
            cart.append(item)
            await session.set("cart", cart)
            return len(cart)

        async with Client(server) as client:
            names = {t.name for t in await client.list_tools()}
        assert {"create_session", "end_session"} <= names

    async def test_auto_wired_lifecycle_round_trips(self):
        server = FastMCP("shop")

        @server.tool
        async def add_to_cart(item: str, session_id: SessionId) -> int:
            session = get_context().get_session(session_id)
            cart = await session.get("cart", default=[])
            cart.append(item)
            await session.set("cart", cart)
            return len(cart)

        async with Client(server) as client:
            session_id = (await client.call_tool("create_session", {})).data
            await client.call_tool(
                "add_to_cart", {"item": "apple", "session_id": session_id}
            )
            await client.call_tool("end_session", {"session_id": session_id})

    async def test_not_registered_without_a_session_id_tool(self):
        """A server with no `session_id` tool gets no lifecycle tools."""
        server = FastMCP("plain")

        @server.tool
        async def echo(text: str) -> str:
            return text

        async with Client(server) as client:
            names = {t.name for t in await client.list_tools()}
        assert "create_session" not in names
        assert "end_session" not in names

    async def test_injected_session_alone_does_not_auto_wire(self):
        """The injected `UserSession` pattern needs no `create_session`, so it does
        not trigger auto-registration."""
        server = build_injected_server()
        async with Client(server) as client:
            names = {t.name for t in await client.list_tools()}
        assert "create_session" not in names

    async def test_manual_provider_is_not_doubled(self):
        """An explicitly added SessionProvider suppresses the implicit one — only
        one create_session / end_session is listed."""
        server = FastMCP("shop")
        server.add_provider(SessionProvider())

        @server.tool
        async def add_to_cart(item: str, session_id: SessionId) -> int:
            return len(item)

        async with Client(server) as client:
            tools = await client.list_tools()
        create = [t for t in tools if t.name == "create_session"]
        end = [t for t in tools if t.name == "end_session"]
        assert len(create) == 1
        assert len(end) == 1

    async def test_tool_added_after_construction_still_wires(self):
        """Auto-registration is decided at list time, so a `session_id` tool added
        after the server is built still activates the provider."""
        server = FastMCP("shop")

        async with Client(server) as client:
            names = {t.name for t in await client.list_tools()}
        assert "create_session" not in names

        @server.tool
        async def add_to_cart(item: str, session_id: SessionId) -> int:
            return len(item)

        async with Client(server) as client:
            names = {t.name for t in await client.list_tools()}
        assert {"create_session", "end_session"} <= names

    async def test_opt_out_disables_auto_registration(self):
        """`auto_session_provider=False` (bring-your-own-key) suppresses the
        lifecycle tools even when a `session_id` tool is declared."""
        server = FastMCP("shop", auto_session_provider=False)

        @server.tool
        async def add_to_cart(item: str, session_id: SessionId) -> int:
            return len(item)

        async with Client(server) as client:
            names = {t.name for t in await client.list_tools()}
        assert "create_session" not in names
        assert "end_session" not in names

    async def test_opt_out_still_allows_explicit_provider(self):
        """Opting out of auto-registration does not block a manually added one."""
        server = FastMCP("shop", auto_session_provider=False)
        server.add_provider(SessionProvider())

        @server.tool
        async def add_to_cart(item: str, session_id: SessionId) -> int:
            return len(item)

        async with Client(server) as client:
            names = {t.name for t in await client.list_tools()}
        assert {"create_session", "end_session"} <= names


class TestSessionIdDescriptionAppending:
    async def test_author_description_is_preserved_and_appended(self):
        server = FastMCP("s")

        @server.tool
        async def resume(session_id: SessionId) -> str:
            """Resume work.

            Args:
                session_id: The handle for this workflow.
            """
            return session_id

        async with Client(server) as client:
            tools = {t.name: t for t in await client.list_tools()}
        desc = tools["resume"].input_schema["properties"]["session_id"]["description"]
        assert "The handle for this workflow." in desc
        assert SESSION_ID_DESCRIPTION in desc
        # Author text comes first, contract appended after.
        assert re.search(r"handle for this workflow\.\s+Session identifier\.", desc)
