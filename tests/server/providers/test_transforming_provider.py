"""Tests for Namespace and ToolTransform."""

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.providers import FastMCPProvider
from fastmcp.server.transforms import Namespace, ToolTransform
from fastmcp.server.transforms.version_filter import VersionFilter
from fastmcp.tools.tool_transform import ToolTransformConfig
from fastmcp.utilities.versions import VersionSpec


class TestToolTransformVersions:
    async def test_reversioned_tool_with_all_sources_disabled_is_unavailable(
        self,
    ) -> None:
        server = FastMCP()

        @server.tool(name="calc", version="1.0")
        def first() -> int:
            return 1

        @server.tool(name="calc", version="3.0")
        def latest() -> int:
            return 3

        server.disable(names={"calc"})
        server.add_transform(
            ToolTransform({"calc": ToolTransformConfig(version="2.0")})
        )
        assert await server.get_tool("calc", version=VersionSpec(eq="2.0")) is None

    async def test_explicit_range_does_not_fall_back_to_another_public_version(
        self,
    ) -> None:
        server = FastMCP()

        @server.tool(name="calc", version="1.0")
        def first() -> int:
            return 1

        @server.tool(name="calc", version="3.0")
        def latest() -> int:
            return 3

        server.disable(version=VersionSpec(eq="3.0"))
        tool = await server.get_tool("calc", version=VersionSpec(gte="1.0"))
        assert tool is None

    @pytest.mark.parametrize("requested_version", [None, "2.0"])
    @pytest.mark.parametrize("enable_latest", [False, True])
    async def test_reversioned_tool_selects_enabled_source(
        self, requested_version: str | None, enable_latest: bool
    ) -> None:
        server = FastMCP()
        calls: list[str] = []

        @server.tool(name="calc", version="1.0")
        def first() -> int:
            calls.append("first")
            return 1

        @server.tool(name="calc", version="3.0")
        def latest() -> int:
            calls.append("latest")
            return 3

        server.disable(version=VersionSpec(eq="3.0"))
        server.add_transform(
            ToolTransform({"calc": ToolTransformConfig(version="2.0")})
        )
        if enable_latest:
            server.enable(names={"calc"})

        async with Client(server) as client:
            tools = await client.list_tools()
            assert len(tools) == 1
            assert tools[0].meta is not None
            assert tools[0].meta["fastmcp"]["version"] == "2.0"
            result = await client.call_tool("calc", {}, version=requested_version)
        assert result.data == (3 if enable_latest else 1)
        assert calls == (["latest"] if enable_latest else ["first"])

    @pytest.mark.parametrize("placement", ["server", "provider", "child"])
    @pytest.mark.parametrize("renamed", [False, True])
    async def test_reversioned_tool_is_callable(
        self, placement: str, renamed: bool
    ) -> None:
        source = FastMCP("Source")
        calls: list[int] = []

        @source.tool(version="1.0")
        def calc(x: int) -> int:
            calls.append(x)
            return x + 1

        name = "renamed" if renamed else "calc"
        transform = ToolTransform(
            {"calc": ToolTransformConfig(name=name, version="2.0")}
        )
        if placement == "server":
            server = source
            server.add_transform(transform)
        else:
            server = FastMCP("Parent")
            provider = FastMCPProvider(source)
            if placement == "provider":
                provider.add_transform(transform)
            else:
                source.add_transform(transform)
            server.add_provider(provider)

        async with Client(server) as client:
            tools = await client.list_tools()
            assert len(tools) == 1
            assert tools[0].name == name
            assert tools[0].meta is not None
            assert tools[0].meta["fastmcp"]["version"] == "2.0"
            result = await client.call_tool(name, {"x": 4}, version="2.0")
            assert result.data == 5
            assert type(result.data) is int
            with pytest.raises(ToolError, match="Unknown tool"):
                await client.call_tool(name, {"x": 8}, version="1.0")
            if renamed:
                with pytest.raises(ToolError, match="Unknown tool"):
                    await client.call_tool("calc", {"x": 8}, version="2.0")
            assert calls == [4]
            result = await client.call_tool(name, {"x": 9})
            assert result.data == 10
        assert calls == [4, 9]

    @pytest.mark.parametrize(
        ("spec", "found"),
        [
            (VersionSpec(eq="2.0"), True),
            (VersionSpec(eq="v2"), True),
            (VersionSpec(gte="2", lt="3"), True),
            (VersionSpec(eq="1.0"), False),
            (VersionSpec(gte="3"), False),
            (VersionSpec(lt="2"), False),
        ],
    )
    async def test_version_spec_matches_transformed_version(
        self, spec: VersionSpec, found: bool
    ) -> None:
        server = FastMCP()

        @server.tool(version="1.0")
        def calc() -> int:
            return 1

        server.add_transform(
            ToolTransform({"calc": ToolTransformConfig(version="2.0")})
        )
        tool = await server.get_tool("calc", version=spec)
        assert (tool is not None) is found
        if tool is not None:
            assert tool.version == "2.0"

    async def test_omitted_version_preserves_source_selection(self) -> None:
        server = FastMCP()

        @server.tool(name="calc", version="1.0")
        def first() -> int:
            return 1

        @server.tool(name="calc", version="3.0")
        def latest() -> int:
            return 3

        server.add_transform(
            ToolTransform({"calc": ToolTransformConfig(name="renamed")})
        )
        async with Client(server) as client:
            first_result = await client.call_tool("renamed", {}, version="1.0")
            latest_result = await client.call_tool("renamed", {})
        assert first_result.data == 1
        assert latest_result.data == 3

    async def test_explicit_none_clears_version(self) -> None:
        server = FastMCP()

        @server.tool(version="1.0")
        def calc() -> int:
            return 1

        server.add_transform(ToolTransform({"calc": ToolTransformConfig(version=None)}))
        tool = await server.get_tool("calc", version=VersionSpec(eq="2.0"))
        assert tool is not None
        assert tool.version is None
        async with Client(server) as client:
            result = await client.call_tool("calc", {}, version="2.0")
        assert result.data == 1

    @pytest.mark.parametrize("filter_first", [False, True])
    async def test_reversioning_composes_with_version_filter(
        self, filter_first: bool
    ) -> None:
        server = FastMCP()

        @server.tool(version="1.0")
        def calc() -> int:
            return 1

        transform = ToolTransform({"calc": ToolTransformConfig(version="2.0")})
        version_filter = (
            VersionFilter(version_lt="2")
            if filter_first
            else VersionFilter(version_gte="2")
        )
        for layer in (
            [version_filter, transform] if filter_first else [transform, version_filter]
        ):
            server.add_transform(layer)

        async with Client(server) as client:
            tools = await client.list_tools()
            assert len(tools) == 1
            result = await client.call_tool("calc", {}, version="2.0")
        assert result.data == 1


class TestNamespaceTransform:
    """Test Namespace transform transformations."""

    async def test_namespace_prefixes_tool_names(self):
        """Test that namespace is applied as prefix to tool names."""
        server = FastMCP("Test")

        @server.tool
        def my_tool() -> str:
            return "result"

        provider = FastMCPProvider(server)
        layer = Namespace("ns")

        # Get tools and pass directly to transform
        tools = await provider.list_tools()
        transformed_tools = await layer.list_tools(tools)

        assert len(transformed_tools) == 1
        assert transformed_tools[0].name == "ns_my_tool"

    async def test_namespace_prefixes_prompt_names(self):
        """Test that namespace is applied as prefix to prompt names."""
        server = FastMCP("Test")

        @server.prompt
        def my_prompt() -> str:
            return "prompt content"

        provider = FastMCPProvider(server)
        layer = Namespace("ns")

        prompts = await provider.list_prompts()
        transformed_prompts = await layer.list_prompts(prompts)

        assert len(transformed_prompts) == 1
        assert transformed_prompts[0].name == "ns_my_prompt"

    async def test_namespace_prefixes_resource_uris(self):
        """Test that namespace is inserted into resource URIs."""
        server = FastMCP("Test")

        @server.resource("resource://data")
        def my_resource() -> str:
            return "content"

        provider = FastMCPProvider(server)
        layer = Namespace("ns")

        resources = await provider.list_resources()
        transformed_resources = await layer.list_resources(resources)

        assert len(transformed_resources) == 1
        assert str(transformed_resources[0].uri) == "resource://ns/data"

    async def test_namespace_prefixes_template_uris(self):
        """Test that namespace is inserted into resource template URIs."""
        server = FastMCP("Test")

        @server.resource("resource://{name}/data")
        def my_template(name: str) -> str:
            return f"content for {name}"

        provider = FastMCPProvider(server)
        layer = Namespace("ns")

        templates = await provider.list_resource_templates()
        transformed_templates = await layer.list_resource_templates(templates)

        assert len(transformed_templates) == 1
        assert transformed_templates[0].uri_template == "resource://ns/{name}/data"


class TestToolTransformRenames:
    """Test ToolTransform renaming functionality."""

    async def test_tool_rename(self):
        """Test tool renaming with ToolTransform."""
        server = FastMCP("Test")

        @server.tool
        def verbose_tool_name() -> str:
            return "result"

        provider = FastMCPProvider(server)
        layer = ToolTransform({"verbose_tool_name": ToolTransformConfig(name="short")})

        tools = await provider.list_tools()
        transformed_tools = await layer.list_tools(tools)

        assert len(transformed_tools) == 1
        assert transformed_tools[0].name == "short"

    async def test_renamed_tool_is_callable_via_mount(self):
        """Test that renamed tools can be called by new name via mount."""
        sub = FastMCP("Sub")

        @sub.tool
        def original() -> str:
            return "success"

        main = FastMCP("Main")
        # Add provider with transform layer
        provider = FastMCPProvider(sub)
        provider.add_transform(
            ToolTransform({"original": ToolTransformConfig(name="renamed")})
        )
        main.add_provider(provider)

        async with Client(main) as client:
            result = await client.call_tool("renamed", {})
            assert result.data == "success"

    def test_duplicate_rename_targets_raises_error(self):
        """Test that duplicate target names in ToolTransform raises ValueError."""
        with pytest.raises(ValueError, match="duplicate target name"):
            ToolTransform(
                {
                    "tool_a": ToolTransformConfig(name="same"),
                    "tool_b": ToolTransformConfig(name="same"),
                }
            )


class TestTransformReverseLookup:
    """Test reverse lookups for routing."""

    async def test_namespace_get_tool(self):
        """Test that tools can be looked up by transformed name."""
        server = FastMCP("Test")

        @server.tool
        def my_tool() -> str:
            return "result"

        provider = FastMCPProvider(server)
        layer = Namespace("ns")

        # Create call_next that delegates to provider
        async def get_tool(name: str, version=None):
            return await provider._get_tool(name, version)

        tool = await layer.get_tool("ns_my_tool", get_tool)

        assert tool is not None
        assert tool.name == "ns_my_tool"

    async def test_transform_layer_get_tool(self):
        """Test that renamed tools can be looked up by new name."""
        server = FastMCP("Test")

        @server.tool
        def original() -> str:
            return "result"

        provider = FastMCPProvider(server)
        layer = ToolTransform({"original": ToolTransformConfig(name="renamed")})

        async def get_tool(name: str, version=None):
            return await provider._get_tool(name, version)

        tool = await layer.get_tool("renamed", get_tool)

        assert tool is not None
        assert tool.name == "renamed"

    async def test_namespace_get_resource(self):
        """Test that resources can be looked up by transformed URI."""
        server = FastMCP("Test")

        @server.resource("resource://data")
        def my_resource() -> str:
            return "content"

        provider = FastMCPProvider(server)
        layer = Namespace("ns")

        async def get_resource(uri: str, version=None):
            return await provider._get_resource(uri, version)

        resource = await layer.get_resource("resource://ns/data", get_resource)

        assert resource is not None
        assert str(resource.uri) == "resource://ns/data"

    async def test_namespace_get_resource_with_hostless_uri(self):
        """A URI with no protocol://path is listed unprefixed, so it must resolve as-is."""
        server = FastMCP("Test")

        @server.resource("urn:example:config")
        def my_resource() -> str:
            return "content"

        provider = FastMCPProvider(server)
        layer = Namespace("ns")

        resources = await layer.list_resources(await provider.list_resources())
        assert str(resources[0].uri) == "urn:example:config"

        async def get_resource(uri: str, version=None):
            return await provider._get_resource(uri, version)

        resource = await layer.get_resource("urn:example:config", get_resource)

        assert resource is not None
        assert str(resource.uri) == "urn:example:config"

    async def test_namespace_get_resource_template_with_hostless_uri(self):
        """Same for templates, which share the URI reversal."""
        server = FastMCP("Test")

        @server.resource("urn:example:item/{item_id}")
        def my_template(item_id: str) -> str:
            return f"item {item_id}"

        provider = FastMCPProvider(server)
        layer = Namespace("ns")

        templates = await layer.list_resource_templates(
            await provider.list_resource_templates()
        )
        assert templates[0].uri_template == "urn:example:item/{item_id}"

        async def get_template(uri: str, version=None):
            return await provider._get_resource_template(uri, version)

        template = await layer.get_resource_template(
            "urn:example:item/{item_id}", get_template
        )

        assert template is not None
        assert template.uri_template == "urn:example:item/{item_id}"

    async def test_nonmatching_namespace_returns_none(self):
        """Test that lookups with wrong namespace return None."""
        server = FastMCP("Test")

        @server.tool
        def my_tool() -> str:
            return "result"

        provider = FastMCPProvider(server)
        layer = Namespace("ns")

        async def get_tool(name: str, version=None):
            return await provider._get_tool(name, version)

        # Wrong namespace prefix
        assert await layer.get_tool("wrong_my_tool", get_tool) is None
        # No prefix at all
        assert await layer.get_tool("my_tool", get_tool) is None


class TestTransformStacking:
    """Test stacking multiple transforms via provider add_transform."""

    async def test_stacked_namespaces_compose(self):
        """Test that stacked namespaces are applied in order."""
        server = FastMCP("Test")

        @server.tool
        def my_tool() -> str:
            return "result"

        provider = FastMCPProvider(server)
        inner_layer = Namespace("inner")
        outer_layer = Namespace("outer")

        # Apply transforms sequentially: base -> inner -> outer
        tools = await provider.list_tools()
        tools = await inner_layer.list_tools(tools)
        tools = await outer_layer.list_tools(tools)

        assert len(tools) == 1
        assert tools[0].name == "outer_inner_my_tool"

    async def test_stacked_transforms_are_callable(self):
        """Test that stacked transforms still allow tool calls."""
        sub = FastMCP("Sub")

        @sub.tool
        def my_tool() -> str:
            return "success"

        main = FastMCP("Main")
        provider = FastMCPProvider(sub)
        # Add namespace layer then rename layer
        provider.add_transform(Namespace("ns"))
        provider.add_transform(
            ToolTransform({"ns_my_tool": ToolTransformConfig(name="short")})
        )
        main.add_provider(provider)

        async with Client(main) as client:
            result = await client.call_tool("short", {})
            assert result.data == "success"


class TestNoTransformation:
    """Test behavior when no transformations are applied."""

    async def test_transform_passthrough(self):
        """Test that base Transform passes through unchanged."""
        from fastmcp.server.transforms import Transform

        server = FastMCP("Test")

        @server.tool
        def my_tool() -> str:
            return "result"

        provider = FastMCPProvider(server)
        transform = Transform()

        tools = await provider.list_tools()
        transformed_tools = await transform.list_tools(tools)

        assert len(transformed_tools) == 1
        assert transformed_tools[0].name == "my_tool"

    async def test_empty_transform_layer_passthrough(self):
        """Test that empty ToolTransform has no effect."""
        server = FastMCP("Test")

        @server.tool
        def my_tool() -> str:
            return "result"

        provider = FastMCPProvider(server)
        layer = ToolTransform({})

        tools = await provider.list_tools()
        transformed_tools = await layer.list_tools(tools)

        assert len(transformed_tools) == 1
        assert transformed_tools[0].name == "my_tool"
