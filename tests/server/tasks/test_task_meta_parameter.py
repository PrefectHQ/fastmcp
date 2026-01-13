"""
Tests for the explicit task_meta parameter on FastMCP.call_tool().

These tests verify that the task_meta parameter provides explicit control
over sync vs task execution, replacing implicit contextvar-based behavior.
"""

import mcp.types
import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.server.tasks.config import TaskMeta
from fastmcp.tools.tool import Tool, ToolResult


class TestTaskMetaParameter:
    """Tests for task_meta parameter on FastMCP.call_tool()."""

    async def test_task_meta_none_returns_tool_result(self):
        """With task_meta=None (default), call_tool returns ToolResult."""
        server = FastMCP("test")

        @server.tool
        async def simple_tool(x: int) -> int:
            return x * 2

        result = await server.call_tool("simple_tool", {"x": 5})

        first_content = result.content[0]
        assert isinstance(first_content, mcp.types.TextContent)
        assert first_content.text == "10"

    async def test_task_meta_none_on_task_enabled_tool_still_returns_tool_result(self):
        """Even for task=True tools, task_meta=None returns ToolResult synchronously."""
        server = FastMCP("test")

        @server.tool(task=True)
        async def task_enabled_tool(x: int) -> int:
            return x * 2

        # Without task_meta, should execute synchronously
        result = await server.call_tool("task_enabled_tool", {"x": 5})

        first_content = result.content[0]
        assert isinstance(first_content, mcp.types.TextContent)
        assert first_content.text == "10"

    async def test_task_meta_on_forbidden_tool_raises_error(self):
        """Providing task_meta to a task=False tool raises ToolError."""
        server = FastMCP("test")

        @server.tool(task=False)
        async def sync_only_tool(x: int) -> int:
            return x * 2

        # Error is raised before docket is needed (McpError wrapped as ToolError)
        with pytest.raises(ToolError) as exc_info:
            await server.call_tool("sync_only_tool", {"x": 5}, task_meta=TaskMeta())

        assert "does not support task-augmented execution" in str(exc_info.value)

    async def test_task_meta_fn_key_auto_populated_in_call_tool(self):
        """fn_key is auto-populated from tool name in call_tool()."""
        server = FastMCP("test")

        @server.tool(task=True)
        async def auto_key_tool() -> str:
            return "done"

        # Verify fn_key starts as None
        task_meta = TaskMeta()
        assert task_meta.fn_key is None

        # call_tool enriches the task_meta before passing to _run
        # We test this via the client integration path
        async with Client(server) as client:
            result = await client.call_tool("auto_key_tool", {}, task=True)
            # Should succeed because fn_key was auto-populated
            from fastmcp.client.tasks import ToolTask

            assert isinstance(result, ToolTask)

    async def test_task_meta_fn_key_enrichment_logic(self):
        """Verify that fn_key enrichment uses Tool.make_key()."""
        # Direct test of the enrichment logic
        tool_name = "my_tool"
        expected_key = Tool.make_key(tool_name)

        assert expected_key == "tool:my_tool"


class TestTaskMetaTTL:
    """Tests for task_meta.ttl behavior."""

    async def test_task_with_custom_ttl_creates_task(self):
        """task_meta.ttl is passed through when creating tasks."""
        server = FastMCP("test")

        @server.tool(task=True)
        async def ttl_tool() -> str:
            return "done"

        custom_ttl_ms = 30000  # 30 seconds

        async with Client(server) as client:
            # Use client.call_tool with task=True and ttl
            task = await client.call_tool("ttl_tool", {}, task=True, ttl=custom_ttl_ms)

            from fastmcp.client.tasks import ToolTask

            assert isinstance(task, ToolTask)

            # Verify task completes successfully
            result = await task.result()
            assert "done" in str(result)

    async def test_task_without_ttl_uses_default(self):
        """task_meta.ttl=None uses docket.execution_ttl default."""
        server = FastMCP("test")

        @server.tool(task=True)
        async def default_ttl_tool() -> str:
            return "done"

        async with Client(server) as client:
            # Use client.call_tool with task=True, default ttl
            task = await client.call_tool("default_ttl_tool", {}, task=True)

            from fastmcp.client.tasks import ToolTask

            assert isinstance(task, ToolTask)

            # Verify task completes successfully
            result = await task.result()
            assert "done" in str(result)


class TrackingMiddleware(Middleware):
    """Middleware that tracks tool calls."""

    def __init__(self, calls: list[str]):
        super().__init__()
        self._calls = calls

    async def on_call_tool(
        self,
        context: MiddlewareContext[mcp.types.CallToolRequestParams],
        call_next: CallNext[mcp.types.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        if context.method:
            self._calls.append(context.method)
        return await call_next(context)


class TestTaskMetaMiddleware:
    """Tests that task_meta is properly propagated through middleware."""

    async def test_task_meta_propagated_through_middleware(self):
        """task_meta is passed through middleware chain."""
        server = FastMCP("test")
        middleware_saw_request: list[str] = []

        @server.tool(task=True)
        async def middleware_test_tool() -> str:
            return "done"

        server.add_middleware(TrackingMiddleware(middleware_saw_request))

        async with Client(server) as client:
            # Use client to trigger the middleware chain
            task = await client.call_tool("middleware_test_tool", {}, task=True)

            # Middleware should have run
            assert "tools/call" in middleware_saw_request

            # And task should have been created
            from fastmcp.client.tasks import ToolTask

            assert isinstance(task, ToolTask)


class TestTaskMetaClientIntegration:
    """Tests that task_meta works correctly with the Client."""

    async def test_client_task_true_maps_to_task_meta(self):
        """Client's task=True creates proper task_meta on server."""
        server = FastMCP("test")

        @server.tool(task=True)
        async def client_test_tool(x: int) -> int:
            return x * 2

        async with Client(server) as client:
            # Client passes task=True, server receives as task_meta
            task = await client.call_tool("client_test_tool", {"x": 5}, task=True)

            # Should get back a ToolTask (client wrapper)
            from fastmcp.client.tasks import ToolTask

            assert isinstance(task, ToolTask)

            # Wait for result
            result = await task.result()
            assert "10" in str(result)

    async def test_client_without_task_gets_immediate_result(self):
        """Client without task=True gets immediate result."""
        server = FastMCP("test")

        @server.tool(task=True)
        async def immediate_tool(x: int) -> int:
            return x * 2

        async with Client(server) as client:
            # No task=True, should execute synchronously
            result = await client.call_tool("immediate_tool", {"x": 5})

            # Should get CallToolResult directly
            assert "10" in str(result)

    async def test_client_task_with_custom_ttl(self):
        """Client can pass custom TTL for task execution."""
        server = FastMCP("test")

        @server.tool(task=True)
        async def custom_ttl_tool() -> str:
            return "done"

        custom_ttl_ms = 60000  # 60 seconds

        async with Client(server) as client:
            task = await client.call_tool(
                "custom_ttl_tool", {}, task=True, ttl=custom_ttl_ms
            )

            from fastmcp.client.tasks import ToolTask

            assert isinstance(task, ToolTask)

            # Verify task completes successfully
            result = await task.result()
            assert "done" in str(result)


class TestTaskMetaDirectServerCall:
    """Tests for direct server calls (tool calling another tool)."""

    async def test_tool_can_call_another_tool_with_task(self):
        """A tool can call another tool as a background task."""
        server = FastMCP("test")

        @server.tool(task=True)
        async def inner_tool(x: int) -> int:
            return x * 2

        @server.tool
        async def outer_tool(x: int) -> str:
            # Call inner tool as background task
            result = await server.call_tool(
                "inner_tool", {"x": x}, task_meta=TaskMeta()
            )
            # Should get CreateTaskResult since we're in server context
            return f"Created task: {result.task.taskId}"

        async with Client(server) as client:
            # Call outer_tool which internally calls inner_tool with task_meta
            result = await client.call_tool("outer_tool", {"x": 5})
            # The outer tool should have successfully created a background task
            assert "Created task:" in str(result)

    async def test_tool_can_call_another_tool_synchronously(self):
        """A tool can call another tool synchronously (no task_meta)."""
        server = FastMCP("test")

        @server.tool(task=True)
        async def inner_tool(x: int) -> int:
            return x * 2

        @server.tool
        async def outer_tool(x: int) -> str:
            # Call inner tool synchronously (no task_meta)
            result = await server.call_tool("inner_tool", {"x": x})
            # Should get ToolResult directly
            first_content = result.content[0]
            assert isinstance(first_content, mcp.types.TextContent)
            return f"Got result: {first_content.text}"

        async with Client(server) as client:
            result = await client.call_tool("outer_tool", {"x": 5})
            assert "Got result: 10" in str(result)

    async def test_tool_can_call_another_tool_with_custom_ttl(self):
        """A tool can call another tool as a background task with custom TTL."""
        server = FastMCP("test")

        @server.tool(task=True)
        async def inner_tool(x: int) -> int:
            return x * 2

        @server.tool
        async def outer_tool(x: int) -> str:
            custom_ttl = 45000  # 45 seconds
            result = await server.call_tool(
                "inner_tool", {"x": x}, task_meta=TaskMeta(ttl=custom_ttl)
            )
            return f"Task TTL: {result.task.ttl}"

        async with Client(server) as client:
            result = await client.call_tool("outer_tool", {"x": 5})
            # The inner tool task should have the custom TTL
            assert "Task TTL: 45000" in str(result)
