"""Tests for versioned calls and client version selection."""
# ruff: noqa: F811  # Intentional function redefinition for version testing

from __future__ import annotations

from mcp.types import TextContent

from fastmcp import FastMCP
from fastmcp.utilities.versions import (
    VersionSpec,
)


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
            assert result[0].text == expected
