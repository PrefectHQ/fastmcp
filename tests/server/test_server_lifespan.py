"""Tests for server_lifespan and session_lifespan behavior."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import Client, FastMCP
from fastmcp.server.context import Context


class TestServerLifespan:
    """Test server_lifespan functionality."""

    async def test_server_lifespan_basic(self):
        """Test that server_lifespan is entered once and persists across sessions."""
        lifespan_events: list[str] = []

        @asynccontextmanager
        async def server_lifespan(mcp: FastMCP) -> AsyncIterator[dict[str, Any]]:
            lifespan_events.append("enter")
            try:
                yield {"initialized": True}
            finally:
                lifespan_events.append("exit")

        mcp = FastMCP("TestServer", lifespan=server_lifespan)

        @mcp.tool
        def get_value() -> str:
            return "test"

        # Server lifespan should be entered when run_async starts
        assert lifespan_events == []

        # Connect first client session
        async with Client(mcp) as client1:
            result1 = await client1.call_tool("get_value", {})
            assert result1.data == "test"
            # Server lifespan should have been entered once
            assert lifespan_events == ["enter"]

            # Connect second client session while first is still active
            async with Client(mcp) as client2:
                result2 = await client2.call_tool("get_value", {})
                assert result2.data == "test"
                # Server lifespan should still only have been entered once
                assert lifespan_events == ["enter"]

        # Because we're using a fastmcptransport, the server lifespan should be exited
        # when the client session closes
        assert lifespan_events == ["enter", "exit"]

    async def test_server_lifespan_context_available(self):
        """Test that server_lifespan context is available to tools."""

        @asynccontextmanager
        async def server_lifespan(mcp: FastMCP) -> AsyncIterator[dict]:
            yield {"db_connection": "mock_db"}

        mcp = FastMCP("TestServer", lifespan=server_lifespan)

        @mcp.tool
        def get_db_info(ctx: Context) -> str:
            # Access the server lifespan context
            assert ctx.request_context is not None
            lifespan_context = ctx.request_context.lifespan_context
            return lifespan_context.get("db_connection", "no_db")

        async with Client(mcp) as client:
            result = await client.call_tool("get_db_info", {})
            assert result.data == "mock_db"
