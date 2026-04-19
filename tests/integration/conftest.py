"""Conftest for server integration tests with reusable fixtures.

This module provides fixtures for creating MCP servers and clients in integration tests.
All fixtures are designed to work with pytest-asyncio and use in-memory transport
for fast, reliable testing without process forking.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from fastmcp import Client, Context, FastMCP
from fastmcp.client.transports import FastMCPTransport


@pytest.fixture
def sample_tools() -> dict[str, Callable[..., Any]]:
    """Fixture providing a set of sample tools for integration testing.

    This fixture returns a dictionary of pre-built tool functions covering
    common testing scenarios: echo, arithmetic, dictionary lookup, error
    generation, and progress reporting.

    Returns:
        A dictionary mapping tool names to tool functions:
        - "echo": Returns the input message unchanged
        - "add": Adds two integers and returns the result
        - "lookup": Looks up a key in a predefined sample dictionary
        - "error_tool": Always raises a ValueError for testing error handling
        - "progress_tool": An async tool that reports progress during execution

    The sample dictionary for lookup contains:
        - "key1" -> "value1"
        - "key2" -> "value2"
        - "key3" -> "value3"

    Example:
        ```python
        @pytest.mark.asyncio
        async def test_example(sample_tools):
            server = FastMCP("TestServer")

            for name, tool_fn in sample_tools.items():
                server.tool()(tool_fn)

            async with Client(transport=FastMCPTransport(server)) as client:
                result = await client.call_tool("echo", {"message": "Hello"})
                assert result.data == "Hello"
        ```
    """
    sample_data = {"key1": "value1", "key2": "value2", "key3": "value3"}

    def echo(message: str) -> str:
        """Echo the input message."""
        return message

    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    def lookup(key: str) -> str | None:
        """Look up a key in the sample data dictionary.

        Args:
            key: The key to look up. Must be one of "key1", "key2", or "key3".

        Returns:
            The value if found, None otherwise.
        """
        return sample_data.get(key)

    def error_tool() -> None:
        """A tool that always raises an error.

        Raises:
            ValueError: Always raised with message "This is a test error from error_tool".
        """
        raise ValueError("This is a test error from error_tool")

    async def progress_tool(context: Context) -> str:
        """An async tool that reports progress during execution.

        This tool demonstrates the progress reporting API. It reports progress
        at 5 steps (20%, 40%, 60%, 80%, 100%) before completing.

        Args:
            context: The FastMCP Context object used for progress reporting.

        Returns:
            The string "Progress complete" after all progress is reported.
        """
        for i in range(5):
            await context.report_progress(
                progress=i + 1,
                total=5,
                message=f"Step {i + 1} of 5",
            )
        return "Progress complete"

    return {
        "echo": echo,
        "add": add,
        "lookup": lookup,
        "error_tool": error_tool,
        "progress_tool": progress_tool,
    }


@pytest.fixture
async def basic_test_server(
    sample_tools: dict[str, Callable[..., Any]],
) -> FastMCP:
    """Fixture providing a pre-configured server with sample tools and a resource.

    This is the most convenient fixture for quick testing. It creates a server
    named "BasicTestServer" with all the sample tools (echo, add, lookup,
    error_tool, progress_tool) and a sample resource at "test://sample/resource".

    The server is created fresh for each test, ensuring test isolation.

    Returns:
        A FastMCP server instance with:
        - 5 sample tools (echo, add, lookup, error_tool, progress_tool)
        - 1 sample resource at URI "test://sample/resource"

    Usage:
        ```python
        @pytest.mark.asyncio
        async def test_example(basic_test_server):
            async with Client(transport=FastMCPTransport(basic_test_server)) as client:
                result = await client.call_tool("echo", {"message": "Hello"})
                assert result.data == "Hello"

                result = await client.call_tool("add", {"a": 2, "b": 3})
                assert result.data == 5

                from pydantic import AnyUrl
                resource = await client.read_resource(AnyUrl("test://sample/resource"))
        ```
    """
    server = FastMCP("BasicTestServer")

    for name, tool_fn in sample_tools.items():
        server.tool()(tool_fn)

    @server.resource(uri="test://sample/resource")
    async def sample_resource() -> str:
        return '{"message": "Hello from sample resource", "value": 42}'

    return server
