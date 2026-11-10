"""MCP config tag filters keep their meaning when another server is added."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from mcp import ClientSession

from fastmcp import Client, FastMCP
from fastmcp.client.transports import (
    FastMCPTransport,
    SSETransport,
    StdioTransport,
    StreamableHttpTransport,
)


@pytest.fixture(params=["http", "sse", "stdio"])
def backend_entry(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    server = FastMCP("Tag filter backend")

    @server.tool(tags={"public"})
    def public_tool() -> str:
        return "public"

    @server.tool(tags={"private"})
    def private_tool() -> str:
        return "private"

    @server.tool
    def untagged_tool() -> str:
        return "untagged"

    @asynccontextmanager
    async def connect_session(
        self: Any, **kwargs: Any
    ) -> AsyncGenerator[ClientSession, None]:
        async with FastMCPTransport(server).connect_session(**kwargs) as session:
            yield session

    if request.param == "stdio":
        monkeypatch.setattr(StdioTransport, "connect_session", connect_session)
        return {"command": "unused"}
    transport = SSETransport if request.param == "sse" else StreamableHttpTransport
    monkeypatch.setattr(transport, "connect_session", connect_session)
    return {"url": "http://example.invalid/mcp", "transport": request.param}


@pytest.mark.parametrize("server_count", [1, 2])
@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({}, {"public_tool", "private_tool", "untagged_tool"}),
        ({"include_tags": []}, set()),
        ({"include_tags": ["public"]}, {"public_tool"}),
        ({"exclude_tags": []}, {"public_tool", "private_tool", "untagged_tool"}),
        ({"include_tags": [], "exclude_tags": ["private"]}, set()),
        (
            {"include_tags": ["public", "private"], "exclude_tags": ["private"]},
            {"public_tool"},
        ),
    ],
)
async def test_tag_filters_with_one_or_multiple_servers(
    backend_entry: dict[str, Any],
    server_count: int,
    filters: dict[str, list[str]],
    expected: set[str],
) -> None:
    servers = {"filtered": {**backend_entry, **filters}}
    names = expected
    if server_count == 2:
        servers["other"] = {**backend_entry, "include_tags": ["public"]}
        names = {f"filtered_{name}" for name in expected} | {"other_public_tool"}

    async with Client({"mcpServers": servers}) as client:
        assert {tool.name for tool in await client.list_tools()} == names
        if server_count == 2:
            assert (await client.call_tool("other_public_tool")).data == "public"
