"""Tests for component versioning functionality."""
# ruff: noqa: F811  # Intentional function redefinition for version testing

from __future__ import annotations

from mcp.types import TextContent

from fastmcp import FastMCP
from fastmcp.utilities.versions import (
    VersionKey,
    VersionSpec,
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


class TestVersionFilter:
    """Tests for VersionFilter transform."""

    async def test_version_lt_filters_high_versions(self):
        """VersionFilter(version_lt='3.0') hides v3+, shows v1 and v2."""
        from fastmcp.server.transforms import VersionFilter

        mcp = FastMCP()

        @mcp.tool(version="1.0")
        def calc() -> int:
            return 1

        @mcp.tool(version="2.0")
        def calc() -> int:
            return 2

        @mcp.tool(version="3.0")
        def calc() -> int:
            return 3

        # Without filter, list_tools returns all versions
        tools = await mcp.list_tools()
        versions = {t.version for t in tools}
        assert versions == {"1.0", "2.0", "3.0"}

        # With filter, only v1 and v2 are visible
        mcp.add_transform(VersionFilter(version_lt="3.0"))
        tools = await mcp.list_tools()
        versions = {t.version for t in tools}
        assert versions == {"1.0", "2.0"}

        # get_tool returns highest matching version
        tool = await mcp.get_tool("calc")
        assert tool is not None
        assert tool.version == "2.0"

    async def test_version_gte_filters_low_versions(self):
        """VersionFilter(version_gte='2.0') hides v1, shows v2 and v3."""
        from fastmcp.server.transforms import VersionFilter

        mcp = FastMCP()

        @mcp.tool(version="1.0")
        def add(x: int) -> int:
            return x + 1

        @mcp.tool(version="2.0")
        def add(x: int) -> int:
            return x + 2

        @mcp.tool(version="3.0")
        def add(x: int) -> int:
            return x + 3

        mcp.add_transform(VersionFilter(version_gte="2.0"))

        # list_tools shows all matching versions (v2 and v3)
        tools = await mcp.list_tools()
        versions = {t.version for t in tools}
        assert versions == {"2.0", "3.0"}

        # get_tool returns highest matching version
        tool = await mcp.get_tool("add")
        assert tool is not None
        assert tool.version == "3.0"

        # Can request specific versions in range
        tool_v2 = await mcp.get_tool("add", VersionSpec(eq="2.0"))
        assert tool_v2 is not None
        assert tool_v2.version == "2.0"

        # Cannot request version outside range - returns None
        assert await mcp.get_tool("add", VersionSpec(eq="1.0")) is None

    async def test_version_range(self):
        """VersionFilter(version_gte='2.0', version_lt='3.0') shows only v2.x."""
        from fastmcp.server.transforms import VersionFilter

        mcp = FastMCP()

        @mcp.tool(version="1.0")
        def calc() -> int:
            return 1

        @mcp.tool(version="2.0")
        def calc() -> int:
            return 2

        @mcp.tool(version="2.5")
        def calc() -> int:
            return 25

        @mcp.tool(version="3.0")
        def calc() -> int:
            return 3

        mcp.add_transform(VersionFilter(version_gte="2.0", version_lt="3.0"))

        # list_tools shows all versions in range
        tools = await mcp.list_tools()
        versions = {t.version for t in tools}
        assert versions == {"2.0", "2.5"}

        # get_tool returns highest in range
        tool = await mcp.get_tool("calc")
        assert tool is not None
        assert tool.version == "2.5"

        # Can request specific versions in range
        tool_v2 = await mcp.get_tool("calc", VersionSpec(eq="2.0"))
        assert tool_v2 is not None
        assert tool_v2.version == "2.0"

        # Versions outside range are not accessible - return None
        assert await mcp.get_tool("calc", VersionSpec(eq="1.0")) is None
        assert await mcp.get_tool("calc", VersionSpec(eq="3.0")) is None

    async def test_unversioned_always_passes(self):
        """Unversioned components pass through any filter."""
        from fastmcp.server.transforms import VersionFilter

        mcp = FastMCP()

        @mcp.tool
        def unversioned_tool() -> str:
            return "unversioned"

        @mcp.tool(version="5.0")
        def versioned_tool() -> str:
            return "v5"

        # Filter that would exclude v5.0
        mcp.add_transform(VersionFilter(version_lt="3.0"))

        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "unversioned_tool" in names
        assert "versioned_tool" not in names

    async def test_date_versions(self):
        """Works with date-based versions like '2025-01-15'."""
        from fastmcp.server.transforms import VersionFilter

        mcp = FastMCP()

        @mcp.tool(version="2025-01-01")
        def report() -> str:
            return "jan"

        @mcp.tool(version="2025-06-01")
        def report() -> str:
            return "jun"

        @mcp.tool(version="2025-12-01")
        def report() -> str:
            return "dec"

        # Q1 API: before April
        mcp.add_transform(VersionFilter(version_lt="2025-04-01"))

        tools = await mcp.list_tools()
        assert len(tools) == 1
        assert tools[0].version == "2025-01-01"

    async def test_get_tool_respects_filter(self):
        """get_tool() returns None if highest version is filtered out."""

        from fastmcp.server.transforms import VersionFilter

        mcp = FastMCP()

        @mcp.tool(version="5.0")
        def only_v5() -> str:
            return "v5"

        mcp.add_transform(VersionFilter(version_lt="3.0"))

        # Tool exists but is filtered out - returns None (use get_tool to apply transforms)
        assert await mcp.get_tool("only_v5") is None

    async def test_must_specify_at_least_one(self):
        """VersionFilter() with no args raises ValueError."""
        import pytest

        from fastmcp.server.transforms import VersionFilter

        with pytest.raises(ValueError, match="At least one of"):
            VersionFilter()

    async def test_resources_filtered(self):
        """Resources are filtered by version."""
        from fastmcp.server.transforms import VersionFilter

        mcp = FastMCP()

        @mcp.resource("file:///config", version="1.0")
        def config_v1() -> str:
            return "v1"

        @mcp.resource("file:///config", version="2.0")
        def config_v2() -> str:
            return "v2"

        mcp.add_transform(VersionFilter(version_lt="2.0"))

        resources = await mcp.list_resources()
        assert len(resources) == 1
        assert resources[0].version == "1.0"

    async def test_prompts_filtered(self):
        """Prompts are filtered by version."""
        from fastmcp.server.transforms import VersionFilter

        mcp = FastMCP()

        @mcp.prompt(version="1.0")
        def greet(name: str) -> str:
            return f"Hi {name}"

        @mcp.prompt(version="2.0")
        def greet(name: str) -> str:
            return f"Hello {name}"

        mcp.add_transform(VersionFilter(version_lt="2.0"))

        prompts = await mcp.list_prompts()
        assert len(prompts) == 1
        assert prompts[0].version == "1.0"

    async def test_repr(self):
        """Test VersionFilter string representation."""
        from fastmcp.server.transforms import VersionFilter

        f1 = VersionFilter(version_lt="3.0")
        assert repr(f1) == "VersionFilter(version_lt='3.0')"

        f2 = VersionFilter(version_gte="2.0", version_lt="3.0")
        assert repr(f2) == "VersionFilter(version_gte='2.0', version_lt='3.0')"

        f3 = VersionFilter(version_gte="1.0")
        assert repr(f3) == "VersionFilter(version_gte='1.0')"


class TestVersionMixingValidation:
    """Tests for versioned/unversioned mixing prevention."""

    async def test_resource_mixing_rejected(self):
        """Cannot mix versioned and unversioned resources with the same URI."""
        import pytest

        mcp = FastMCP()

        @mcp.resource("file:///config", version="1.0")
        def config_v1() -> str:
            return "v1"

        with pytest.raises(ValueError, match="unversioned.*versioned"):

            @mcp.resource("file:///config")
            def config_unversioned() -> str:
                return "unversioned"

    async def test_prompt_mixing_rejected(self):
        """Cannot mix versioned and unversioned prompts with the same name."""
        import pytest

        mcp = FastMCP()

        @mcp.prompt
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        with pytest.raises(ValueError, match="versioned.*unversioned"):

            @mcp.prompt(version="1.0")
            def greet(name: str) -> str:
                return f"Hi, {name}!"

    async def test_multiple_versions_allowed(self):
        """Multiple versioned components with same name are allowed."""
        mcp = FastMCP()

        @mcp.tool(version="1.0")
        def calc() -> int:
            return 1

        @mcp.tool(version="2.0")
        def calc() -> int:
            return 2

        @mcp.tool(version="3.0")
        def calc() -> int:
            return 3

        # All versioned - list_tools returns all
        tools = await mcp.list_tools()
        assert len(tools) == 3
        versions = {t.version for t in tools}
        assert versions == {"1.0", "2.0", "3.0"}

        # get_tool returns highest
        tool = await mcp.get_tool("calc")
        assert tool is not None
        assert tool.version == "3.0"


class TestMountedVersionFiltering:
    """Tests for version filtering with mounted servers (FastMCPProvider).

    Note: For mounted servers, list_* methods show what the child exposes (already
    deduplicated to highest version). get_* methods support range filtering via
    VersionSpec propagation to FastMCPProvider.
    """

    async def test_mounted_get_tool_with_range_filter(self):
        """FastMCPProvider.get_tool applies range filtering from VersionSpec."""
        from fastmcp.server.providers.fastmcp_provider import FastMCPProvider
        from fastmcp.utilities.versions import VersionSpec

        child = FastMCP("Child")

        @child.tool(version="2.0")
        def calc() -> int:
            return 2

        provider = FastMCPProvider(child)

        # Without range spec, should return the tool
        tool = await provider.get_tool("calc")
        assert tool is not None
        assert tool.version == "2.0"

        # With range spec that excludes v2.0, should return None
        tool = await provider.get_tool("calc", version=VersionSpec(lt="2.0"))
        assert tool is None

        # With range spec that includes v2.0, should return the tool
        tool = await provider.get_tool("calc", version=VersionSpec(gte="2.0"))
        assert tool is not None
        assert tool.version == "2.0"

    async def test_mounted_get_resource_with_range_filter(self):
        """FastMCPProvider.get_resource applies range filtering from VersionSpec."""
        from fastmcp.server.providers.fastmcp_provider import FastMCPProvider
        from fastmcp.utilities.versions import VersionSpec

        child = FastMCP("Child")

        @child.resource("file://data/", version="2.0")
        def data() -> str:
            return "data"

        provider = FastMCPProvider(child)

        # Without range spec, should return the resource
        resource = await provider.get_resource("file://data/")
        assert resource is not None
        assert resource.version == "2.0"

        # With range spec that excludes v2.0, should return None
        resource = await provider.get_resource(
            "file://data/", version=VersionSpec(lt="2.0")
        )
        assert resource is None

    async def test_mounted_get_prompt_with_range_filter(self):
        """FastMCPProvider.get_prompt applies range filtering from VersionSpec."""
        from fastmcp.server.providers.fastmcp_provider import FastMCPProvider
        from fastmcp.utilities.versions import VersionSpec

        child = FastMCP("Child")

        @child.prompt(version="2.0")
        def greet(name: str) -> str:
            return f"Hello {name}"

        provider = FastMCPProvider(child)

        # Without range spec, should return the prompt
        prompt = await provider.get_prompt("greet")
        assert prompt is not None
        assert prompt.version == "2.0"

        # With range spec that excludes v2.0, should return None
        prompt = await provider.get_prompt("greet", version=VersionSpec(lt="2.0"))
        assert prompt is None

    async def test_mounted_unversioned_passes_version_filter(self):
        """Unversioned components in mounted servers pass through version filters."""
        from fastmcp.server.transforms import VersionFilter

        child = FastMCP("Child")

        @child.tool
        def unversioned_tool() -> str:
            return "unversioned"

        parent = FastMCP("Parent")
        parent.mount(child, "child")
        parent.add_transform(VersionFilter(version_lt="3.0"))

        # Unversioned should pass through
        tools = await parent.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "child_unversioned_tool"
        assert tools[0].version is None

    async def test_version_filter_filters_out_high_mounted_version(self):
        """VersionFilter hides mounted components outside the range."""
        from fastmcp.server.transforms import VersionFilter

        child = FastMCP("Child")

        @child.tool(version="5.0")
        def high_version_tool() -> int:
            return 5

        parent = FastMCP("Parent")
        parent.mount(child, "child")
        parent.add_transform(VersionFilter(version_lt="3.0"))

        # v5.0 is outside the filter range, so it should be hidden
        tools = await parent.list_tools()
        assert len(tools) == 0

        # get_tool should also return None (respects filter, applies transforms)
        assert await parent.get_tool("child_high_version_tool") is None


class TestMountedRangeFiltering:
    """Tests for version range filtering with mounted servers."""

    async def test_mounted_lower_version_selected_by_filter(self):
        """When parent has filter <2.0 and child has v1.0+v3.0, should get v1.0."""
        from fastmcp.server.transforms import VersionFilter

        child = FastMCP("Child")

        @child.tool(version="1.0")
        def calc() -> int:
            return 1

        @child.tool(version="3.0")
        def calc() -> int:
            return 3

        parent = FastMCP("Parent")
        parent.mount(child, "child")
        parent.add_transform(VersionFilter(version_lt="2.0"))

        # Should return v1.0 (the highest version that matches <2.0)
        # Use get_tool to apply transforms
        tool = await parent.get_tool("child_calc")
        assert tool is not None
        assert tool.version == "1.0"

    async def test_explicit_version_honored_within_filter_range(self):
        """Explicit version="1.0" request should work within filter range."""
        from fastmcp.server.transforms import VersionFilter

        child = FastMCP("Child")

        @child.tool(version="1.0")
        def calc() -> int:
            return 1

        @child.tool(version="2.0")
        def calc() -> int:
            return 2

        @child.tool(version="3.0")
        def calc() -> int:
            return 3

        parent = FastMCP("Parent")
        parent.mount(child, "child")
        parent.add_transform(VersionFilter(version_gte="1.0", version_lt="3.0"))

        # Request specific version within range (use get_tool to apply transforms)
        tool = await parent.get_tool("child_calc", VersionSpec(eq="1.0"))
        assert tool is not None
        assert tool.version == "1.0"

        # Request version outside range should return None
        result = await parent.get_tool("child_calc", VersionSpec(eq="3.0"))
        assert result is None


class TestUnversionedExemption:
    """Tests confirming unversioned components bypass version filters."""

    async def test_unversioned_bypasses_version_filter(self):
        """Unversioned components pass through any VersionFilter - by design."""
        from fastmcp.server.transforms import VersionFilter

        mcp = FastMCP()

        @mcp.tool
        def unversioned_tool() -> str:
            return "unversioned"

        @mcp.tool(version="5.0")
        def versioned_tool() -> str:
            return "v5"

        # Filter that would exclude v5.0
        mcp.add_transform(VersionFilter(version_lt="3.0"))

        tools = await mcp.list_tools()
        names = [t.name for t in tools]

        # Unversioned passes through (exempt from filtering)
        assert "unversioned_tool" in names
        # Versioned is filtered out
        assert "versioned_tool" not in names

    async def test_unversioned_returned_for_exact_version_request(self):
        """Requesting exact version of unversioned tool returns the tool."""
        mcp = FastMCP()

        @mcp.tool
        def my_tool() -> str:
            return "unversioned"

        # Even with explicit version request, unversioned tool is returned
        # (it's the only version that exists, and unversioned matches any spec)
        tool = await mcp.get_tool("my_tool", VersionSpec(eq="1.0"))
        assert tool is not None
        assert tool.version is None

    async def test_unversioned_matches_any_version_spec(self):
        """VersionSpec.matches(None) returns True for any spec."""
        from fastmcp.utilities.versions import VersionSpec

        # Unversioned matches exact version specs
        assert VersionSpec(eq="1.0").matches(None) is True

        # Unversioned matches range specs
        assert VersionSpec(gte="1.0", lt="3.0").matches(None) is True

        # Unversioned matches open specs
        assert VersionSpec(lt="5.0").matches(None) is True
        assert VersionSpec(gte="1.0").matches(None) is True


class TestVersionValidation:
    """Tests for version string validation."""

    async def test_version_with_at_symbol_rejected(self):
        """Version strings containing '@' should be rejected."""
        import pytest
        from pydantic import ValidationError

        mcp = FastMCP()

        with pytest.raises(ValidationError, match="cannot contain '@'"):

            @mcp.tool(version="1.0@beta")
            def my_tool() -> str:
                return "test"


class TestVersionMetadata:
    """Tests for version metadata exposure in list operations."""

    async def test_tool_versions_in_meta(self):
        """Each version has its own version in metadata."""
        mcp = FastMCP()

        @mcp.tool(version="1.0")
        def add(x: int, y: int) -> int:  # noqa: F811
            return x + y

        @mcp.tool(version="2.0")
        def add(x: int, y: int) -> int:  # noqa: F811
            return x + y

        # list_tools returns all versions
        tools = await mcp.list_tools()
        assert len(tools) == 2

        # Each version has its own version in metadata
        by_version = {t.version: t for t in tools}
        assert by_version["1.0"].get_meta()["fastmcp"]["version"] == "1.0"
        assert by_version["2.0"].get_meta()["fastmcp"]["version"] == "2.0"

    async def test_resource_versions_in_meta(self):
        """Each version has its own version in metadata."""
        mcp = FastMCP()

        @mcp.resource("data://config", version="1.0")
        def config_v1() -> str:  # noqa: F811
            return "v1"

        @mcp.resource("data://config", version="2.0")
        def config_v2() -> str:  # noqa: F811
            return "v2"

        # list_resources returns all versions
        resources = await mcp.list_resources()
        assert len(resources) == 2

        # Each version has its own version in metadata
        by_version = {r.version: r for r in resources}
        assert by_version["1.0"].get_meta()["fastmcp"]["version"] == "1.0"
        assert by_version["2.0"].get_meta()["fastmcp"]["version"] == "2.0"

    async def test_prompt_versions_in_meta(self):
        """Each version has its own version in metadata."""
        mcp = FastMCP()

        @mcp.prompt(version="1.0")
        def greet() -> str:  # noqa: F811
            return "Hello v1"

        @mcp.prompt(version="2.0")
        def greet() -> str:  # noqa: F811
            return "Hello v2"

        # list_prompts returns all versions
        prompts = await mcp.list_prompts()
        assert len(prompts) == 2

        # Each version has its own version in metadata
        by_version = {p.version: p for p in prompts}
        assert by_version["1.0"].get_meta()["fastmcp"]["version"] == "1.0"
        assert by_version["2.0"].get_meta()["fastmcp"]["version"] == "2.0"

    async def test_unversioned_no_versions_list(self):
        """Unversioned components should not have versions list in meta."""
        mcp = FastMCP()

        @mcp.tool
        def simple() -> str:
            return "simple"

        tools = await mcp.list_tools()
        assert len(tools) == 1

        tool = tools[0]
        meta = tool.get_meta()
        assert "versions" not in meta.get("fastmcp", {})


class TestVersionedCalls:
    """Tests for calling specific component versions."""

    async def test_call_tool_with_version(self):
        """call_tool should use specified version."""
        mcp = FastMCP()

        @mcp.tool(version="1.0")
        def calculate(x: int, y: int) -> int:  # noqa: F811
            return x + y

        @mcp.tool(version="2.0")
        def calculate(x: int, y: int) -> int:  # noqa: F811
            return x * y

        # Default: highest version (2.0, multiplication)
        result = await mcp.call_tool("calculate", {"x": 3, "y": 4})
        assert result.structured_content is not None
        assert result.structured_content["result"] == 12

        # Explicit v1.0 (addition)
        result = await mcp.call_tool(
            "calculate", {"x": 3, "y": 4}, version=VersionSpec(eq="1.0")
        )
        assert result.structured_content is not None
        assert result.structured_content["result"] == 7

        # Explicit v2.0 (multiplication)
        result = await mcp.call_tool(
            "calculate", {"x": 3, "y": 4}, version=VersionSpec(eq="2.0")
        )
        assert result.structured_content is not None
        assert result.structured_content["result"] == 12

    async def test_read_resource_with_version(self):
        """read_resource should use specified version."""
        mcp = FastMCP()

        @mcp.resource("data://config", version="1.0")
        def config() -> str:  # noqa: F811
            return "config v1"

        @mcp.resource("data://config", version="2.0")
        def config() -> str:  # noqa: F811
            return "config v2"

        # Default: highest version
        result = await mcp.read_resource("data://config")
        assert result.contents[0].content == "config v2"

        # Explicit v1.0
        result = await mcp.read_resource("data://config", version=VersionSpec(eq="1.0"))
        assert result.contents[0].content == "config v1"

    async def test_render_prompt_with_version(self):
        """render_prompt should use specified version."""
        mcp = FastMCP()

        @mcp.prompt(version="1.0")
        def greet() -> str:  # noqa: F811
            return "Hello from v1"

        @mcp.prompt(version="2.0")
        def greet() -> str:  # noqa: F811
            return "Hello from v2"

        # Default: highest version
        result = await mcp.render_prompt("greet")
        content = result.messages[0].content
        assert isinstance(content, TextContent) and content.text == "Hello from v2"

        # Explicit v1.0
        result = await mcp.render_prompt("greet", version=VersionSpec(eq="1.0"))
        content = result.messages[0].content
        assert isinstance(content, TextContent) and content.text == "Hello from v1"

    async def test_call_tool_invalid_version_not_found(self):
        """Calling with non-existent version should raise NotFoundError."""
        import pytest

        from fastmcp.exceptions import NotFoundError

        mcp = FastMCP()

        @mcp.tool(version="1.0")
        def mytool() -> str:
            return "v1"

        with pytest.raises(NotFoundError):
            await mcp.call_tool("mytool", {}, version=VersionSpec(eq="999.0"))


class TestClientVersionSelection:
    """Tests for client-side version selection via the version parameter.

    Version selection flows through request-level _meta, not arguments.
    """

    import pytest

    @pytest.mark.parametrize(
        "version,expected",
        [
            (None, 10),  # Default: highest version (2.0) -> 5 * 2
            ("1.0", 6),  # v1.0 -> 5 + 1
            ("2.0", 10),  # v2.0 -> 5 * 2
        ],
    )
    async def test_call_tool_version_selection(
        self, version: str | None, expected: int
    ):
        """Client.call_tool routes to correct version via request meta."""
        from fastmcp import Client

        mcp = FastMCP()

        @mcp.tool(version="1.0")
        def calc(x: int) -> int:  # noqa: F811
            return x + 1

        @mcp.tool(version="2.0")
        def calc(x: int) -> int:  # noqa: F811
            return x * 2

        async with Client(mcp) as client:
            result = await client.call_tool("calc", {"x": 5}, version=version)
            assert result.data == expected

    @pytest.mark.parametrize(
        "version,expected",
        [
            (None, "Hello world from v2"),  # Default: highest version
            ("1.0", "Hello world from v1"),
            ("2.0", "Hello world from v2"),
        ],
    )
    async def test_get_prompt_version_selection(
        self, version: str | None, expected: str
    ):
        """Client.get_prompt routes to correct version via request meta."""
        from fastmcp import Client

        mcp = FastMCP()

        @mcp.prompt(version="1.0")
        def greet(name: str) -> str:  # noqa: F811
            return f"Hello {name} from v1"

        @mcp.prompt(version="2.0")
        def greet(name: str) -> str:  # noqa: F811
            return f"Hello {name} from v2"

        async with Client(mcp) as client:
            result = await client.get_prompt(
                "greet", {"name": "world"}, version=version
            )
            content = result.messages[0].content
            assert isinstance(content, TextContent) and content.text == expected

    @pytest.mark.parametrize(
        "version,expected",
        [
            (None, "v2 data"),  # Default: highest version
            ("1.0", "v1 data"),
            ("2.0", "v2 data"),
        ],
    )
    async def test_read_resource_version_selection(
        self, version: str | None, expected: str
    ):
        """Client.read_resource routes to correct version via request meta."""
        from fastmcp import Client

        mcp = FastMCP()

        @mcp.resource("data://info", version="1.0")
        def info_v1() -> str:  # noqa: F811
            return "v1 data"

        @mcp.resource("data://info", version="2.0")
        def info_v2() -> str:  # noqa: F811
            return "v2 data"

        async with Client(mcp) as client:
            result = await client.read_resource("data://info", version=version)
            assert result[0].text == expected  # type: ignore[union-attr]
