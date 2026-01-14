"""
Tests for SEP-1686 task capabilities declaration.

Verifies that the server correctly advertises task support.
Task protocol is now always enabled.
"""

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.tasks import get_task_capabilities


async def test_capabilities_include_tasks():
    """Server capabilities always include tasks in first-class field (SEP-1686)."""
    mcp = FastMCP("capability-test")

    @mcp.tool()
    async def test_tool() -> str:
        return "test"

    async with Client(mcp) as client:
        # Get server initialization result which includes capabilities
        init_result = client.initialize_result

        # Verify tasks capability is present as a first-class field (not experimental)
        assert init_result.capabilities.tasks is not None
        assert init_result.capabilities.tasks == get_task_capabilities()
        # Verify it's NOT in experimental
        assert "tasks" not in (init_result.capabilities.experimental or {})


async def test_client_uses_task_capable_session():
    """Client uses task-capable initialization."""
    mcp = FastMCP("client-cap-test")

    @mcp.tool()
    async def test_tool() -> str:
        return "test"

    async with Client(mcp) as client:
        # Client should have connected successfully with task capabilities
        assert client.initialize_result is not None
        # Session should be a ClientSession (task-capable init uses standard session)
        assert type(client.session).__name__ == "ClientSession"
