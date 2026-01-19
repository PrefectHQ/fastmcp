"""Core versioning functionality: VersionKey, utilities, and components."""
# ruff: noqa: F811  # Intentional function redefinition for version testing

from __future__ import annotations

from mcp.types import TextContent

from fastmcp import FastMCP
from fastmcp.utilities.versions import (
    VersionKey,
    compare_versions,
    is_version_greater,
)


class TestVersionKey:
    """Tests for VersionKey comparison class."""

    def test_none_sorts_lowest(self):
        """None (unversioned) should sort lower than any version."""
        assert VersionKey(None) < VersionKey("1.0")
        assert VersionKey(None) < VersionKey("0.1")
        assert VersionKey(None) < VersionKey("anything")

    def test_none_equals_none(self):
        """Two None versions should be equal."""
        assert VersionKey(None) == VersionKey(None)
        assert not (VersionKey(None) < VersionKey(None))
        assert not (VersionKey(None) > VersionKey(None))

    def test_pep440_versions_compared_semantically(self):
        """Valid PEP 440 versions should compare semantically."""
        assert VersionKey("1.0") < VersionKey("2.0")
        assert VersionKey("1.0") < VersionKey("1.1")
        assert VersionKey("1.9") < VersionKey("1.10")  # Semantic, not string
        assert VersionKey("2") < VersionKey("10")  # Semantic, not string

    def test_v_prefix_stripped(self):
        """Versions with 'v' prefix should be handled correctly."""
        assert VersionKey("v1.0") == VersionKey("1.0")
        assert VersionKey("v2.0") > VersionKey("v1.0")

    def test_string_fallback_for_invalid_versions(self):
        """Invalid PEP 440 versions should fall back to string comparison."""
        # Dates are not valid PEP 440
        assert VersionKey("2024-01-01") < VersionKey("2025-01-01")
        # String comparison (lexicographic)
        assert VersionKey("alpha") < VersionKey("beta")

    def test_pep440_sorts_before_strings(self):
        """PEP 440 versions sort before invalid string versions."""
        # "1.0" is valid PEP 440, "not-semver" is not
        assert VersionKey("1.0") < VersionKey("not-semver")
        assert VersionKey("999.0") < VersionKey("aaa")  # PEP 440 < string

    def test_repr(self):
        """Test string representation."""
        assert repr(VersionKey("1.0")) == "VersionKey('1.0')"
        assert repr(VersionKey(None)) == "VersionKey(None)"


class TestVersionFunctions:
    """Tests for version comparison functions."""

    def test_compare_versions(self):
        """Test compare_versions function."""
        assert compare_versions("1.0", "2.0") == -1
        assert compare_versions("2.0", "1.0") == 1
        assert compare_versions("1.0", "1.0") == 0
        assert compare_versions(None, "1.0") == -1
        assert compare_versions("1.0", None) == 1
        assert compare_versions(None, None) == 0

    def test_is_version_greater(self):
        """Test is_version_greater function."""
        assert is_version_greater("2.0", "1.0")
        assert not is_version_greater("1.0", "2.0")
        assert not is_version_greater("1.0", "1.0")
        assert is_version_greater("1.0", None)
        assert not is_version_greater(None, "1.0")


class TestComponentVersioning:
    """Tests for versioning in FastMCP components."""

    async def test_tool_with_version(self):
        """Tool version should be reflected in key."""
        mcp = FastMCP()

        @mcp.tool(version="2.0")
        def my_tool(x: int) -> int:
            return x * 2

        tools = await mcp.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "my_tool"
        assert tools[0].version == "2.0"
        assert tools[0].key == "tool:my_tool@2.0"

    async def test_tool_without_version(self):
        """Tool without version should have @ sentinel in key but empty version."""
        mcp = FastMCP()

        @mcp.tool
        def my_tool(x: int) -> int:
            return x * 2

        tools = await mcp.list_tools()
        assert len(tools) == 1
        assert tools[0].version is None
        # Keys always have @ sentinel for unambiguous parsing
        assert tools[0].key == "tool:my_tool@"

    async def test_tool_version_as_int(self):
        """Tool version as int should be coerced to string."""
        mcp = FastMCP()

        @mcp.tool(version=2)
        def my_tool(x: int) -> int:
            return x * 2

        tools = await mcp.list_tools()
        assert len(tools) == 1
        assert tools[0].version == "2"
        assert tools[0].key == "tool:my_tool@2"

    async def test_tool_version_zero_is_truthy(self):
        """Version 0 should become "0" (truthy string), not empty."""
        mcp = FastMCP()

        @mcp.tool(version=0)
        def my_tool(x: int) -> int:
            return x * 2

        tools = await mcp.list_tools()
        assert len(tools) == 1
        assert tools[0].version == "0"
        assert tools[0].key == "tool:my_tool@0"  # Not "tool:my_tool@"

    async def test_multiple_tool_versions_all_returned(self):
        """list_tools returns all versions; get_tool returns highest."""
        mcp = FastMCP()

        @mcp.tool(version="1.0")
        def add(x: int, y: int) -> int:
            return x + y

        @mcp.tool(version="2.0")
        def add(x: int, y: int, z: int = 0) -> int:
            return x + y + z

        # list_tools returns all versions
        tools = await mcp.list_tools()
        assert len(tools) == 2
        versions = {t.version for t in tools}
        assert versions == {"1.0", "2.0"}

        # get_tool returns highest version
        tool = await mcp.get_tool("add")
        assert tool is not None
        assert tool.version == "2.0"

    async def test_call_tool_invokes_highest_version(self):
        """Calling a tool by name should invoke the highest version."""
        mcp = FastMCP()

        @mcp.tool(version="1.0")
        def add(x: int, y: int) -> int:
            return x + y

        @mcp.tool(version="2.0")
        def add(x: int, y: int) -> int:
            return (x + y) * 10  # Different behavior to distinguish

        result = await mcp.call_tool("add", {"x": 1, "y": 2})
        # Should invoke v2.0 which multiplies by 10
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "30"

    async def test_mixing_versioned_and_unversioned_rejected(self):
        """Cannot mix versioned and unversioned tools with the same name."""
        import pytest

        mcp = FastMCP()

        @mcp.tool
        def my_tool() -> str:
            return "unversioned"

        # Adding versioned tool when unversioned exists should fail
        with pytest.raises(ValueError, match="versioned.*unversioned"):

            @mcp.tool(version="1.0")
            def my_tool() -> str:
                return "v1.0"

    async def test_mixing_unversioned_after_versioned_rejected(self):
        """Cannot add unversioned tool when versioned exists."""
        import pytest

        mcp = FastMCP()

        @mcp.tool(version="1.0")
        def my_tool() -> str:
            return "v1.0"

        # Adding unversioned tool when versioned exists should fail
        with pytest.raises(ValueError, match="unversioned.*versioned"):

            @mcp.tool
            def my_tool() -> str:
                return "unversioned"

    async def test_resource_with_version(self):
        """Resource version should work like tool version."""
        mcp = FastMCP()

        @mcp.resource("file:///config", version="1.0")
        def config_v1() -> str:
            return "config v1"

        @mcp.resource("file:///config", version="2.0")
        def config_v2() -> str:
            return "config v2"

        # list_resources returns all versions
        resources = await mcp.list_resources()
        assert len(resources) == 2
        versions = {r.version for r in resources}
        assert versions == {"1.0", "2.0"}

        # get_resource returns highest version
        resource = await mcp.get_resource("file:///config")
        assert resource is not None
        assert resource.version == "2.0"

    async def test_prompt_with_version(self):
        """Prompt version should work like tool version."""
        mcp = FastMCP()

        @mcp.prompt(version="1.0")
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        @mcp.prompt(version="2.0")
        def greet(name: str) -> str:
            return f"Greetings, {name}!"

        # list_prompts returns all versions
        prompts = await mcp.list_prompts()
        assert len(prompts) == 2
        versions = {p.version for p in prompts}
        assert versions == {"1.0", "2.0"}

        # get_prompt returns highest version
        prompt = await mcp.get_prompt("greet")
        assert prompt is not None
        assert prompt.version == "2.0"
