"""
Tests for MCP SEP-1686 task protocol behavior through proxy servers.

Proxy servers explicitly forbid task-augmented execution. All proxy components
(tools, prompts, resources) have task_config.mode="forbidden".

Clients connecting through proxies can:
- Execute tools/prompts/resources normally (sync execution)
- NOT use task-augmented execution (task=True fails gracefully for tools,
  raises McpError for prompts/resources)
"""

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import TextContent, TextResourceContents

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport
from fastmcp.server.providers.proxy import ProxyClient


@pytest.fixture
def backend_server() -> FastMCP:
    """Create a backend server with task-enabled components.

    The backend has tasks enabled, but the proxy should NOT forward
    task execution - it should treat all components as forbidden.
    """
    mcp = FastMCP("backend-server")

    @mcp.tool(task=True)
    async def add_numbers(a: int, b: int) -> int:
        """Add two numbers together."""
        return a + b

    @mcp.tool(task=False)
    async def sync_only_tool(message: str) -> str:
        """Tool that only supports synchronous execution."""
        return f"sync: {message}"

    @mcp.prompt(task=True)
    async def greeting_prompt(name: str) -> str:
        """A prompt that can execute as a task."""
        return f"Hello, {name}! Welcome to the system."

    @mcp.resource("data://info.txt", task=True)
    async def info_resource() -> str:
        """A resource that can be read as a task."""
        return "Important information from the backend"

    @mcp.resource("data://user/{user_id}.json", task=True)
    async def user_resource(user_id: str) -> str:
        """A resource template that can execute as a task."""
        return f'{{"id": "{user_id}", "name": "User {user_id}"}}'

    return mcp


@pytest.fixture
def proxy_server(backend_server: FastMCP) -> FastMCP:
    """Create a proxy server that forwards to the backend."""
    return FastMCP.as_proxy(ProxyClient(transport=FastMCPTransport(backend_server)))


class TestProxyToolsSyncExecution:
    """Test that tools work normally through proxy (sync execution)."""

    async def test_tool_sync_execution_works(self, proxy_server: FastMCP):
        """Tool called without task=True works through proxy."""
        async with Client(proxy_server) as client:
            result = await client.call_tool("add_numbers", {"a": 5, "b": 3})
            assert "8" in str(result)

    async def test_sync_only_tool_works(self, proxy_server: FastMCP):
        """Sync-only tool works through proxy."""
        async with Client(proxy_server) as client:
            result = await client.call_tool("sync_only_tool", {"message": "test"})
            assert "sync: test" in str(result)


class TestProxyToolsTaskForbidden:
    """Test that tools with task=True are forbidden through proxy."""

    async def test_tool_task_returns_error_immediately(self, proxy_server: FastMCP):
        """Tool called with task=True through proxy returns error immediately."""
        async with Client(proxy_server) as client:
            task = await client.call_tool("add_numbers", {"a": 5, "b": 3}, task=True)

            # Should return immediately (forbidden behavior)
            assert task.returned_immediately

            # Result should be an error
            result = await task.result()
            assert result.is_error

    async def test_sync_only_tool_task_returns_error_immediately(
        self, proxy_server: FastMCP
    ):
        """Sync-only tool with task=True also returns error immediately."""
        async with Client(proxy_server) as client:
            task = await client.call_tool(
                "sync_only_tool", {"message": "test"}, task=True
            )

            assert task.returned_immediately
            result = await task.result()
            assert result.is_error


class TestProxyPromptsSyncExecution:
    """Test that prompts work normally through proxy (sync execution)."""

    async def test_prompt_sync_execution_works(self, proxy_server: FastMCP):
        """Prompt called without task=True works through proxy."""
        async with Client(proxy_server) as client:
            result = await client.get_prompt("greeting_prompt", {"name": "Alice"})
            assert isinstance(result.messages[0].content, TextContent)
            assert "Hello, Alice!" in result.messages[0].content.text


class TestProxyPromptsTaskForbidden:
    """Test that prompts with task=True are forbidden through proxy."""

    async def test_prompt_task_raises_mcp_error(self, proxy_server: FastMCP):
        """Prompt called with task=True through proxy raises McpError."""
        async with Client(proxy_server) as client:
            with pytest.raises(McpError) as exc_info:
                await client.get_prompt("greeting_prompt", {"name": "Alice"}, task=True)

            assert "does not support task-augmented execution" in str(exc_info.value)


class TestProxyResourcesSyncExecution:
    """Test that resources work normally through proxy (sync execution)."""

    async def test_resource_sync_execution_works(self, proxy_server: FastMCP):
        """Resource read without task=True works through proxy."""
        async with Client(proxy_server) as client:
            result = await client.read_resource("data://info.txt")
            assert isinstance(result[0], TextResourceContents)
            assert "Important information from the backend" in result[0].text

    async def test_resource_template_sync_execution_works(self, proxy_server: FastMCP):
        """Resource template without task=True works through proxy."""
        async with Client(proxy_server) as client:
            result = await client.read_resource("data://user/42.json")
            assert isinstance(result[0], TextResourceContents)
            assert '"id": "42"' in result[0].text


class TestProxyResourcesTaskForbidden:
    """Test that resources with task=True are forbidden through proxy."""

    async def test_resource_task_raises_mcp_error(self, proxy_server: FastMCP):
        """Resource read with task=True through proxy raises McpError."""
        async with Client(proxy_server) as client:
            with pytest.raises(McpError) as exc_info:
                await client.read_resource("data://info.txt", task=True)

            assert "does not support task-augmented execution" in str(exc_info.value)

    async def test_resource_template_task_raises_mcp_error(self, proxy_server: FastMCP):
        """Resource template with task=True through proxy raises McpError."""
        async with Client(proxy_server) as client:
            with pytest.raises(McpError) as exc_info:
                await client.read_resource("data://user/42.json", task=True)

            assert "does not support task-augmented execution" in str(exc_info.value)
