"""Advertisement of the SEP-2663 tasks extension capability.

A server with the tasks extension registered advertises the
`io.modelcontextprotocol/tasks` extension in its capabilities; a server without
it does not.
"""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.utilities.tasks import TASKS_EXTENSION_ID
from fastmcp_tasks import TasksExtension


async def test_extension_capability_advertised():
    """The tasks extension is advertised when registered."""
    mcp = FastMCP("capability-test")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def my_tool() -> str:
        return "ok"

    async with Client(mcp, mode="auto") as client:
        extensions = client.server_capabilities.extensions or {}
        assert extensions.get(TASKS_EXTENSION_ID) == {}


async def test_extension_capability_absent_without_extension():
    """The tasks extension is not advertised when no extension is registered."""
    mcp = FastMCP("capability-test")

    @mcp.tool
    async def my_tool() -> str:
        return "ok"

    async with Client(mcp, mode="auto") as client:
        extensions = client.server_capabilities.extensions or {}
        assert TASKS_EXTENSION_ID not in extensions
