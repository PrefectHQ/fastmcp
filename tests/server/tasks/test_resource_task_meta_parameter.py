"""
Tests for the explicit task_meta parameter on FastMCP.read_resource().

These tests verify that the task_meta parameter provides explicit control
over sync vs task execution for resources and resource templates.
"""

import pytest
from mcp.shared.exceptions import McpError

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.resources.resource import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.tasks.config import TaskMeta


class TestResourceTaskMetaParameter:
    """Tests for task_meta parameter on FastMCP.read_resource()."""

    async def test_task_meta_none_returns_resource_result(self):
        """With task_meta=None (default), read_resource returns ResourceResult."""
        server = FastMCP("test")

        @server.resource("data://test")
        async def simple_resource() -> str:
            return "hello world"

        result = await server.read_resource("data://test")

        assert result.contents[0].content == "hello world"

    async def test_task_meta_none_on_task_enabled_resource_still_returns_result(self):
        """Even for task=True resources, task_meta=None returns ResourceResult."""
        server = FastMCP("test")

        @server.resource("data://test", task=True)
        async def task_enabled_resource() -> str:
            return "hello world"

        # Without task_meta, should execute synchronously
        result = await server.read_resource("data://test")

        assert result.contents[0].content == "hello world"

    async def test_task_meta_on_forbidden_resource_raises_error(self):
        """Providing task_meta to a task=False resource raises McpError."""
        server = FastMCP("test")

        @server.resource("data://test", task=False)
        async def sync_only_resource() -> str:
            return "hello"

        with pytest.raises(McpError) as exc_info:
            await server.read_resource("data://test", task_meta=TaskMeta())

        assert "does not support task-augmented execution" in str(exc_info.value)

    async def test_task_meta_fn_key_enrichment_for_resource(self):
        """Verify that fn_key enrichment uses Resource.make_key()."""
        resource_uri = "data://my-resource"
        expected_key = Resource.make_key(resource_uri)

        assert expected_key == "resource:data://my-resource"

    async def test_task_meta_fn_key_enrichment_for_template(self):
        """Verify that fn_key enrichment uses ResourceTemplate.make_key()."""
        template_pattern = "data://{id}"
        expected_key = ResourceTemplate.make_key(template_pattern)

        assert expected_key == "template:data://{id}"


class TestResourceTemplateTaslMeta:
    """Tests for task_meta with resource templates."""

    async def test_template_task_meta_none_returns_resource_result(self):
        """With task_meta=None, template read returns ResourceResult."""
        server = FastMCP("test")

        @server.resource("item://{id}")
        async def get_item(id: str) -> str:
            return f"Item {id}"

        result = await server.read_resource("item://42")

        assert result.contents[0].content == "Item 42"

    async def test_template_task_meta_on_task_enabled_template_returns_result(self):
        """Even for task=True templates, task_meta=None returns ResourceResult."""
        server = FastMCP("test")

        @server.resource("item://{id}", task=True)
        async def get_item(id: str) -> str:
            return f"Item {id}"

        # Without task_meta, should execute synchronously
        result = await server.read_resource("item://42")

        assert result.contents[0].content == "Item 42"

    async def test_template_task_meta_on_forbidden_template_raises_error(self):
        """Providing task_meta to a task=False template raises McpError."""
        server = FastMCP("test")

        @server.resource("item://{id}", task=False)
        async def sync_only_template(id: str) -> str:
            return f"Item {id}"

        with pytest.raises(McpError) as exc_info:
            await server.read_resource("item://42", task_meta=TaskMeta())

        assert "does not support task-augmented execution" in str(exc_info.value)


class TestResourceTaskMetaClientIntegration:
    """Tests that task_meta works correctly with the Client for resources."""

    async def test_client_read_resource_without_task_gets_immediate_result(self):
        """Client without task=True gets immediate result."""
        server = FastMCP("test")

        @server.resource("data://test", task=True)
        async def immediate_resource() -> str:
            return "hello"

        async with Client(server) as client:
            result = await client.read_resource("data://test")

            # Should get ReadResourceResult directly
            assert "hello" in str(result)

    async def test_client_read_resource_with_task_creates_task(self):
        """Client with task=True creates a background task."""
        server = FastMCP("test")

        @server.resource("data://test", task=True)
        async def task_resource() -> str:
            return "hello"

        async with Client(server) as client:
            from fastmcp.client.tasks import ResourceTask

            task = await client.read_resource("data://test", task=True)

            assert isinstance(task, ResourceTask)

            # Wait for result
            result = await task.result()
            assert "hello" in str(result)

    async def test_client_read_template_with_task_creates_task(self):
        """Client with task=True on template creates a background task."""
        server = FastMCP("test")

        @server.resource("item://{id}", task=True)
        async def get_item(id: str) -> str:
            return f"Item {id}"

        async with Client(server) as client:
            from fastmcp.client.tasks import ResourceTask

            task = await client.read_resource("item://42", task=True)

            assert isinstance(task, ResourceTask)

            # Wait for result
            result = await task.result()
            assert "Item 42" in str(result)


class TestResourceTaskMetaDirectServerCall:
    """Tests for direct server read_resource calls with task_meta."""

    async def test_resource_can_read_another_resource_with_task(self):
        """A resource can read another resource as a background task."""
        server = FastMCP("test")

        @server.resource("data://inner", task=True)
        async def inner_resource() -> str:
            return "inner data"

        @server.tool
        async def outer_tool() -> str:
            # Read inner resource as background task
            result = await server.read_resource("data://inner", task_meta=TaskMeta())
            # Should get CreateTaskResult since we provided task_meta
            return f"Created task: {result.task.taskId}"

        async with Client(server) as client:
            result = await client.call_tool("outer_tool", {})
            assert "Created task:" in str(result)

    async def test_resource_can_read_another_resource_synchronously(self):
        """A resource can read another resource synchronously (no task_meta)."""
        server = FastMCP("test")

        @server.resource("data://inner", task=True)
        async def inner_resource() -> str:
            return "inner data"

        @server.tool
        async def outer_tool() -> str:
            # Read inner resource synchronously (no task_meta)
            result = await server.read_resource("data://inner")
            # Should get ResourceResult directly
            return f"Got result: {result.contents[0].content}"

        async with Client(server) as client:
            result = await client.call_tool("outer_tool", {})
            assert "Got result: inner data" in str(result)

    async def test_resource_can_read_template_with_task(self):
        """A tool can read a resource template as a background task."""
        server = FastMCP("test")

        @server.resource("item://{id}", task=True)
        async def get_item(id: str) -> str:
            return f"Item {id}"

        @server.tool
        async def outer_tool() -> str:
            result = await server.read_resource("item://99", task_meta=TaskMeta())
            return f"Created task: {result.task.taskId}"

        async with Client(server) as client:
            result = await client.call_tool("outer_tool", {})
            assert "Created task:" in str(result)

    async def test_resource_can_read_with_custom_ttl(self):
        """A tool can read a resource as a background task with custom TTL."""
        server = FastMCP("test")

        @server.resource("data://inner", task=True)
        async def inner_resource() -> str:
            return "inner data"

        @server.tool
        async def outer_tool() -> str:
            custom_ttl = 45000  # 45 seconds
            result = await server.read_resource(
                "data://inner", task_meta=TaskMeta(ttl=custom_ttl)
            )
            return f"Task TTL: {result.task.ttl}"

        async with Client(server) as client:
            result = await client.call_tool("outer_tool", {})
            assert "Task TTL: 45000" in str(result)


class TestResourceTaskMetaTypeNarrowing:
    """Tests for type narrowing based on task_meta parameter."""

    async def test_read_resource_without_task_meta_type_is_resource_result(self):
        """Calling read_resource without task_meta returns ResourceResult type."""
        server = FastMCP("test")

        @server.resource("data://test")
        async def simple_resource() -> str:
            return "hello"

        # This should type-check as ResourceResult, not the union type
        result = await server.read_resource("data://test")

        # No isinstance check needed - type is narrowed by overload
        content = result.contents[0].content
        assert content == "hello"

    async def test_read_resource_with_task_meta_type_is_create_task_result(self):
        """Calling read_resource with task_meta returns CreateTaskResult type."""
        server = FastMCP("test")

        @server.resource("data://test", task=True)
        async def task_resource() -> str:
            return "hello"

        async with Client(server) as client:
            # Need to use client to get full task infrastructure
            from fastmcp.client.tasks import ResourceTask

            task = await client.read_resource("data://test", task=True)
            assert isinstance(task, ResourceTask)

            # For direct server call, we need the Client context for Docket
            # This test verifies the overload works via client integration
            result = await task.result()
            assert "hello" in str(result)
