"""Tests for versioning in mounted servers."""
# ruff: noqa: F811  # Intentional function redefinition for version testing

from __future__ import annotations

from mcp.types import TextContent

from fastmcp import FastMCP
from fastmcp.utilities.versions import (
    VersionSpec,
)


class TestVersionSorting:
    """Tests for version sorting behavior."""

    async def test_semantic_version_sorting(self):
        """Versions should sort semantically, not lexicographically."""
        mcp = FastMCP()

        # Add versions out of order
        @mcp.tool(version="1")
        def count() -> int:
            return 1

        @mcp.tool(version="10")
        def count() -> int:
            return 10

        @mcp.tool(version="2")
        def count() -> int:
            return 2

        # list_tools returns all versions
        tools = await mcp.list_tools()
        assert len(tools) == 3
        versions = {t.version for t in tools}
        assert versions == {"1", "2", "10"}

        # get_tool returns highest (semantic: 10 > 2 > 1)
        tool = await mcp.get_tool("count")
        assert tool is not None
        assert tool.version == "10"

        # call_tool uses highest version
        result = await mcp.call_tool("count", {})
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "10"

    async def test_semver_sorting(self):
        """Full semver versions should sort correctly."""
        mcp = FastMCP()

        @mcp.tool(version="1.2.3")
        def info() -> str:
            return "1.2.3"

        @mcp.tool(version="1.2.10")
        def info() -> str:
            return "1.2.10"

        @mcp.tool(version="1.10.1")
        def info() -> str:
            return "1.10.1"

        # list_tools returns all versions
        tools = await mcp.list_tools()
        assert len(tools) == 3
        versions = {t.version for t in tools}
        assert versions == {"1.2.3", "1.2.10", "1.10.1"}

        # get_tool returns highest: 1.10.1 > 1.2.10 > 1.2.3 (semantic)
        tool = await mcp.get_tool("info")
        assert tool is not None
        assert tool.version == "1.10.1"

    async def test_v_prefix_normalized(self):
        """Versions with 'v' prefix should compare correctly."""
        mcp = FastMCP()

        @mcp.tool(version="v1.0")
        def calc() -> int:
            return 1

        @mcp.tool(version="v2.0")
        def calc() -> int:
            return 2

        # list_tools returns all versions
        tools = await mcp.list_tools()
        assert len(tools) == 2
        versions = {t.version for t in tools}
        assert versions == {"v1.0", "v2.0"}

        # get_tool returns highest
        tool = await mcp.get_tool("calc")
        assert tool is not None
        assert tool.version == "v2.0"


class TestMountedServerVersioning:
    """Tests for versioning in mounted servers (FastMCPProvider)."""

    async def test_mounted_tool_preserves_version(self):
        """Mounted tools should preserve their version info."""
        child = FastMCP("Child")

        @child.tool(version="2.0")
        def add(x: int, y: int) -> int:
            return x + y

        parent = FastMCP("Parent")
        parent.mount(child, "child")

        tools = await parent.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "child_add"
        assert tools[0].version == "2.0"

    async def test_mounted_resource_preserves_version(self):
        """Mounted resources should preserve their version info."""
        child = FastMCP("Child")

        @child.resource("file:///config", version="1.5")
        def config() -> str:
            return "config data"

        parent = FastMCP("Parent")
        parent.mount(child, "child")

        resources = await parent.list_resources()
        assert len(resources) == 1
        assert resources[0].version == "1.5"

    async def test_mounted_prompt_preserves_version(self):
        """Mounted prompts should preserve their version info."""
        child = FastMCP("Child")

        @child.prompt(version="3.0")
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        parent = FastMCP("Parent")
        parent.mount(child, "child")

        prompts = await parent.list_prompts()
        assert len(prompts) == 1
        assert prompts[0].name == "child_greet"
        assert prompts[0].version == "3.0"

    async def test_mounted_get_tool_with_version(self):
        """Should be able to get specific version from mounted server."""
        child = FastMCP("Child")

        @child.tool(version="1.0")
        def calc() -> int:
            return 1

        @child.tool(version="2.0")
        def calc() -> int:
            return 2

        parent = FastMCP("Parent")
        parent.mount(child, "child")

        # Get highest version (default)
        tool = await parent.get_tool("child_calc")
        assert tool is not None
        assert tool.version == "2.0"

        # Get specific version
        tool_v1 = await parent.get_tool("child_calc", VersionSpec(eq="1.0"))
        assert tool_v1 is not None
        assert tool_v1.version == "1.0"

    async def test_mounted_multiple_versions_all_returned(self):
        """Mounted server with multiple versions should show all versions."""
        child = FastMCP("Child")

        @child.tool(version="1.0")
        def my_tool() -> str:
            return "v1"

        @child.tool(version="3.0")
        def my_tool() -> str:
            return "v3"

        @child.tool(version="2.0")
        def my_tool() -> str:
            return "v2"

        parent = FastMCP("Parent")
        parent.mount(child, "child")

        # list_tools returns all versions
        tools = await parent.list_tools()
        assert len(tools) == 3
        versions = {t.version for t in tools}
        assert versions == {"1.0", "2.0", "3.0"}

        # get_tool returns highest
        tool = await parent.get_tool("child_my_tool")
        assert tool is not None
        assert tool.version == "3.0"

    async def test_mounted_call_tool_uses_highest_version(self):
        """Calling mounted tool should use highest version."""
        child = FastMCP("Child")

        @child.tool(version="1.0")
        def double(x: int) -> int:
            return x * 2

        @child.tool(version="2.0")
        def double(x: int) -> int:
            return x * 2 + 100  # Different behavior

        parent = FastMCP("Parent")
        parent.mount(child, "child")

        result = await parent.call_tool("child_double", {"x": 5})
        # Should use v2.0 which adds 100
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "110"

    async def test_mounted_tool_wrapper_executes_correct_version(self):
        """Calling a specific versioned tool wrapper should execute that version."""
        child = FastMCP("Child")

        @child.tool(version="1.0")
        def calc(x: int) -> int:
            return x * 10  # v1.0 multiplies by 10

        @child.tool(version="2.0")
        def calc(x: int) -> int:
            return x * 100  # v2.0 multiplies by 100

        parent = FastMCP("Parent")
        parent.mount(child, "child")

        # Get the v1.0 wrapper specifically
        tools = await parent.list_tools()
        v1_tool = next(
            t for t in tools if t.name == "child_calc" and t.version == "1.0"
        )

        # Calling the v1.0 wrapper should execute v1.0's logic
        result = await v1_tool.run({"x": 5})
        assert result.content[0].text == "50"  # 5 * 10, not 5 * 100

    async def test_mounted_resource_wrapper_reads_correct_version(self):
        """Reading a specific versioned resource should read that version."""
        from fastmcp.utilities.versions import VersionSpec

        child = FastMCP("Child")

        @child.resource("data:///config", version="1.0")
        def config_v1() -> str:
            return "config-v1-content"

        @child.resource("data:///config", version="2.0")
        def config_v2() -> str:
            return "config-v2-content"

        parent = FastMCP("Parent")
        parent.mount(child, "child")

        # Reading with version=1.0 should read v1.0's content
        result = await parent.read_resource(
            "data://child//config", version=VersionSpec(eq="1.0")
        )
        assert result.contents[0].content == "config-v1-content"

        # Reading with version=2.0 should read v2.0's content
        result = await parent.read_resource(
            "data://child//config", version=VersionSpec(eq="2.0")
        )
        assert result.contents[0].content == "config-v2-content"

    async def test_mounted_prompt_wrapper_renders_correct_version(self):
        """Rendering a specific versioned prompt should render that version."""
        from fastmcp.utilities.versions import VersionSpec

        child = FastMCP("Child")

        @child.prompt(version="1.0")
        def greeting(name: str) -> str:
            return f"Hello, {name}!"  # v1.0 says Hello

        @child.prompt(version="2.0")
        def greeting(name: str) -> str:
            return f"Greetings, {name}!"  # v2.0 says Greetings

        parent = FastMCP("Parent")
        parent.mount(child, "child")

        # Rendering with version=1.0 should render v1.0's content
        result = await parent.render_prompt(
            "child_greeting", {"name": "World"}, version=VersionSpec(eq="1.0")
        )
        content = result.messages[0].content
        assert isinstance(content, TextContent) and "Hello, World!" in content.text

        # Rendering with version=2.0 should render v2.0's content
        result = await parent.render_prompt(
            "child_greeting", {"name": "World"}, version=VersionSpec(eq="2.0")
        )
        content = result.messages[0].content
        assert isinstance(content, TextContent) and "Greetings, World!" in content.text

    async def test_deeply_nested_version_forwarding(self):
        """Verify version is correctly forwarded through multiple mount levels."""
        level3 = FastMCP("Level3")

        @level3.tool(version="1.0")
        def calc(x: int) -> int:
            return x * 10  # v1.0 multiplies by 10

        @level3.tool(version="2.0")
        def calc(x: int) -> int:
            return x * 100  # v2.0 multiplies by 100

        level2 = FastMCP("Level2")
        level2.mount(level3, "l3")

        level1 = FastMCP("Level1")
        level1.mount(level2, "l2")

        # All versions should be visible through two levels of mounting
        tools = await level1.list_tools()
        calc_tools = [t for t in tools if "calc" in t.name]
        assert len(calc_tools) == 2
        versions = {t.version for t in calc_tools}
        assert versions == {"1.0", "2.0"}

        # Get v1.0 wrapper through two levels of mounting
        v1_tool = next(t for t in tools if "calc" in t.name and t.version == "1.0")

        # Should execute v1.0 logic, not v2.0
        result = await v1_tool.run({"x": 5})
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "50"  # 5 * 10, not 5 * 100
