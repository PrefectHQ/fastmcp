"""Comprehensive tests for LocalProvider.

Tests cover:
- Storage operations (add/remove tools, resources, templates, prompts)
- Provider interface (list/get operations)
- Decorator patterns (all calling styles)
- Tool transformations
- Standalone usage (provider attached to multiple servers)
- Task registration
"""

from typing import Any

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.prompts.prompt import Prompt
from fastmcp.server.providers.local_provider import LocalProvider
from fastmcp.server.tasks import TaskConfig
from fastmcp.tools.tool import Tool, ToolResult


class TestLocalProviderStorage:
    """Tests for LocalProvider storage operations."""

    def test_add_tool(self):
        """Test adding a tool to LocalProvider."""
        provider = LocalProvider()

        tool = Tool(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object", "properties": {}},
        )
        provider.add_tool(tool)

        assert "tool:test_tool" in provider._components
        assert provider._components["tool:test_tool"] is tool

    def test_add_multiple_tools(self):
        """Test adding multiple tools."""
        provider = LocalProvider()

        tool1 = Tool(
            name="tool1",
            description="First tool",
            parameters={"type": "object", "properties": {}},
        )
        tool2 = Tool(
            name="tool2",
            description="Second tool",
            parameters={"type": "object", "properties": {}},
        )
        provider.add_tool(tool1)
        provider.add_tool(tool2)

        assert "tool:tool1" in provider._components
        assert "tool:tool2" in provider._components

    def test_remove_tool(self):
        """Test removing a tool from LocalProvider."""
        provider = LocalProvider()

        tool = Tool(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object", "properties": {}},
        )
        provider.add_tool(tool)
        provider.remove_tool("test_tool")

        assert "tool:test_tool" not in provider._components

    def test_remove_nonexistent_tool_raises(self):
        """Test that removing a nonexistent tool raises KeyError."""
        provider = LocalProvider()

        with pytest.raises(KeyError):
            provider.remove_tool("nonexistent")

    def test_add_resource(self):
        """Test adding a resource to LocalProvider."""
        provider = LocalProvider()

        @provider.resource("resource://test")
        def test_resource() -> str:
            return "content"

        assert "resource:resource://test" in provider._components

    def test_remove_resource(self):
        """Test removing a resource from LocalProvider."""
        provider = LocalProvider()

        @provider.resource("resource://test")
        def test_resource() -> str:
            return "content"

        provider.remove_resource("resource://test")

        assert "resource:resource://test" not in provider._components

    def test_add_template(self):
        """Test adding a resource template to LocalProvider."""
        provider = LocalProvider()

        @provider.resource("resource://{id}")
        def template_fn(id: str) -> str:
            return f"Resource {id}"

        assert "template:resource://{id}" in provider._components

    def test_remove_template(self):
        """Test removing a resource template from LocalProvider."""
        provider = LocalProvider()

        @provider.resource("resource://{id}")
        def template_fn(id: str) -> str:
            return f"Resource {id}"

        provider.remove_template("resource://{id}")

        assert "template:resource://{id}" not in provider._components

    def test_add_prompt(self):
        """Test adding a prompt to LocalProvider."""
        provider = LocalProvider()

        prompt = Prompt(
            name="test_prompt",
            description="A test prompt",
        )
        provider.add_prompt(prompt)

        assert "prompt:test_prompt" in provider._components

    def test_remove_prompt(self):
        """Test removing a prompt from LocalProvider."""
        provider = LocalProvider()

        prompt = Prompt(
            name="test_prompt",
            description="A test prompt",
        )
        provider.add_prompt(prompt)
        provider.remove_prompt("test_prompt")

        assert "prompt:test_prompt" not in provider._components


class TestLocalProviderInterface:
    """Tests for LocalProvider's Provider interface."""

    async def test_list_tools_empty(self):
        """Test listing tools when empty."""
        provider = LocalProvider()
        tools = await provider.list_tools()
        assert tools == []

    async def test_list_tools(self):
        """Test listing tools returns all stored tools."""
        provider = LocalProvider()

        tool1 = Tool(name="tool1", description="First", parameters={"type": "object"})
        tool2 = Tool(name="tool2", description="Second", parameters={"type": "object"})
        provider.add_tool(tool1)
        provider.add_tool(tool2)

        tools = await provider.list_tools()
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert names == {"tool1", "tool2"}

    async def test_get_tool_found(self):
        """Test getting a tool that exists."""
        provider = LocalProvider()

        tool = Tool(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object"},
        )
        provider.add_tool(tool)

        result = await provider.get_tool("test_tool")
        assert result is not None
        assert result.name == "test_tool"

    async def test_get_tool_not_found(self):
        """Test getting a tool that doesn't exist returns None."""
        provider = LocalProvider()
        result = await provider.get_tool("nonexistent")
        assert result is None

    async def test_list_resources(self):
        """Test listing resources."""
        provider = LocalProvider()

        @provider.resource("resource://test")
        def test_resource() -> str:
            return "content"

        resources = await provider.list_resources()
        assert len(resources) == 1
        assert str(resources[0].uri) == "resource://test"

    async def test_get_resource_found(self):
        """Test getting a resource that exists."""
        provider = LocalProvider()

        @provider.resource("resource://test")
        def test_resource() -> str:
            return "content"

        result = await provider.get_resource("resource://test")
        assert result is not None
        assert str(result.uri) == "resource://test"

    async def test_get_resource_not_found(self):
        """Test getting a resource that doesn't exist returns None."""
        provider = LocalProvider()
        result = await provider.get_resource("resource://nonexistent")
        assert result is None

    async def test_list_resource_templates(self):
        """Test listing resource templates."""
        provider = LocalProvider()

        @provider.resource("resource://{id}")
        def template_fn(id: str) -> str:
            return f"Resource {id}"

        templates = await provider.list_resource_templates()
        assert len(templates) == 1
        assert templates[0].uri_template == "resource://{id}"

    async def test_get_resource_template_match(self):
        """Test getting a template that matches a URI."""
        provider = LocalProvider()

        @provider.resource("resource://{id}")
        def template_fn(id: str) -> str:
            return f"Resource {id}"

        result = await provider.get_resource_template("resource://123")
        assert result is not None
        assert result.uri_template == "resource://{id}"

    async def test_get_resource_template_no_match(self):
        """Test getting a template with no match returns None."""
        provider = LocalProvider()

        @provider.resource("resource://{id}")
        def template_fn(id: str) -> str:
            return f"Resource {id}"

        result = await provider.get_resource_template("other://123")
        assert result is None

    async def test_list_prompts(self):
        """Test listing prompts."""
        provider = LocalProvider()

        prompt = Prompt(
            name="test_prompt",
            description="A test prompt",
        )
        provider.add_prompt(prompt)

        prompts = await provider.list_prompts()
        assert len(prompts) == 1
        assert prompts[0].name == "test_prompt"

    async def test_get_prompt_found(self):
        """Test getting a prompt that exists."""
        provider = LocalProvider()

        prompt = Prompt(
            name="test_prompt",
            description="A test prompt",
        )
        provider.add_prompt(prompt)

        result = await provider.get_prompt("test_prompt")
        assert result is not None
        assert result.name == "test_prompt"

    async def test_get_prompt_not_found(self):
        """Test getting a prompt that doesn't exist returns None."""
        provider = LocalProvider()
        result = await provider.get_prompt("nonexistent")
        assert result is None


class TestLocalProviderDecorators:
    """Tests for LocalProvider decorator methods."""

    def test_tool_decorator_bare(self):
        """Test @provider.tool without parentheses."""
        provider = LocalProvider()

        @provider.tool
        def my_tool(x: int) -> int:
            return x * 2

        assert "tool:my_tool" in provider._components
        assert provider._components["tool:my_tool"].name == "my_tool"

    def test_tool_decorator_with_parens(self):
        """Test @provider.tool() with empty parentheses."""
        provider = LocalProvider()

        @provider.tool()
        def my_tool(x: int) -> int:
            return x * 2

        assert "tool:my_tool" in provider._components

    def test_tool_decorator_with_name_kwarg(self):
        """Test @provider.tool(name='custom')."""
        provider = LocalProvider()

        @provider.tool(name="custom_name")
        def my_tool(x: int) -> int:
            return x * 2

        assert "tool:custom_name" in provider._components
        assert "tool:my_tool" not in provider._components

    def test_tool_decorator_with_description(self):
        """Test @provider.tool(description='...')."""
        provider = LocalProvider()

        @provider.tool(description="Custom description")
        def my_tool(x: int) -> int:
            return x * 2

        assert provider._components["tool:my_tool"].description == "Custom description"

    def test_tool_direct_call(self):
        """Test provider.tool(fn, name='...')."""
        provider = LocalProvider()

        def my_tool(x: int) -> int:
            return x * 2

        provider.tool(my_tool, name="direct_tool")

        assert "tool:direct_tool" in provider._components

    async def test_tool_decorator_execution(self):
        """Test that decorated tools execute correctly."""
        provider = LocalProvider()

        @provider.tool
        def add(a: int, b: int) -> int:
            return a + b

        server = FastMCP("Test", providers=[provider])

        async with Client(server) as client:
            result = await client.call_tool("add", {"a": 2, "b": 3})
            assert result.data == 5

    def test_resource_decorator(self):
        """Test @provider.resource decorator."""
        provider = LocalProvider()

        @provider.resource("resource://test")
        def my_resource() -> str:
            return "test content"

        assert "resource:resource://test" in provider._components

    def test_resource_decorator_with_name(self):
        """Test @provider.resource with custom name."""
        provider = LocalProvider()

        @provider.resource("resource://test", name="custom_name")
        def my_resource() -> str:
            return "test content"

        assert provider._components["resource:resource://test"].name == "custom_name"

    async def test_resource_decorator_execution(self):
        """Test that decorated resources execute correctly."""
        provider = LocalProvider()

        @provider.resource("resource://greeting")
        def greeting() -> str:
            return "Hello, World!"

        server = FastMCP("Test", providers=[provider])

        async with Client(server) as client:
            result = await client.read_resource("resource://greeting")
            assert "Hello, World!" in str(result)

    def test_prompt_decorator_bare(self):
        """Test @provider.prompt without parentheses."""
        provider = LocalProvider()

        @provider.prompt
        def my_prompt() -> str:
            return "A prompt"

        assert "prompt:my_prompt" in provider._components

    def test_prompt_decorator_with_parens(self):
        """Test @provider.prompt() with empty parentheses."""
        provider = LocalProvider()

        @provider.prompt()
        def my_prompt() -> str:
            return "A prompt"

        assert "prompt:my_prompt" in provider._components

    def test_prompt_decorator_with_name(self):
        """Test @provider.prompt(name='custom')."""
        provider = LocalProvider()

        @provider.prompt(name="custom_prompt")
        def my_prompt() -> str:
            return "A prompt"

        assert "prompt:custom_prompt" in provider._components
        assert "prompt:my_prompt" not in provider._components


class TestLocalProviderToolTransformations:
    """Tests for tool transformations in LocalProvider."""

    def test_add_tool_transformation(self):
        """Test adding a tool transformation."""
        from fastmcp.tools.tool_transform import ToolTransformConfig

        provider = LocalProvider()

        @provider.tool
        def my_tool(x: int) -> int:
            return x

        config = ToolTransformConfig(name="renamed_tool")
        provider.add_tool_transformation("my_tool", config)

        assert provider.get_tool_transformation("my_tool") is config

    async def test_list_tools_applies_transformations(self):
        """Test that list_tools applies transformations."""
        from fastmcp.tools.tool_transform import ToolTransformConfig

        provider = LocalProvider()

        @provider.tool
        def original_tool(x: int) -> int:
            return x

        config = ToolTransformConfig(name="transformed_tool")
        provider.add_tool_transformation("original_tool", config)

        tools = await provider.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "transformed_tool"

    async def test_get_tool_applies_transformation(self):
        """Test that get_tool applies transformation."""
        from fastmcp.tools.tool_transform import ToolTransformConfig

        provider = LocalProvider()

        @provider.tool
        def my_tool(x: int) -> int:
            return x

        config = ToolTransformConfig(description="New description")
        provider.add_tool_transformation("my_tool", config)

        tool = await provider.get_tool("my_tool")
        assert tool is not None
        assert tool.description == "New description"

    def test_remove_tool_transformation(self):
        """Test removing a tool transformation."""
        from fastmcp.tools.tool_transform import ToolTransformConfig

        provider = LocalProvider()

        @provider.tool
        def my_tool(x: int) -> int:
            return x

        config = ToolTransformConfig(name="renamed")
        provider.add_tool_transformation("my_tool", config)
        provider.remove_tool_transformation("my_tool")

        assert provider.get_tool_transformation("my_tool") is None


class TestLocalProviderTaskRegistration:
    """Tests for task registration in LocalProvider."""

    async def test_get_tasks_returns_task_eligible_tools(self):
        """Test that get_tasks returns tools with task support."""
        provider = LocalProvider()

        @provider.tool(task=True)
        async def background_tool(x: int) -> int:
            return x

        tasks = await provider.get_tasks()
        assert len(tasks) == 1
        assert tasks[0].name == "background_tool"

    async def test_get_tasks_filters_forbidden_tools(self):
        """Test that get_tasks excludes tools with forbidden task mode."""
        provider = LocalProvider()

        @provider.tool(task=False)
        def sync_only_tool(x: int) -> int:
            return x

        tasks = await provider.get_tasks()
        assert len(tasks) == 0

    async def test_get_tasks_includes_custom_tool_subclasses(self):
        """Test that custom Tool subclasses are included in get_tasks."""

        class CustomTool(Tool):
            task_config: TaskConfig = TaskConfig(mode="optional")
            parameters: dict[str, Any] = {"type": "object", "properties": {}}

            async def run(self, arguments: dict[str, Any]) -> ToolResult:
                return ToolResult(content="custom")

        provider = LocalProvider()
        provider.add_tool(CustomTool(name="custom", description="Custom tool"))

        tasks = await provider.get_tasks()
        assert len(tasks) == 1
        assert tasks[0].name == "custom"


class TestLocalProviderStandaloneUsage:
    """Tests for standalone LocalProvider usage patterns."""

    async def test_attach_provider_to_server(self):
        """Test that LocalProvider can be attached to a server."""
        provider = LocalProvider()

        @provider.tool
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        server = FastMCP("Test", providers=[provider])

        async with Client(server) as client:
            tools = await client.list_tools()
            assert any(t.name == "greet" for t in tools)

    async def test_attach_provider_to_multiple_servers(self):
        """Test that same provider can be attached to multiple servers."""
        provider = LocalProvider()

        @provider.tool
        def shared_tool() -> str:
            return "shared"

        server1 = FastMCP("Server1", providers=[provider])
        server2 = FastMCP("Server2", providers=[provider])

        async with Client(server1) as client1:
            tools1 = await client1.list_tools()
            assert any(t.name == "shared_tool" for t in tools1)

        async with Client(server2) as client2:
            tools2 = await client2.list_tools()
            assert any(t.name == "shared_tool" for t in tools2)

    async def test_tools_visible_via_server_get_tools(self):
        """Test that provider tools are visible via server.get_tools()."""
        provider = LocalProvider()

        @provider.tool
        def provider_tool() -> str:
            return "from provider"

        server = FastMCP("Test", providers=[provider])

        tools = await server.get_tools()
        assert any(t.name == "provider_tool" for t in tools)

    async def test_server_decorator_and_provider_tools_coexist(self):
        """Test that server decorators and provider tools coexist."""
        provider = LocalProvider()

        @provider.tool
        def provider_tool() -> str:
            return "from provider"

        server = FastMCP("Test", providers=[provider])

        @server.tool
        def server_tool() -> str:
            return "from server"

        tools = await server.get_tools()
        assert any(t.name == "provider_tool" for t in tools)
        assert any(t.name == "server_tool" for t in tools)

    async def test_local_provider_first_wins_duplicates(self):
        """Test that LocalProvider tools take precedence over added providers."""
        provider = LocalProvider()

        @provider.tool
        def duplicate_tool() -> str:
            return "from added provider"

        server = FastMCP("Test", providers=[provider])

        @server.tool
        def duplicate_tool() -> str:  # noqa: F811
            return "from server"

        # Server's LocalProvider is first, so its tool wins
        tools = await server.get_tools()
        assert any(t.name == "duplicate_tool" for t in tools)

        async with Client(server) as client:
            result = await client.call_tool("duplicate_tool", {})
            assert result.data == "from server"
