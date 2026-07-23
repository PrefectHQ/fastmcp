"""End-to-end tests for the two session-state patterns and `SessionProvider`.

Covers the injected `session: UserSession` per-user pattern, the explicit
`session_id: SessionId` argument pattern (with its create-then-validate
lifecycle), and the `SessionProvider` that supplies the `create_session` /
`end_session` lifecycle tools. The schema, registration, and lifecycle paths run
through an in-memory `Client`; the principal-isolation cases drive the tool
through its full injection + storage path under a simulated authenticated
principal.
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken as SDKAccessToken

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.server.dependencies import get_session
from fastmcp.server.sessions import (
    SESSION_ID_DESCRIPTION,
    InvalidSession,
    SessionId,
    SessionProvider,
    UserSession,
)
from fastmcp.tools.base import ToolResult


def result_value(result: ToolResult) -> Any:
    """The `"result"` field of a direct `Tool.run()` call's structured content.

    `structured_content` is `dict | None` on `ToolResult` (a bare function tool
    always populates it, but the type isn't narrowed by construction), so this
    asserts it is present before indexing.
    """
    assert result.structured_content is not None
    return result.structured_content["result"]


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
        session = await get_session(session_id)
        cart = await session.get("cart", default=[])
        cart.append(item)
        await session.set("cart", cart)
        return len(cart)

    @server.tool
    async def view_cart(session_id: SessionId) -> list[str]:
        session = await get_session(session_id)
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
        assert add_tool is not None
        assert view_tool is not None

        async with Context(fastmcp=server):
            with as_principal(make_token(subject="user-a")):
                await add_tool.run({"item": "apple"})
                second = await add_tool.run({"item": "banana"})
                view = await view_tool.run({})

        assert result_value(second) == 2
        assert result_value(view) == ["apple", "banana"]

    async def test_two_principals_get_isolated_buckets(self):
        server = build_injected_server()
        add_tool = await server.get_tool("add_to_cart")
        view_tool = await server.get_tool("view_cart")
        assert add_tool is not None
        assert view_tool is not None

        async with Context(fastmcp=server):
            with as_principal(make_token(subject="user-a")):
                await add_tool.run({"item": "apple"})
            with as_principal(make_token(subject="user-b")):
                view_b = await view_tool.run({})

        assert result_value(view_b) == []

    async def test_injected_value_is_a_user_session_instance(self):
        """The handler receives a `UserSession`, not a bare `Session` — so
        `isinstance(session, UserSession)` holds for code that keys off it."""
        server = FastMCP("shop")

        @server.tool
        async def whoami(session: UserSession) -> bool:
            return isinstance(session, UserSession)

        tool = await server.get_tool("whoami")
        assert tool is not None
        async with Context(fastmcp=server):
            with as_principal(make_token()):
                result = await tool.run({})
        assert result_value(result) is True

    async def test_optional_session_is_none_without_auth(self):
        """`session: UserSession | None = None` injects `None` on an
        unauthenticated request instead of raising."""
        server = FastMCP("shop")

        @server.tool
        async def maybe(session: UserSession | None = None) -> bool:
            return session is None

        tool = await server.get_tool("maybe")
        assert tool is not None
        async with Context(fastmcp=server):
            result = await tool.run({})
        assert result_value(result) is True

    async def test_optional_session_is_present_with_auth(self):
        """The same optional parameter injects a real `UserSession` when the
        request is authenticated."""
        server = FastMCP("shop")

        @server.tool
        async def maybe(session: UserSession | None = None) -> bool:
            return isinstance(session, UserSession)

        tool = await server.get_tool("maybe")
        assert tool is not None
        async with Context(fastmcp=server):
            with as_principal(make_token()):
                result = await tool.run({})
        assert result_value(result) is True

    async def test_optional_session_not_in_input_schema(self):
        """An optional injected session is still excluded from the schema."""
        server = FastMCP("shop")

        @server.tool
        async def maybe(session: UserSession | None = None) -> bool:
            return session is None

        async with Client(server) as client:
            tools = {t.name: t for t in await client.list_tools()}
        assert "session" not in tools["maybe"].input_schema.get("properties", {})

    async def test_user_session_needs_no_provider(self):
        """A server using only `UserSession` requires no `SessionProvider` and
        lists no lifecycle tools."""
        server = build_injected_server()
        async with Client(server) as client:
            names = {t.name for t in await client.list_tools()}
        assert "create_session" not in names
        assert "end_session" not in names

    async def test_storage_key_does_not_embed_the_raw_principal(self):
        """The injected session is stored under the reserved per-user id, not
        under the raw principal JSON — proven by reconstructing a `Session`
        against the reserved id and reading back what injection wrote."""
        from fastmcp.server.sessions import (
            _USER_SESSION_ID,
            Session,
            current_principal,
            session_storage_key,
        )

        server = build_injected_server()
        add_tool = await server.get_tool("add_to_cart")
        assert add_tool is not None

        token = make_token(subject="user-a")
        async with Context(fastmcp=server):
            with as_principal(token):
                await add_tool.run({"item": "apple"})
                principal = current_principal()

        assert principal is not None
        assert token.subject is not None
        # The raw principal never appears in the storage key itself.
        key = session_storage_key(principal, _USER_SESSION_ID)
        assert principal not in key
        assert token.subject not in key
        assert token.client_id not in key

        # And the reserved-id reconstruction reads back what injection wrote,
        # proving injection actually used `_USER_SESSION_ID` as the session id.
        reconstructed = Session(
            store=server._state_store,
            principal=principal,
            session_id=_USER_SESSION_ID,
        )
        assert await reconstructed.get("cart") == ["apple"]


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

    async def test_resolved_session_exposes_its_id(self):
        """A session resolved from a `session_id` argument carries that id."""
        server = FastMCP("shop")
        server.add_provider(SessionProvider())

        @server.tool
        async def which_session(session_id: SessionId) -> str | None:
            return (await get_session(session_id)).id

        async with Client(server) as client:
            session_id = (await client.call_tool("create_session", {})).data
            result = (
                await client.call_tool("which_session", {"session_id": session_id})
            ).data
        assert result == session_id

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
            session = await get_session(session_id)
            cart = await session.get("cart", default=[])
            cart.append(item)
            await session.set("cart", cart)
            return len(cart)

        @server.tool
        async def clear_cart(session_id: SessionId) -> str:
            session = await get_session(session_id)
            await session.clear()
            return "cleared"

        @server.tool
        async def view_cart(session_id: SessionId) -> list[str]:
            session = await get_session(session_id)
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
        assert create_tool is not None
        assert view_tool is not None

        async with Context(fastmcp=server):
            with as_principal(make_token(subject="user-a")):
                created = await create_tool.run({})
                session_id = result_value(created)
            with as_principal(make_token(subject="user-b")):
                with pytest.raises(InvalidSession):
                    await view_tool.run({"session_id": session_id})
            with as_principal(make_token(subject="user-a")):
                # A's own session still resolves.
                view_a = await view_tool.run({"session_id": session_id})

        assert result_value(view_a) == []

    async def test_two_principals_same_id_are_isolated(self):
        server = build_id_server()
        create_tool = await server.get_tool("create_session")
        add_tool = await server.get_tool("add_to_cart")
        view_tool = await server.get_tool("view_cart")
        assert create_tool is not None
        assert add_tool is not None
        assert view_tool is not None

        async with Context(fastmcp=server):
            with as_principal(make_token(subject="user-a")):
                id_a = result_value(await create_tool.run({}))
                await add_tool.run({"item": "apple", "session_id": id_a})
            with as_principal(make_token(subject="user-b")):
                id_b = result_value(await create_tool.run({}))
                # B's own session under its own id is empty.
                view_b = await view_tool.run({"session_id": id_b})
                assert result_value(view_b) == []
            with as_principal(make_token(subject="user-a")):
                view_a = await view_tool.run({"session_id": id_a})

        assert result_value(view_a) == ["apple"]


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


class TestNoProviderIsNonFatal:
    """With the enforcement checks removed, a `session_id` tool without a
    `SessionProvider` is not a setup error — it simply cannot resolve a session,
    because no id can be created. The failure surfaces at use, not at listing."""

    async def test_session_id_tool_lists_without_a_provider(self):
        server = FastMCP("shop")

        @server.tool
        async def add_to_cart(item: str, session_id: SessionId) -> int:
            return len(item)

        async with Client(server) as client:
            names = {t.name for t in await client.list_tools()}
        assert names == {"add_to_cart"}

    async def test_any_id_is_rejected_without_a_way_to_create_one(self):
        server = FastMCP("shop")

        @server.tool
        async def add_to_cart(item: str, session_id: SessionId) -> str:
            session = await get_session(session_id)
            await session.set("item", item)
            return "ok"

        # No provider, so no id was ever minted: resolution rejects any id.
        async with Client(server) as client:
            with pytest.raises(ToolError):
                await client.call_tool(
                    "add_to_cart", {"item": "x", "session_id": "made-up"}
                )


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

    async def test_contract_is_not_duplicated_when_author_repeats_it(self):
        """An author who already includes the contract text doesn't get it twice."""
        from typing import Annotated

        from pydantic import Field

        server = FastMCP("s")
        server.add_provider(SessionProvider())

        @server.tool
        async def resume(
            session_id: Annotated[SessionId, Field(description=SESSION_ID_DESCRIPTION)],
        ) -> str:
            return session_id

        async with Client(server) as client:
            tools = {t.name: t for t in await client.list_tools()}
        desc = tools["resume"].input_schema["properties"]["session_id"]["description"]
        assert desc.count(SESSION_ID_DESCRIPTION) == 1

    async def test_description_survives_namespaced_mount(self):
        """The contract names no specific tool, so it stays correct when a mount
        renames the lifecycle tool under a namespace — the description must not
        point agents at an unqualified `create_session` that does not exist
        under that mount."""
        child = FastMCP("child")
        child.add_provider(SessionProvider())

        @child.tool
        async def workflow(session_id: SessionId) -> str:
            return session_id

        parent = FastMCP("parent")
        parent.mount(child, namespace="child")

        async with Client(parent) as client:
            tools = {t.name: t for t in await client.list_tools()}

        # The lifecycle tool is renamed under the namespace...
        assert "child_create_session" in tools
        assert "create_session" not in tools
        # ...yet the session_id contract still resolves correctly, because it
        # describes the capability rather than naming a tool.
        desc = tools["child_workflow"].input_schema["properties"]["session_id"][
            "description"
        ]
        assert desc == SESSION_ID_DESCRIPTION
        assert "create_session" not in desc
