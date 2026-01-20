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

        assert "tool:test_tool@" in provider._components
        assert provider._components["tool:test_tool@"] is tool

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

        assert "tool:tool1@" in provider._components
        assert "tool:tool2@" in provider._components

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

        assert "tool:test_tool@" not in provider._components

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

        assert "resource:resource://test@" in provider._components

    def test_remove_resource(self):
        """Test removing a resource from LocalProvider."""
        provider = LocalProvider()

        @provider.resource("resource://test")
        def test_resource() -> str:
            return "content"

        provider.remove_resource("resource://test")

        assert "resource:resource://test@" not in provider._components

    def test_add_template(self):
        """Test adding a resource template to LocalProvider."""
        provider = LocalProvider()

        @provider.resource("resource://{id}")
        def template_fn(id: str) -> str:
            return f"Resource {id}"

        assert "template:resource://{id}@" in provider._components

    def test_remove_template(self):
        """Test removing a resource template from LocalProvider."""
        provider = LocalProvider()

        @provider.resource("resource://{id}")
        def template_fn(id: str) -> str:
            return f"Resource {id}"

        provider.remove_template("resource://{id}")

        assert "template:resource://{id}@" not in provider._components

    def test_add_prompt(self):
        """Test adding a prompt to LocalProvider."""
        provider = LocalProvider()

        prompt = Prompt(
            name="test_prompt",
            description="A test prompt",
        )
        provider.add_prompt(prompt)

        assert "prompt:test_prompt@" in provider._components

    def test_remove_prompt(self):
        """Test removing a prompt from LocalProvider."""
        provider = LocalProvider()

        prompt = Prompt(
            name="test_prompt",
            description="A test prompt",
        )
        provider.add_prompt(prompt)
        provider.remove_prompt("test_prompt")

        assert "prompt:test_prompt@" not in provider._components


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
    """Tests for LocalProvider decorator registration.

    Note: Decorator calling patterns and metadata are tested in the standalone
    decorator tests (tests/tools/test_standalone_decorator.py, etc.). These tests
    focus on LocalProvider-specific behavior: registration into _components,
    the enabled flag, and round-trip execution via Client.
    """

    def test_tool_decorator_registers(self):
        """Tool decorator should register in _components."""
        provider = LocalProvider()

        @provider.tool
        def my_tool(x: int) -> int:
            return x * 2

        assert "tool:my_tool@" in provider._components
        assert provider._components["tool:my_tool@"].name == "my_tool"

    def test_tool_decorator_with_custom_name_registers(self):
        """Tool with custom name should register under that name."""
        provider = LocalProvider()

        @provider.tool(name="custom_name")
        def my_tool(x: int) -> int:
            return x * 2

        assert "tool:custom_name@" in provider._components
        assert "tool:my_tool@" not in provider._components

    def test_tool_direct_call(self):
        """provider.tool(fn) should register the function."""
        provider = LocalProvider()

        def my_tool(x: int) -> int:
            return x * 2

        provider.tool(my_tool, name="direct_tool")

        assert "tool:direct_tool@" in provider._components

    def test_tool_enabled_false(self):
        """Tool with enabled=False should add a Visibility transform."""
        provider = LocalProvider()

        @provider.tool(enabled=False)
        def disabled_tool() -> str:
            return "should be disabled"

        assert "tool:disabled_tool@" in provider._components
        # enabled=False adds a Visibility transform to disable the tool
        from fastmcp.server.transforms.visibility import Visibility

        enabled_transforms = [
            t for t in provider.transforms if isinstance(t, Visibility)
        ]
        assert len(enabled_transforms) == 1
        assert enabled_transforms[0]._enabled is False
        assert enabled_transforms[0].keys == {"tool:disabled_tool@"}

    async def test_tool_enabled_false_not_listed(self):
        """Disabled tool should not appear in get_tools (filtering happens at server level)."""
        provider = LocalProvider()

        @provider.tool(enabled=False)
        def disabled_tool() -> str:
            return "should be disabled"

        @provider.tool
        def enabled_tool() -> str:
            return "should be enabled"

        # Filtering happens at the server level, not provider level
        server = FastMCP("Test", providers=[provider])
        tools = await server.list_tools()
        names = {t.name for t in tools}
        assert "enabled_tool" in names
        assert "disabled_tool" not in names

    async def test_server_enable_overrides_provider_disable(self):
        """Server-level enable should override provider-level disable."""
        provider = LocalProvider()

        @provider.tool(enabled=False)
        def my_tool() -> str:
            return "result"

        server = FastMCP("Test", providers=[provider])

        # Tool is disabled at provider level
        assert await server.get_tool("my_tool") is None

        # Server-level enable overrides it
        server.enable(names={"my_tool"})
        tool = await server.get_tool("my_tool")
        assert tool is not None
        assert tool.name == "my_tool"

    async def test_tool_roundtrip(self):
        """Tool should execute correctly via Client."""
        provider = LocalProvider()

        @provider.tool
        def add(a: int, b: int) -> int:
            return a + b

        server = FastMCP("Test", providers=[provider])

        async with Client(server) as client:
            result = await client.call_tool("add", {"a": 2, "b": 3})
            assert result.data == 5

    def test_resource_decorator_registers(self):
        """Resource decorator should register in _components."""
        provider = LocalProvider()

        @provider.resource("resource://test")
        def my_resource() -> str:
            return "test content"

        assert "resource:resource://test@" in provider._components

    def test_resource_with_custom_name_registers(self):
        """Resource with custom name should register with that name."""
        provider = LocalProvider()

        @provider.resource("resource://test", name="custom_name")
        def my_resource() -> str:
            return "test content"

        assert provider._components["resource:resource://test@"].name == "custom_name"

    def test_resource_enabled_false(self):
        """Resource with enabled=False should add a Visibility transform."""
        provider = LocalProvider()

        @provider.resource("resource://test", enabled=False)
        def disabled_resource() -> str:
            return "should be disabled"

        assert "resource:resource://test@" in provider._components
        # enabled=False adds a Visibility transform to disable the resource
        from fastmcp.server.transforms.visibility import Visibility

        enabled_transforms = [
            t for t in provider.transforms if isinstance(t, Visibility)
        ]
        assert len(enabled_transforms) == 1
        assert enabled_transforms[0]._enabled is False
        assert enabled_transforms[0].keys == {"resource:resource://test@"}

    async def test_resource_enabled_false_not_listed(self):
        """Disabled resource should not appear in get_resources (filtering at server level)."""
        provider = LocalProvider()

        @provider.resource("resource://disabled", enabled=False)
        def disabled_resource() -> str:
            return "should be disabled"

        @provider.resource("resource://enabled")
        def enabled_resource() -> str:
            return "should be enabled"

        # Filtering happens at the server level, not provider level
        server = FastMCP("Test", providers=[provider])
        resources = await server.list_resources()
        uris = {str(r.uri) for r in resources}
        assert "resource://enabled" in uris
        assert "resource://disabled" not in uris

    def test_template_enabled_false(self):
        """Template with enabled=False should add a Visibility transform."""
        provider = LocalProvider()

        @provider.resource("data://{id}", enabled=False)
        def disabled_template(id: str) -> str:
            return f"Data {id}"

        assert "template:data://{id}@" in provider._components
        # enabled=False adds a Visibility transform to disable the template
        from fastmcp.server.transforms.visibility import Visibility

        enabled_transforms = [
            t for t in provider.transforms if isinstance(t, Visibility)
        ]
        assert len(enabled_transforms) == 1
        assert enabled_transforms[0]._enabled is False
        assert enabled_transforms[0].keys == {"template:data://{id}@"}

    async def test_template_enabled_false_not_listed(self):
        """Disabled template should not appear in get_resource_templates (filtering at server level)."""
        provider = LocalProvider()

        @provider.resource("data://{id}", enabled=False)
        def disabled_template(id: str) -> str:
            return f"Data {id}"

        @provider.resource("items://{id}")
        def enabled_template(id: str) -> str:
            return f"Item {id}"

        # Filtering happens at the server level, not provider level
        server = FastMCP("Test", providers=[provider])
        templates = await server.list_resource_templates()
        uris = {t.uri_template for t in templates}
        assert "items://{id}" in uris
        assert "data://{id}" not in uris

    async def test_resource_roundtrip(self):
        """Resource should execute correctly via Client."""
        provider = LocalProvider()

        @provider.resource("resource://greeting")
        def greeting() -> str:
            return "Hello, World!"

        server = FastMCP("Test", providers=[provider])

        async with Client(server) as client:
            result = await client.read_resource("resource://greeting")
            assert "Hello, World!" in str(result)

    def test_prompt_decorator_registers(self):
        """Prompt decorator should register in _components."""
        provider = LocalProvider()

        @provider.prompt
        def my_prompt() -> str:
            return "A prompt"

        assert "prompt:my_prompt@" in provider._components

    def test_prompt_with_custom_name_registers(self):
        """Prompt with custom name should register under that name."""
        provider = LocalProvider()

        @provider.prompt(name="custom_prompt")
        def my_prompt() -> str:
            return "A prompt"

        assert "prompt:custom_prompt@" in provider._components
        assert "prompt:my_prompt@" not in provider._components

    def test_prompt_enabled_false(self):
        """Prompt with enabled=False should add a Visibility transform."""
        provider = LocalProvider()

        @provider.prompt(enabled=False)
        def disabled_prompt() -> str:
            return "should be disabled"

        assert "prompt:disabled_prompt@" in provider._components
        # enabled=False adds a Visibility transform to disable the prompt
        from fastmcp.server.transforms.visibility import Visibility

        enabled_transforms = [
            t for t in provider.transforms if isinstance(t, Visibility)
        ]
        assert len(enabled_transforms) == 1
        assert enabled_transforms[0]._enabled is False
        assert enabled_transforms[0].keys == {"prompt:disabled_prompt@"}

    async def test_prompt_enabled_false_not_listed(self):
        """Disabled prompt should not appear in get_prompts (filtering at server level)."""
        provider = LocalProvider()

        @provider.prompt(enabled=False)
        def disabled_prompt() -> str:
            return "should be disabled"

        @provider.prompt
        def enabled_prompt() -> str:
            return "should be enabled"

        # Filtering happens at the server level, not provider level
        server = FastMCP("Test", providers=[provider])
        prompts = await server.list_prompts()
        names = {p.name for p in prompts}
        assert "enabled_prompt" in names
        assert "disabled_prompt" not in names

    async def test_prompt_roundtrip(self):
        """Prompt should execute correctly via Client."""
        provider = LocalProvider()

        @provider.prompt
        def greeting(name: str) -> str:
            return f"Hello, {name}!"

        server = FastMCP("Test", providers=[provider])

        async with Client(server) as client:
            result = await client.get_prompt("greeting", {"name": "World"})
            assert "Hello, World!" in str(result)


class TestProviderToolTransformations:
    """Tests for tool transformations via add_transform()."""

    async def test_add_transform_applies_tool_transforms(self):
        """Test that add_transform with ToolTransform applies tool transformations."""
        from fastmcp.server.transforms import ToolTransform
        from fastmcp.tools.tool_transform import ToolTransformConfig

        provider = LocalProvider()

        @provider.tool
        def my_tool(x: int) -> int:
            return x

        # Add transform layer
        layer = ToolTransform({"my_tool": ToolTransformConfig(name="renamed_tool")})
        provider.add_transform(layer)

        # Get tools and pass directly to transform
        tools = await provider.list_tools()
        transformed_tools = await layer.list_tools(tools)
        assert len(transformed_tools) == 1
        assert transformed_tools[0].name == "renamed_tool"

    async def test_transform_layer_get_tool(self):
        """Test that ToolTransform.get_tool works correctly."""
        from fastmcp.server.transforms import ToolTransform
        from fastmcp.tools.tool_transform import ToolTransformConfig

        provider = LocalProvider()

        @provider.tool
        def original_tool(x: int) -> int:
            return x

        layer = ToolTransform(
            {"original_tool": ToolTransformConfig(name="transformed_tool")}
        )

        # Get tool through layer with call_next
        async def get_tool(name: str, version=None):
            return await provider._get_tool(name, version)

        tool = await layer.get_tool("transformed_tool", get_tool)
        assert tool is not None
        assert tool.name == "transformed_tool"

        # Original name should not work
        tool = await layer.get_tool("original_tool", get_tool)
        assert tool is None

    async def test_transform_layer_description_change(self):
        """Test that ToolTransform can change description."""
        from fastmcp.server.transforms import ToolTransform
        from fastmcp.tools.tool_transform import ToolTransformConfig

        provider = LocalProvider()

        @provider.tool
        def my_tool(x: int) -> int:
            return x

        layer = ToolTransform(
            {"my_tool": ToolTransformConfig(description="New description")}
        )

        async def get_tool(name: str, version=None):
            return await provider._get_tool(name, version)

        tool = await layer.get_tool("my_tool", get_tool)
        assert tool is not None
        assert tool.description == "New description"

    async def test_provider_unaffected_by_transforms(self):
        """Test that provider's own tools are unchanged by layers stored on it."""
        from fastmcp.server.transforms import ToolTransform
        from fastmcp.tools.tool_transform import ToolTransformConfig

        provider = LocalProvider()

        @provider.tool
        def my_tool(x: int) -> int:
            return x

        # Add layer to provider (layers are applied by server, not _list_tools)
        layer = ToolTransform({"my_tool": ToolTransformConfig(name="renamed")})
        provider.add_transform(layer)

        # Provider's _list_tools returns raw tools (transforms applied when queried via list_tools)
        original_tools = await provider._list_tools()
        assert original_tools[0].name == "my_tool"

        # Transform modifies them when applied directly
        transformed_tools = await layer.list_tools(original_tools)
        assert transformed_tools[0].name == "renamed"

    def test_transform_layer_duplicate_target_name_raises_error(self):
        """Test that ToolTransform with duplicate target names raises ValueError."""
        from fastmcp.server.transforms import ToolTransform
        from fastmcp.tools.tool_transform import ToolTransformConfig

        with pytest.raises(ValueError, match="duplicate target name"):
            ToolTransform(
                {
                    "tool_a": ToolTransformConfig(name="same_name"),
                    "tool_b": ToolTransformConfig(name="same_name"),
                }
            )


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
        """Test that provider tools are visible via server.list_tools()."""
        provider = LocalProvider()

        @provider.tool
        def provider_tool() -> str:
            return "from provider"

        server = FastMCP("Test", providers=[provider])

        tools = await server.list_tools()
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

        tools = await server.list_tools()
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
        tools = await server.list_tools()
        assert any(t.name == "duplicate_tool" for t in tools)

        async with Client(server) as client:
            result = await client.call_tool("duplicate_tool", {})
            assert result.data == "from server"
