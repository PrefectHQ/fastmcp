import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ConfigDict

from fastmcp import Client, ClientGroup, Context, FastMCP
from fastmcp.client.transports import FastMCPTransport
from fastmcp.mcp_config import MCPConfig, StdioMCPServer


class LegacyFastMCPTransport(FastMCPTransport):
    legacy_only = True


class InMemoryServer(StdioMCPServer):
    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    mcp: FastMCP
    command: str = "in-memory"

    def to_transport(self) -> FastMCPTransport:
        return FastMCPTransport(self.mcp)


class LegacyInMemoryServer(InMemoryServer):
    def to_transport(self) -> FastMCPTransport:
        return LegacyFastMCPTransport(self.mcp)


def make_server(name: str) -> FastMCP:
    server = FastMCP(name)

    @server.tool
    async def protocol_era(ctx: Context) -> str:
        request_context = ctx.request_context
        assert request_context is not None
        return request_context.protocol_version

    @server.tool
    def echo(value: str) -> str:
        return f"{name}: {value}"

    return server


async def test_clients_negotiate_independently():
    legacy = Client(LegacyFastMCPTransport(make_server("legacy")))
    modern = Client(FastMCPTransport(make_server("modern")))
    group = ClientGroup({"old": legacy, "new": modern})

    async with group:
        assert group.protocol_versions == {
            "old": "2025-11-25",
            "new": "2026-07-28",
        }

        tools = await group.list_tools()
        assert {tool.name for tool in tools} == {
            "old_protocol_era",
            "old_echo",
            "new_protocol_era",
            "new_echo",
        }
        route = await group.resolve_tool("new_echo")
        assert route.server_name == "new"
        assert route.client is modern
        assert route.upstream_name == "echo"

        old_era = await group.call_tool("old_protocol_era")
        new_era = await group.call_tool("new_protocol_era")
        echoed = await group.call_tool("new_echo", {"value": "hello"})

        assert old_era.data == "2025-11-25"
        assert new_era.data == "2026-07-28"
        assert echoed.data == "modern: hello"

    assert not legacy.is_connected()
    assert not modern.is_connected()


async def test_from_config_applies_mode_per_server():
    config = MCPConfig(
        mcpServers={
            "old": InMemoryServer(mcp=make_server("old"), mode="legacy"),
            "new": InMemoryServer(mcp=make_server("new"), mode="auto"),
        }
    )
    group = ClientGroup.from_config(config)

    async with group:
        assert group.protocol_versions == {
            "old": "2025-11-25",
            "new": "2026-07-28",
        }


async def test_call_tool_populates_routes_lazily():
    group = ClientGroup({"server": Client(make_server("server"))})

    async with group:
        result = await group.call_tool("server_echo", {"value": "hello"})

    assert result.data == "server: hello"


async def test_concurrent_cold_calls_share_tool_discovery():
    first = Client(make_server("first"))
    second = Client(make_server("second"))
    group = ClientGroup({"first": first, "second": second})

    with (
        patch.object(first, "list_tools", wraps=first.list_tools) as first_list,
        patch.object(second, "list_tools", wraps=second.list_tools) as second_list,
    ):
        async with group:
            results = await asyncio.gather(
                group.call_tool("first_echo", {"value": "one"}),
                group.call_tool("second_echo", {"value": "two"}),
                group.call_tool("first_protocol_era"),
            )

    assert [result.data for result in results] == [
        "first: one",
        "second: two",
        "2026-07-28",
    ]
    assert first_list.call_count == 1
    assert second_list.call_count == 1


async def test_unknown_tools_do_not_repeat_discovery():
    first = Client(make_server("first"))
    second = Client(make_server("second"))
    group = ClientGroup({"first": first, "second": second})

    with (
        patch.object(first, "list_tools", wraps=first.list_tools) as first_list,
        patch.object(second, "list_tools", wraps=second.list_tools) as second_list,
    ):
        async with group:
            for name in ("missing", "also_missing", "missing"):
                with pytest.raises(KeyError, match=f"Unknown tool: {name!r}"):
                    await group.call_tool(name)

    assert first_list.call_count == 1
    assert second_list.call_count == 1


async def test_callers_can_manage_connections():
    old = Client(LegacyFastMCPTransport(make_server("old")))
    new = Client(FastMCPTransport(make_server("new")))
    group = ClientGroup({"old": old, "new": new})

    async with old, new:
        tools = await group.list_tools()
        result = await group.call_tool("new_echo", {"value": "hello"})

        assert {tool.name for tool in tools} >= {"old_echo", "new_echo"}
        assert result.data == "new: hello"
        assert group.protocol_versions == {
            "old": "2025-11-25",
            "new": "2026-07-28",
        }

    assert not old.is_connected()
    assert not new.is_connected()


async def test_group_context_does_not_close_caller_owned_client():
    client = Client(make_server("server"))
    group = ClientGroup({"server": client})

    async with client:
        async with group:
            result = await group.call_tool("server_echo", {"value": "hello"})
            assert result.data == "server: hello"

        assert client.is_connected()

    assert not client.is_connected()


async def test_group_requires_each_client_to_be_connected():
    connected = Client(make_server("connected"))
    disconnected = Client(make_server("disconnected"))
    group = ClientGroup({"connected": connected, "disconnected": disconnected})

    async with connected:
        try:
            await group.list_tools()
        except RuntimeError as exc:
            assert str(exc) == "ClientGroup clients are not connected: 'disconnected'"
        else:
            raise AssertionError("Expected a disconnected-client error")


async def test_group_keeps_one_connection_per_server():
    lifecycles = {"entered": 0, "exited": 0}

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        lifecycles["entered"] += 1
        try:
            yield {}
        finally:
            lifecycles["exited"] += 1

    server = FastMCP("stateful", lifespan=lifespan)

    @server.tool
    def echo(value: str) -> str:
        return value

    group = ClientGroup({"stateful": Client(server)})
    async with group:
        await group.list_tools()
        await group.call_tool("stateful_echo", {"value": "one"})
        await group.call_tool("stateful_echo", {"value": "two"})
        assert lifecycles == {"entered": 1, "exited": 0}

    assert lifecycles == {"entered": 1, "exited": 1}


async def test_tool_name_collisions_are_rejected():
    first = FastMCP("first")
    second = FastMCP("second")

    @first.tool(name="b_echo")
    def first_echo() -> str:
        return "first"

    @second.tool(name="echo")
    def second_echo() -> str:
        return "second"

    group = ClientGroup(
        {
            "a": Client(first),
            "a_b": Client(second),
        }
    )

    async with group:
        try:
            await group.list_tools()
        except ValueError as exc:
            assert str(exc) == "Tool name collision: 'a_b_echo'"
        else:
            raise AssertionError("Expected a tool name collision")


async def test_single_client_group_provides_namespacing_alone():
    group = ClientGroup({"solo": Client(FastMCPTransport(make_server("solo")))})

    async with group:
        tools = await group.list_tools()
        assert sorted(tool.name for tool in tools) == ["solo_echo", "solo_protocol_era"]

        result = await group.call_tool("solo_echo", {"value": "hi"})
        assert result.data == "solo: hi"


async def test_client_membership_is_immutable():
    group = ClientGroup({"solo": Client(FastMCPTransport(make_server("solo")))})

    with pytest.raises(TypeError):
        group.clients["other"] = Client(  # ty: ignore[invalid-assignment]
            FastMCPTransport(make_server("other"))
        )


async def test_concurrent_group_entry_does_not_double_connect():
    client = Client(FastMCPTransport(make_server("solo")))
    group = ClientGroup({"solo": client})

    with patch.object(Client, "_connect", wraps=client._connect) as connect:
        await asyncio.gather(group.__aenter__(), group.__aenter__())

    assert connect.call_count == 1
    assert client.is_connected()

    await group.__aexit__(None, None, None)
    assert client.is_connected()
    await group.__aexit__(None, None, None)
    assert not client.is_connected()


async def test_known_route_survives_an_unrelated_dead_client():
    healthy = Client(FastMCPTransport(make_server("healthy")))
    doomed = Client(FastMCPTransport(make_server("doomed")))
    group = ClientGroup({"healthy": healthy, "doomed": doomed})

    async with healthy:
        async with doomed:
            await group.list_tools()
        assert not doomed.is_connected()

        result = await group.call_tool("healthy_echo", {"value": "hi"})
        assert result.data == "healthy: hi"

        with pytest.raises(RuntimeError, match="doomed"):
            await group.call_tool("doomed_echo", {"value": "hi"})


async def test_partial_connect_failure_unwinds_connected_clients():
    ok = Client(FastMCPTransport(make_server("ok")))
    bad = Client(FastMCPTransport(make_server("bad")))
    group = ClientGroup({"ok": ok, "bad": bad})

    from unittest.mock import AsyncMock

    with patch.object(bad, "__aenter__", AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(RuntimeError, match="boom"):
            async with group:
                pass

    assert not ok.is_connected()
    assert group._exit_stack is None


async def test_explicit_list_tools_refreshes_past_client_response_cache():
    """A cache-hinted server must not leave group.list_tools() serving a stale
    catalog: the explicit call is the documented refresh mechanism."""
    server = FastMCP("hinted", cache_ttl=60000)

    @server.tool
    def original() -> str:
        return "original"

    client = Client(FastMCPTransport(server), cache=True)
    group = ClientGroup({"hinted": client})

    async with group:
        tools = await group.list_tools()
        assert [tool.name for tool in tools] == ["hinted_original"]

        @server.tool
        def added() -> str:
            return "added"

        refreshed = await group.list_tools()
        assert {tool.name for tool in refreshed} == {"hinted_original", "hinted_added"}

        result = await group.call_tool("hinted_added")
        assert result.data == "added"


async def test_group_context_is_reentrant():
    client = Client(make_server("server"))
    group = ClientGroup({"server": client})

    async with group:
        async with group:
            result = await group.call_tool("server_echo", {"value": "inner"})
            assert result.data == "server: inner"

        assert client.is_connected()
        result = await group.call_tool("server_echo", {"value": "outer"})
        assert result.data == "server: outer"

    assert not client.is_connected()


async def test_concurrent_group_entries_share_one_connection():
    client = Client(make_server("server"))
    group = ClientGroup({"server": client})
    started = asyncio.Event()

    async def hold_open() -> None:
        async with group:
            started.set()
            await asyncio.sleep(0.05)

    async def use_while_held() -> str:
        await started.wait()
        async with group:
            result = await group.call_tool("server_echo", {"value": "shared"})
            return result.data

    with patch.object(Client, "_connect", wraps=client._connect) as connect:
        _, data = await asyncio.gather(hold_open(), use_while_held())

    assert data == "server: shared"
    assert connect.call_count == 1
    assert not client.is_connected()


async def test_group_reconnects_after_last_exit():
    client = Client(make_server("server"))
    group = ClientGroup({"server": client})

    async with group:
        assert client.is_connected()
    assert not client.is_connected()

    async with group:
        result = await group.call_tool("server_echo", {"value": "again"})
        assert result.data == "server: again"
    assert not client.is_connected()


async def test_failed_entry_leaves_group_reenterable():
    good = Client(make_server("good"))
    bad = Client(FastMCPTransport(make_server("bad")))
    group = ClientGroup({"good": good, "bad": bad})

    with patch.object(bad, "_connect", side_effect=ConnectionError("down")):
        with pytest.raises(ConnectionError):
            async with group:
                raise AssertionError("group entry should have failed")

    assert not good.is_connected()

    async with group:
        result = await group.call_tool("good_echo", {"value": "recovered"})
        assert result.data == "good: recovered"
