"""End-to-end tests for the two session-state patterns and `SessionProvider`.

Covers the injected `session: UserSession` per-user pattern, the explicit
`session_id: SessionId` argument pattern (with its create-then-validate
lifecycle), and the requirement that a `SessionProvider` be registered whenever a
tool declares `session_id`. The schema, registration, and lifecycle paths run
through an in-memory `Client`; the principal-isolation cases drive the tool
through its full injection + storage path under a simulated authenticated
principal.
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
    InvalidSession,
    SessionId,
    SessionProvider,
    SessionProviderRequiredError,
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
        session = await get_context().get_session(session_id)
        cart = await session.get("cart", default=[])
        cart.append(item)
        await session.set("cart", cart)
        return len(cart)

    @server.tool
    async def view_cart(session_id: SessionId) -> list[str]:
        session = await get_context().get_session(session_id)
        return await session.get("cart", default=[])

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

    async def test_user_session_needs_no_provider(self):
        """A server using only `UserSession` requires no `SessionProvider` and
        lists no lifecycle tools."""
        server = build_injected_server()
        async with Client(server) as client:
            names = {t.name for t in await client.list_tools()}
        assert "create_session" not in names
        assert "end_session" not in names


# ---------------------------------------------------------------------------
# Explicit `session_id: SessionId` — create then validate
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

    async def test_created_id_round_trips_state_across_calls(self):
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

    async def test_uncreated_id_is_rejected(self):
        """An id that was never handed out by `create_session` does not resolve."""
        server = build_id_server()
        async with Client(server) as client:
            with pytest.raises(ToolError):
                await client.call_tool("view_cart", {"session_id": "never-created"})

    async def test_distinct_created_ids_are_isolated(self):
        server = build_id_server()
        async with Client(server) as client:
            id_a = (await client.call_tool("create_session", {})).data
            id_b = (await client.call_tool("create_session", {})).data
            assert id_a != id_b

            await client.call_tool("add_to_cart", {"item": "apple", "session_id": id_a})
            view_b = await client.call_tool("view_cart", {"session_id": id_b})
            assert view_b.data == []

    async def test_end_session_invalidates_the_session(self):
        """After `end_session` the id no longer resolves at all."""
        server = build_id_server()
        async with Client(server) as client:
            session_id = (await client.call_tool("create_session", {})).data
            await client.call_tool(
                "add_to_cart", {"item": "apple", "session_id": session_id}
            )

            await client.call_tool("end_session", {"session_id": session_id})

            with pytest.raises(ToolError):
                await client.call_tool("view_cart", {"session_id": session_id})

    async def test_clear_keeps_the_session_valid(self):
        """`session.clear()` empties state but the session still resolves."""
        server = FastMCP("shop")
        server.add_provider(SessionProvider())

        @server.tool
        async def add_to_cart(item: str, session_id: SessionId) -> int:
            session = await get_context().get_session(session_id)
            cart = await session.get("cart", default=[])
            cart.append(item)
            await session.set("cart", cart)
            return len(cart)

        @server.tool
        async def clear_cart(session_id: SessionId) -> str:
            session = await get_context().get_session(session_id)
            await session.clear()
            return "cleared"

        @server.tool
        async def view_cart(session_id: SessionId) -> list[str]:
            session = await get_context().get_session(session_id)
            return await session.get("cart", default=[])

        async with Client(server) as client:
            session_id = (await client.call_tool("create_session", {})).data
            await client.call_tool(
                "add_to_cart", {"item": "apple", "session_id": session_id}
            )
            await client.call_tool("clear_cart", {"session_id": session_id})

            # Still resolves (no error), and state is empty.
            view = await client.call_tool("view_cart", {"session_id": session_id})
            assert view.data == []

    async def test_created_under_one_principal_rejected_under_another(self):
        """An id created by principal A is rejected when used by principal B."""
        server = build_id_server()
        create_tool = await server.get_tool("create_session")
        view_tool = await server.get_tool("view_cart")

        async with Context(fastmcp=server):
            with as_principal(make_token(subject="user-a")):
                created = await create_tool.run({})
                session_id = created.structured_content["result"]
            with as_principal(make_token(subject="user-b")):
                with pytest.raises(InvalidSession):
                    await view_tool.run({"session_id": session_id})
            with as_principal(make_token(subject="user-a")):
                # A's own session still resolves.
                view_a = await view_tool.run({"session_id": session_id})

        assert view_a.structured_content["result"] == []

    async def test_two_principals_same_id_are_isolated(self):
        server = build_id_server()
        create_tool = await server.get_tool("create_session")
        add_tool = await server.get_tool("add_to_cart")
        view_tool = await server.get_tool("view_cart")

        async with Context(fastmcp=server):
            with as_principal(make_token(subject="user-a")):
                id_a = (await create_tool.run({})).structured_content["result"]
                await add_tool.run({"item": "apple", "session_id": id_a})
            with as_principal(make_token(subject="user-b")):
                id_b = (await create_tool.run({})).structured_content["result"]
                # B's own session under its own id is empty.
                view_b = await view_tool.run({"session_id": id_b})
                assert view_b.structured_content["result"] == []
            with as_principal(make_token(subject="user-a")):
                view_a = await view_tool.run({"session_id": id_a})

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


class TestSessionProviderRequirement:
    async def test_missing_provider_raises_naming_the_tool(self):
        """A `session_id` tool with no `SessionProvider` fails loudly, naming the
        offending tool and the fix."""
        server = FastMCP("shop")

        @server.tool
        async def add_to_cart(item: str, session_id: SessionId) -> int:
            return len(item)

        with pytest.raises(SessionProviderRequiredError) as exc_info:
            await server.list_tools()
        message = str(exc_info.value)
        assert "add_to_cart" in message
        assert "add_provider(SessionProvider())" in message

    async def test_missing_provider_blocks_tool_resolution(self):
        """The requirement also gates `_get_tool`, so a call cannot slip past."""
        server = FastMCP("shop")

        @server.tool
        async def add_to_cart(item: str, session_id: SessionId) -> int:
            return len(item)

        with pytest.raises(SessionProviderRequiredError):
            await server.get_tool("add_to_cart")

    async def test_missing_provider_surfaces_over_the_wire(self):
        """Over the client wire the misconfiguration still fails the call."""
        server = FastMCP("shop")

        @server.tool
        async def add_to_cart(item: str, session_id: SessionId) -> int:
            return len(item)

        async with Client(server) as client:
            with pytest.raises(Exception):
                await client.list_tools()

    async def test_registering_the_provider_satisfies_the_requirement(self):
        server = FastMCP("shop")
        server.add_provider(SessionProvider())

        @server.tool
        async def add_to_cart(item: str, session_id: SessionId) -> int:
            return len(item)

        async with Client(server) as client:
            names = {t.name for t in await client.list_tools()}
        assert {"create_session", "end_session"} <= names

    async def test_requirement_triggers_for_tool_added_after_construction(self):
        """The check re-scans live, so a `session_id` tool added after the server
        is built still triggers the requirement."""
        server = FastMCP("shop")

        # No session_id tool yet: listing is fine.
        await server.list_tools()

        @server.tool
        async def add_to_cart(item: str, session_id: SessionId) -> int:
            return len(item)

        with pytest.raises(SessionProviderRequiredError, match="add_to_cart"):
            await server.list_tools()

    async def test_no_requirement_without_a_session_id_tool(self):
        """A server with no `session_id` tool needs no provider and lists cleanly."""
        server = FastMCP("plain")

        @server.tool
        async def echo(text: str) -> str:
            return text

        async with Client(server) as client:
            names = {t.name for t in await client.list_tools()}
        assert names == {"echo"}


class TestSessionIdDescriptionAppending:
    async def test_author_description_is_preserved_and_appended(self):
        server = FastMCP("s")
        server.add_provider(SessionProvider())

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
