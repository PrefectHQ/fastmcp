"""Tests for version filtering functionality."""
# ruff: noqa: F811  # Intentional function redefinition for version testing

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.utilities.versions import (
    VersionSpec,
)


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
