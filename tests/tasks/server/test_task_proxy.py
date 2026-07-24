"""
Tests for SEP-2663 task behavior through proxy servers.

SEP-2663 tasks are tools-only. Proxy servers force every proxied tool to
`task_config.mode="forbidden"`, so a tool that is `task=True` on the backend
runs *synchronously* through the proxy and is never tasked — even when the
client opts the tasks extension in for the request.
"""

import pytest
from docket import Docket
from fastmcp_tasks.models import CreateTaskResult

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport
from fastmcp.server import create_proxy
from fastmcp.tools.base import ToolResult
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import (
    _opted_in_request,
    auth_scope,
    running_task_server,
)


@pytest.fixture(autouse=True)
def reset_docket_memory_server():
    """Force a fresh memory:// Docket server bound to each test's event loop."""
    if hasattr(Docket, "_memory_server"):
        delattr(Docket, "_memory_server")
    yield
    if hasattr(Docket, "_memory_server"):
        delattr(Docket, "_memory_server")


async def call_tool_with_optin(server: FastMCP, name: str, arguments: dict):
    """Run a `tools/call` with the tasks opt-in bound into the request context."""
    with auth_scope(None), _opted_in_request(name, arguments, None):
        return await server.call_tool(name, arguments)


@pytest.fixture
def backend_server() -> FastMCP:
    """A backend server with a task-enabled tool.

    The backend has tasks enabled, but the proxy must NOT forward task
    execution — it treats every proxied tool as forbidden.
    """
    mcp = FastMCP("backend-server")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def add_numbers(a: int, b: int) -> int:
        """Add two numbers together."""
        return a + b

    @mcp.tool(task=False)
    async def sync_only_tool(message: str) -> str:
        """Tool that only supports synchronous execution."""
        return f"sync: {message}"

    return mcp


@pytest.fixture
def proxy_server(backend_server: FastMCP) -> FastMCP:
    """A proxy server that forwards to the backend, with tasks advertised."""
    proxy = create_proxy(FastMCPTransport(backend_server))
    proxy.add_extension(TasksExtension())
    return proxy


class TestProxyToolsSyncExecution:
    """Tools work normally through the proxy (synchronous execution)."""

    async def test_tool_sync_execution_works(self, proxy_server: FastMCP):
        """A tool called without opting in works through the proxy."""
        async with Client(proxy_server) as client:
            result = await client.call_tool("add_numbers", {"a": 5, "b": 3})
            assert "8" in str(result)

    async def test_sync_only_tool_works(self, proxy_server: FastMCP):
        """A sync-only tool works through the proxy."""
        async with Client(proxy_server) as client:
            result = await client.call_tool("sync_only_tool", {"message": "test"})
            assert "sync: test" in str(result)


class TestProxyToolsTaskForbidden:
    """A proxied tool never tasks, even when the client opts in."""

    async def test_task_enabled_tool_runs_sync_through_proxy(
        self, proxy_server: FastMCP
    ):
        """A backend `task=True` tool runs sync through the forbidden proxy."""
        async with running_task_server(proxy_server):
            result = await call_tool_with_optin(
                proxy_server, "add_numbers", {"a": 5, "b": 3}
            )

            # The forbidden proxy tool declines to task even with the opt-in.
            assert not isinstance(result, CreateTaskResult)
            assert isinstance(result, ToolResult)
            assert result.structured_content == {"result": 8}

    async def test_sync_only_tool_runs_sync_through_proxy(self, proxy_server: FastMCP):
        """A sync-only tool also runs sync through the proxy with the opt-in."""
        async with running_task_server(proxy_server):
            result = await call_tool_with_optin(
                proxy_server, "sync_only_tool", {"message": "test"}
            )

            assert not isinstance(result, CreateTaskResult)
            assert isinstance(result, ToolResult)
            assert result.structured_content == {"result": "sync: test"}
