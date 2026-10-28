"""Per-server MCP config timeouts reach the sessions that send requests."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from mcp import ClientSession, MCPError

from fastmcp import Client, FastMCP
from fastmcp.client.transports import (
    FastMCPTransport,
    SSETransport,
    StdioTransport,
    StreamableHttpTransport,
)
from fastmcp.exceptions import ToolError


@pytest.fixture
def backend_sessions(monkeypatch: pytest.MonkeyPatch) -> list[ClientSession]:
    """Replace external connections while retaining config parsing and routing."""
    server = FastMCP("Timeout backend")

    @server.tool(tags={"public"})
    async def slow(seconds: float = 1) -> str:
        await asyncio.sleep(seconds)
        return "completed"

    sessions: list[ClientSession] = []

    @asynccontextmanager
    async def connect_session(
        self: Any, **kwargs: Any
    ) -> AsyncGenerator[ClientSession, None]:
        async with FastMCPTransport(server).connect_session(**kwargs) as session:
            sessions.append(session)
            yield session

    for transport_type in (StreamableHttpTransport, SSETransport, StdioTransport):
        monkeypatch.setattr(transport_type, "connect_session", connect_session)
    return sessions


@pytest.fixture(params=["http", "sse", "stdio"])
def server_entry(request: pytest.FixtureRequest) -> dict[str, Any]:
    if request.param == "stdio":
        return {"command": "unused"}
    return {"url": "http://example.invalid/mcp", "transport": request.param}


@pytest.mark.parametrize("server_count", [1, 2])
@pytest.mark.parametrize("transformed", [False, True])
async def test_entry_timeout_limits_requests(
    backend_sessions: list[ClientSession],
    server_entry: dict[str, Any],
    server_count: int,
    transformed: bool,
) -> None:
    entry = {**server_entry, "timeout": 500}
    if transformed:
        entry["include_tags"] = ["public"]
    servers = {"limited": entry}
    if server_count == 2:
        servers["unlimited"] = server_entry

    async with Client({"mcpServers": servers}, mode="legacy") as client:
        name = "slow" if server_count == 1 else "limited_slow"
        with pytest.raises((MCPError, ToolError), match="timed out"):
            await client.call_tool(name)

        if server_count == 2:
            result = await client.call_tool("unlimited_slow", {"seconds": 0})
            assert result.data == "completed"

    assert backend_sessions[0]._session_read_timeout_seconds == 0.5


@pytest.mark.parametrize(
    ("entry_timeout", "client_timeout", "expected"),
    [
        (None, None, None),
        (None, 3, 3.0),
        (1250, None, 1.25),
        (1250, 3, 1.25),
        (0, 3, None),
    ],
)
@pytest.mark.parametrize("server_count", [1, 2])
async def test_entry_timeout_defaults_and_units(
    backend_sessions: list[ClientSession],
    server_entry: dict[str, Any],
    entry_timeout: int | None,
    client_timeout: int | None,
    expected: float | None,
    server_count: int,
) -> None:
    servers = {"first": {**server_entry, "timeout": entry_timeout}}
    if server_count == 2:
        servers["second"] = server_entry

    async with Client({"mcpServers": servers}, timeout=client_timeout):
        assert backend_sessions[0]._session_read_timeout_seconds == expected
        if server_count == 2:
            assert backend_sessions[1]._session_read_timeout_seconds == client_timeout


async def test_single_server_per_call_timeout_overrides_entry(
    backend_sessions: list[ClientSession], server_entry: dict[str, Any]
) -> None:
    config = {"mcpServers": {"only": {**server_entry, "timeout": 500}}}
    async with Client(config) as client:
        result = await client.call_tool("slow", {"seconds": 0.6}, timeout=2)
        assert result.data == "completed"
        assert backend_sessions[0]._session_read_timeout_seconds == 0.5
