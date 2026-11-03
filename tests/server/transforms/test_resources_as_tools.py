"""Tests for ResourcesAsTools transform."""

import base64
import json

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AuthContext
from fastmcp.server.context import Context
from fastmcp.server.transforms import ResourcesAsTools, VersionFilter
from fastmcp.server.transforms.search import RegexSearchTransform


class TestResourcesAsToolsBasic:
    """Test basic ResourcesAsTools functionality."""

    async def test_adds_list_resources_tool(self):
        """Transform adds list_resources tool."""
        mcp = FastMCP("Test")
        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = {t.name for t in tools}
            assert "list_resources" in tool_names

    async def test_adds_read_resource_tool(self):
        """Transform adds read_resource tool."""
        mcp = FastMCP("Test")
        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = {t.name for t in tools}
            assert "read_resource" in tool_names

    async def test_preserves_existing_tools(self):
        """Transform preserves existing tools."""
        mcp = FastMCP("Test")

        @mcp.tool
        def my_tool() -> str:
            return "result"

        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = {t.name for t in tools}
            assert "my_tool" in tool_names
            assert "list_resources" in tool_names
            assert "read_resource" in tool_names

    async def test_does_not_override_user_defined_tools(self):
        """User-defined tools named list_resources or read_resource take precedence."""
        mcp = FastMCP("Test")

        @mcp.tool
        def list_resources() -> str:
            return "user list_resources"

        @mcp.tool
        def read_resource(uri: str) -> str:
            return f"user read_resource: {uri}"

        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert tool_names.count("list_resources") == 1
            assert tool_names.count("read_resource") == 1

            result1 = await client.call_tool("list_resources", {})
            assert result1.data == "user list_resources"

            result2 = await client.call_tool("read_resource", {"uri": "test://uri"})
            assert result2.data == "user read_resource: test://uri"

    async def test_versioned_user_tool_with_version_filter(self):
        """Synthetic tools remain discoverable when versioned user tool is filtered out."""
        mcp = FastMCP("Test")

        @mcp.tool(version="2.0")
        def read_resource(uri: str) -> str:
            return f"user v2: {uri}"

        @mcp.resource("config://app")
        def app_config() -> str:
            return "config data"

        mcp.add_transform(ResourcesAsTools(mcp))
        mcp.add_transform(VersionFilter(version_lt="2.0"))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "read_resource" in tool_names

            result = await client.call_tool("read_resource", {"uri": "config://app"})
            assert result.data == "config data"

    async def test_regex_search_transform_preceding_resources_as_tools(self):
        """When RegexSearchTransform precedes ResourcesAsTools, synthetic tool is advertised and called."""
        mcp = FastMCP("Test")

        @mcp.tool
        def read_resource(query: str) -> str:
            return f"user: {query}"

        @mcp.resource("config://app")
        def app_config() -> str:
            return "config data"

        mcp.add_transform(RegexSearchTransform())
        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "read_resource" in tool_names
            assert "list_resources" in tool_names

            # Calling read_resource directly calls the advertised synthetic tool
            result = await client.call_tool("read_resource", {"uri": "config://app"})
            assert result.data == "config data"

    async def test_provider_scoped_search_transform_hiding(self):
        """When child provider has RegexSearchTransform, parent ResourcesAsTools advertises and calls synthetic tool."""
        from fastmcp.server.providers import LocalProvider

        mcp = FastMCP("Test")
        child = LocalProvider()

        @child.tool
        def read_resource(query: str) -> str:
            return f"child: {query}"

        child.add_transform(RegexSearchTransform())
        mcp.add_provider(child)

        @mcp.resource("config://app")
        def app_config() -> str:
            return "config data"

        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "read_resource" in tool_names
            assert "list_resources" in tool_names

            result = await client.call_tool("read_resource", {"uri": "config://app"})
            assert result.data == "config data"

    async def test_auth_denied_user_tool_falls_back_to_synthetic_tool(self):
        """When user tool is denied by auth, synthetic tool is advertised and callable."""
        from fastmcp.server.context import _current_transport

        mcp = FastMCP("Test")

        @mcp.tool(auth=_deny_all)
        def read_resource(query: str) -> str:
            return f"user: {query}"

        @mcp.resource("config://app")
        def app_config() -> str:
            return "config data"

        mcp.add_transform(ResourcesAsTools(mcp))

        token = _current_transport.set("http")
        try:
            tools = await mcp.list_tools()
            tool_names = [t.name for t in tools]
            assert "read_resource" in tool_names

            tool = await mcp.get_tool("read_resource")
            assert tool is not None
            assert "uri" in tool.parameters["properties"]
        finally:
            _current_transport.reset(token)

    async def test_visible_colliding_parent_tool_with_unrelated_search_child(
        self,
    ):
        """Visible colliding parent user tool is preferred over synthetic tool even if an unrelated child has search transform."""
        from fastmcp.server.providers import LocalProvider

        mcp = FastMCP("Test")

        @mcp.tool
        def read_resource(query: str) -> str:
            return f"parent: {query}"

        child = LocalProvider()

        @child.tool
        def unrelated_tool() -> str:
            return "unrelated"

        child.add_transform(RegexSearchTransform())
        mcp.add_provider(child)

        @mcp.resource("config://app")
        def app_config() -> str:
            return "config data"

        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "read_resource" in tool_names

            result = await client.call_tool("read_resource", {"query": "hello"})
            assert result.data == "parent: hello"

    async def test_disabled_user_tool_falls_back_to_synthetic_tool(self):
        """When user tool is disabled, synthetic tool is advertised and callable."""
        mcp = FastMCP("Test")

        @mcp.tool(tags={"internal"})
        def read_resource(query: str) -> str:
            return f"user: {query}"

        @mcp.resource("config://app")
        def app_config() -> str:
            return "config data"

        mcp.disable(tags={"internal"})
        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "read_resource" in tool_names

            result = await client.call_tool("read_resource", {"uri": "config://app"})
            assert result.data == "config data"

    async def test_mounted_child_search_hides_user_tool(self):
        """When an unnamespaced mounted child defines read_resource behind search transform, parent uses synthetic tool."""
        parent = FastMCP("Parent")
        child = FastMCP("Child")

        @child.tool
        def read_resource(query: str) -> str:
            return f"child: {query}"

        child.add_transform(RegexSearchTransform())
        parent.mount(child)

        @parent.resource("config://app")
        def app_config() -> str:
            return "config data"

        parent.add_transform(ResourcesAsTools(parent))

        async with Client(parent) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "read_resource" in tool_names

            result = await client.call_tool("read_resource", {"uri": "config://app"})
            assert result.data == "config data"

    async def test_code_mode_preceding_resources_as_tools(self):
        """When CodeMode precedes ResourcesAsTools, hidden user tool does not prevent synthetic tool."""
        from fastmcp.experimental.transforms.code_mode import CodeMode

        mcp = FastMCP("Test")

        @mcp.tool
        def read_resource(query: str) -> str:
            return f"user: {query}"

        @mcp.resource("config://app")
        def app_config() -> str:
            return "config data"

        mcp.add_transform(CodeMode())
        mcp.add_transform(ResourcesAsTools(mcp))

        tool = await mcp.get_tool("read_resource")
        assert tool is not None
        assert "uri" in tool.parameters["properties"]

    async def test_retaining_catalog_transform_preserves_colliding_user_tool(self):
        """A pass-through CatalogTransform leaves user tool advertised and callable via Client."""
        from fastmcp.server.transforms.catalog import CatalogTransform

        class RetainingTransform(CatalogTransform):
            async def transform_tools(self, tools):
                return tools

        mcp = FastMCP("Test")

        @mcp.tool
        def read_resource(query: str) -> str:
            return f"user: {query}"

        @mcp.resource("config://app")
        def app_config() -> str:
            return "config data"

        mcp.add_transform(RetainingTransform())
        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "read_resource")
            assert "query" in tool.input_schema["properties"]

            result = await client.call_tool("read_resource", {"query": "hello"})
            assert result.data == "user: hello"

    async def test_subsequent_disable_transform_leaves_synthetic_tool_accessible(self):
        """When mcp.disable follows ResourcesAsTools, disabled user tool does not suppress synthetic tool."""
        mcp = FastMCP("Test")

        @mcp.tool(tags={"internal"})
        def read_resource(query: str) -> str:
            return f"internal: {query}"

        @mcp.resource("config://app")
        def app_config() -> str:
            return "config data"

        mcp.add_transform(ResourcesAsTools(mcp))
        mcp.disable(tags={"internal"})

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "read_resource" in tool_names

            result = await client.call_tool("read_resource", {"uri": "config://app"})
            assert result.data == "config data"

    async def test_type_aware_identity_does_not_hijack_child_tool_ownership(self):
        """Parent resource named read_resource does not hijack provider identity of child's search-hidden tool."""
        parent = FastMCP("Parent")
        child = FastMCP("Child")

        @child.tool
        def read_resource(query: str) -> str:
            return f"child: {query}"

        child.add_transform(RegexSearchTransform())
        parent.mount(child)

        @parent.resource("res://read_resource")
        def read_resource_res() -> str:
            return "resource payload"

        parent.add_transform(ResourcesAsTools(parent))

        async with Client(parent) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "read_resource" in tool_names

            result = await client.call_tool(
                "read_resource", {"uri": "res://read_resource"}
            )
            assert result.data == "resource payload"

    async def test_custom_provider_with_search_transform_hides_user_tool(self):
        """Providers resolving via _get_tool with SearchTransform yield synthetic reader."""
        from fastmcp.server.providers.base import Provider
        from fastmcp.tools.base import Tool

        class CustomProvider(Provider):
            def __init__(self):
                super().__init__()
                self._tool = Tool.from_function(
                    lambda query: f"custom: {query}", name="read_resource"
                )

            async def _list_tools(self):
                return [self._tool]

            async def _get_tool(self, name: str, version=None):
                return self._tool if name == "read_resource" else None

        provider = CustomProvider()
        provider.add_transform(RegexSearchTransform())

        server = FastMCP("Server", providers=[provider])

        @server.resource("config://app")
        def app_config() -> str:
            return "config data"

        server.add_transform(ResourcesAsTools(server))

        async with Client(server) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "read_resource" in tool_names

            result = await client.call_tool("read_resource", {"uri": "config://app"})
            assert result.data == "config data"

    async def test_session_disabled_user_tool_yields_synthetic_tool(self):
        """When a user tool colliding with synthetic tool is disabled in session, synthetic tool is advertised and callable."""
        mcp = FastMCP("Server")

        @mcp.tool(tags={"internal"})
        def read_resource(query: str) -> str:
            return f"user: {query}"

        @mcp.tool
        async def deactivate_internal(ctx: Context) -> str:
            await ctx.disable_components(tags={"internal"})
            return "deactivated"

        @mcp.resource("config://app")
        def app_config() -> str:
            return "config data"

        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            user_tool = next(t for t in tools if t.name == "read_resource")
            assert "query" in user_tool.input_schema.get("properties", {})

            await client.call_tool("deactivate_internal", {})

            tools_after = await client.list_tools()
            syn_tool = next((t for t in tools_after if t.name == "read_resource"), None)
            assert syn_tool is not None
            assert "uri" in syn_tool.input_schema.get("properties", {})

            res = await client.call_tool("read_resource", {"uri": "config://app"})
            assert res.data == "config data"

    async def test_version_spec_passed_to_provider_get_tool(self):
        """Provider expecting VersionSpec in _get_tool receives valid VersionSpec when probed."""
        from typing import Any

        from fastmcp.server.providers.base import Provider
        from fastmcp.tools.base import Tool
        from fastmcp.utilities.versions import VersionSpec

        received_spec: list[Any] = []

        class VersionAwareProvider(Provider):
            def __init__(self):
                super().__init__()
                self._tool = Tool.from_function(
                    lambda query: f"custom: {query}",
                    name="read_resource",
                    version="1.2.3",
                )

            async def _list_tools(self):
                return [self._tool]

            async def _get_tool(self, name: str, version: VersionSpec | None = None):
                received_spec.append(version)
                if name == "read_resource" and (
                    version is None or version.matches(self._tool.version)
                ):
                    return self._tool
                return None

        provider = VersionAwareProvider()
        provider.add_transform(RegexSearchTransform())

        server = FastMCP("Server", providers=[provider])

        @server.resource("config://app")
        def app_config() -> str:
            return "config data"

        server.add_transform(ResourcesAsTools(server))

        async with Client(server) as client:
            tools = await client.list_tools()
            assert "read_resource" in [t.name for t in tools]
            res = await client.call_tool("read_resource", {"uri": "config://app"})
            assert res.data == "config data"
            assert any(isinstance(spec, VersionSpec) for spec in received_spec)

    async def test_sequence_sensitive_catalog_transform_hiding_tool(self):
        """Sequence-sensitive transforms like catalog slicing are evaluated against full provider catalog."""
        from collections.abc import Sequence

        from fastmcp.server.transforms.catalog import CatalogTransform
        from fastmcp.tools.base import Tool

        class SlicingCatalogTransform(CatalogTransform):
            async def transform_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
                return tools[:1]

        server = FastMCP("Server")

        @server.tool
        def first_tool() -> str:
            return "first"

        @server.tool
        def read_resource(query: str) -> str:
            return f"colliding: {query}"

        server.add_transform(SlicingCatalogTransform())

        @server.resource("config://app")
        def app_config() -> str:
            return "config data"

        server.add_transform(ResourcesAsTools(server))

        async with Client(server) as client:
            tools = await client.list_tools()
            names = [t.name for t in tools]
            assert "first_tool" in names
            assert "read_resource" in names

            res = await client.call_tool("read_resource", {"uri": "config://app"})
            assert res.data == "config data"

    async def test_namespaced_provider_user_tool_collision(self):
        """Collisions with user tools under a Namespace transform are recognized and prioritized."""
        sub = FastMCP("Sub")

        @sub.tool(name="resource")
        def resource_tool(uri: str) -> str:
            return f"sub: {uri}"

        server = FastMCP("Server")
        server.mount(sub, namespace="read")

        @server.resource("config://app")
        def app_config() -> str:
            return "resource data"

        server.add_transform(ResourcesAsTools(server))

        async with Client(server) as client:
            tools = await client.list_tools()
            read_tools = [t for t in tools if t.name == "read_resource"]
            assert len(read_tools) == 1

            res = await client.call_tool("read_resource", {"uri": "custom_arg"})
            assert res.data == "sub: custom_arg"

    async def test_preceding_namespace_with_retaining_catalog_calls_user_tool(self):
        """When a preceding Namespace and a retaining CatalogTransform precede ResourcesAsTools,
        the user tool is called using its query schema rather than falling back to synthetic tool."""
        from collections.abc import Sequence

        from fastmcp.server.transforms.catalog import CatalogTransform
        from fastmcp.server.transforms.namespace import Namespace
        from fastmcp.tools.base import Tool

        class RetainingCatalog(CatalogTransform):
            async def transform_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
                return tools

        server = FastMCP("Server")

        @server.tool(name="resource")
        def user_resource(query: str) -> str:
            return f"user: {query}"

        @server.resource("config://app")
        def app_config() -> str:
            return "app config"

        server.add_transform(Namespace("read"))
        server.add_transform(RetainingCatalog())
        server.add_transform(ResourcesAsTools(server))

        async with Client(server) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "read_resource" in tool_names

            res = await client.call_tool("read_resource", {"query": "test"})
            assert res.data == "user: test"

    async def test_mounted_child_with_middleware_filtering_tool_uses_synthetic_tool(
        self,
    ):
        """When a mounted child has middleware filtering out a colliding tool, the parent's synthetic tool is used."""
        from collections.abc import Sequence
        from typing import Any

        from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
        from fastmcp.tools.base import Tool

        class FilterMiddleware(Middleware):
            async def on_list_tools(
                self,
                context: MiddlewareContext[Any],
                call_next: CallNext[Any, Sequence[Tool]],
            ) -> Sequence[Tool]:
                tools = await call_next(context)
                return [t for t in tools if t.name != "read_resource"]

        child = FastMCP("Child")

        @child.tool(name="read_resource")
        def child_resource(query: str) -> str:
            return f"child: {query}"

        child.add_middleware(FilterMiddleware())

        parent = FastMCP("Parent")
        parent.mount(child)

        @parent.resource("config://app")
        def app_config() -> str:
            return "app config"

        parent.add_transform(ResourcesAsTools(parent))

        async with Client(parent) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "read_resource" in tool_names

            res = await client.call_tool("read_resource", {"uri": "config://app"})
            assert res.data == "app config"

    async def test_parent_search_transform_with_untransformed_mounted_child_pinned_tool(
        self,
    ):
        """When parent has a search transform with always_visible before ResourcesAsTools,
        an untransformed mounted child tool in always_visible is preserved and called with user schema."""
        from fastmcp.server.transforms.search.regex import RegexSearchTransform

        child = FastMCP("Child")

        @child.tool(name="read_resource")
        def child_resource(query: str) -> str:
            return f"child: {query}"

        parent = FastMCP("Parent")
        parent.mount(child)

        @parent.resource("config://app")
        def app_config() -> str:
            return "app config"

        parent.add_transform(RegexSearchTransform(always_visible=["read_resource"]))
        parent.add_transform(ResourcesAsTools(parent))

        async with Client(parent) as client:
            tools = await client.list_tools()
            matched = [t for t in tools if t.name == "read_resource"]
            assert len(matched) == 1
            assert "query" in matched[0].input_schema["properties"]

            res = await client.call_tool("read_resource", {"query": "hello"})
            assert res.data == "child: hello"

    async def test_ancestor_aggregate_search_transform_hiding_child_tool_yields_synthetic_tool(
        self,
    ):
        """When an untransformed mounted child is inside an AggregateProvider with a search transform,
        the ancestor search transform is retained and the synthetic tool is advertised and callable."""
        from fastmcp.server.providers import AggregateProvider
        from fastmcp.server.transforms.search.regex import RegexSearchTransform

        child = FastMCP("Child")

        @child.tool(name="read_resource")
        def child_resource(query: str) -> str:
            return f"child: {query}"

        agg = AggregateProvider()
        agg.add_provider(child)
        agg.add_transform(RegexSearchTransform(always_visible=[]))

        parent = FastMCP("Parent", providers=[agg])

        @parent.resource("config://app")
        def app_config() -> str:
            return "app config"

        parent.add_transform(ResourcesAsTools(parent))

        async with Client(parent) as client:
            tools = await client.list_tools()
            matched = [t for t in tools if t.name == "read_resource"]
            assert len(matched) == 1
            assert "uri" in matched[0].input_schema["properties"]

            res = await client.call_tool("read_resource", {"uri": "config://app"})
            assert res.data == "app config"

    async def test_tool_transform_rename_with_search_transform_yields_synthetic_tool(
        self,
    ):
        """When child provider renames a colliding tool via ToolTransform and hides it via search,
        ownership is resolved and the synthetic tool is advertised and callable."""
        from fastmcp.server.providers import LocalProvider
        from fastmcp.server.transforms import ToolTransform
        from fastmcp.server.transforms.search.regex import RegexSearchTransform
        from fastmcp.tools.tool_transform import ToolTransformConfig

        child = LocalProvider()

        @child.tool
        def fetch_data(query: str) -> str:
            return query

        child.add_transform(RegexSearchTransform(always_visible=[]))
        child.add_transform(
            ToolTransform({"fetch_data": ToolTransformConfig(name="read_resource")})
        )

        parent = FastMCP("Parent", providers=[child])

        @parent.resource("config://app")
        def app_config() -> str:
            return "app config"

        parent.add_transform(ResourcesAsTools(parent))

        async with Client(parent) as client:
            tools = await client.list_tools()
            matched = [t for t in tools if t.name == "read_resource"]
            assert len(matched) == 1
            assert "uri" in matched[0].input_schema["properties"]

            res = await client.call_tool("read_resource", {"uri": "config://app"})
            assert res.data == "app config"

    async def test_plain_transform_listing_override_yields_synthetic_tool(self):
        """When a plain Transform subclass overrides only list_tools to hide a colliding user tool,
        the fast-path guard accounts for it and the synthetic tool is advertised and callable."""
        from fastmcp.server.transforms import Transform

        class CustomFilter(Transform):
            async def list_tools(self, tools):
                return [t for t in tools if t.name != "read_resource"]

        mcp = FastMCP("Test")

        @mcp.tool
        def read_resource(query: str) -> str:
            return f"user: {query}"

        @mcp.resource("config://app")
        def app_config() -> str:
            return "app config"

        mcp.add_transform(CustomFilter())
        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            matched = [t for t in tools if t.name == "read_resource"]
            assert len(matched) == 1
            assert "uri" in matched[0].input_schema["properties"]

            res = await client.call_tool("read_resource", {"uri": "config://app"})
            assert res.data == "app config"


class TestListResourcesTool:
    """Test the list_resources tool."""

    async def test_lists_static_resources(self):
        """list_resources returns static resources with uri."""
        mcp = FastMCP("Test")

        @mcp.resource("config://app")
        def app_config() -> str:
            return "config data"

        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool("list_resources", {})
            resources = json.loads(result.data)

            assert len(resources) == 1
            assert resources[0]["uri"] == "config://app"
            assert resources[0]["name"] == "app_config"

    async def test_lists_resource_templates(self):
        """list_resources returns templates with uri_template."""
        mcp = FastMCP("Test")

        @mcp.resource("file://{path}")
        def read_file(path: str) -> str:
            return f"content of {path}"

        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool("list_resources", {})
            resources = json.loads(result.data)

            assert len(resources) == 1
            assert resources[0]["uri_template"] == "file://{path}"
            assert "uri" not in resources[0]

    async def test_lists_both_resources_and_templates(self):
        """list_resources returns both static and templated resources."""
        mcp = FastMCP("Test")

        @mcp.resource("config://app")
        def app_config() -> str:
            return "config"

        @mcp.resource("file://{path}")
        def read_file(path: str) -> str:
            return f"content of {path}"

        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool("list_resources", {})
            resources = json.loads(result.data)

            assert len(resources) == 2
            # One has uri, one has uri_template
            uris = [r.get("uri") for r in resources if r.get("uri")]
            templates = [
                r.get("uri_template") for r in resources if r.get("uri_template")
            ]
            assert uris == ["config://app"]
            assert templates == ["file://{path}"]

    async def test_lists_highest_version_only(self):
        """list_resources shows one entry per URI, like resources/list does."""
        mcp = FastMCP("Test")

        @mcp.resource("config://app", version="1.0")
        def config_v1() -> str:
            """Config v1."""
            return "v1"

        @mcp.resource("config://app", version="2.0")
        def config_v2() -> str:
            """Config v2."""
            return "v2"

        @mcp.resource("file://{path}", version="1.0")
        def file_v1(path: str) -> str:
            """File v1."""
            return path

        @mcp.resource("file://{path}", version="2.0")
        def file_v2(path: str) -> str:
            """File v2."""
            return path

        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool("list_resources", {})
            resources = json.loads(result.data)

            assert resources == [
                {
                    "uri": "config://app",
                    "name": "config_v2",
                    "description": "Config v2.",
                    "mime_type": "text/plain",
                },
                {
                    "uri_template": "file://{path}",
                    "name": "file_v2",
                    "description": "File v2.",
                },
            ]

    async def test_empty_when_no_resources(self):
        """list_resources returns empty list when no resources exist."""
        mcp = FastMCP("Test")
        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool("list_resources", {})
            assert json.loads(result.data) == []


class TestReadResourceTool:
    """Test the read_resource tool."""

    async def test_reads_static_resource(self):
        """read_resource reads a static resource by URI."""
        mcp = FastMCP("Test")

        @mcp.resource("config://app")
        def app_config() -> str:
            return "my config data"

        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool("read_resource", {"uri": "config://app"})
            assert result.data == "my config data"

    async def test_reads_templated_resource(self):
        """read_resource reads a templated resource with parameters."""
        mcp = FastMCP("Test")

        @mcp.resource("user://{user_id}/profile")
        def user_profile(user_id: str) -> str:
            return f"Profile for user {user_id}"

        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool(
                "read_resource", {"uri": "user://123/profile"}
            )
            assert result.data == "Profile for user 123"

    async def test_error_on_unknown_resource(self):
        """read_resource raises error for unknown URI."""
        from fastmcp.exceptions import ToolError

        mcp = FastMCP("Test")
        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="Unknown resource"):
                await client.call_tool("read_resource", {"uri": "unknown://resource"})

    async def test_reads_binary_as_base64(self):
        """read_resource returns binary content as base64."""
        mcp = FastMCP("Test")

        @mcp.resource("data://binary", mime_type="application/octet-stream")
        def binary_data() -> bytes:
            return b"\x00\x01\x02\x03"

        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool("read_resource", {"uri": "data://binary"})
            # Should be base64 encoded
            decoded = base64.b64decode(result.data)
            assert decoded == b"\x00\x01\x02\x03"


class TestResourcesAsToolsWithNamespace:
    """Test ResourcesAsTools combined with other transforms."""

    async def test_works_with_namespace_on_provider(self):
        """ResourcesAsTools works when provider has Namespace transform."""
        from fastmcp.server.providers import FastMCPProvider
        from fastmcp.server.transforms import Namespace

        sub = FastMCP("Sub")

        @sub.resource("config://app")
        def app_config() -> str:
            return "sub config"

        main = FastMCP("Main")
        provider = FastMCPProvider(sub)
        provider.add_transform(Namespace("sub"))
        main.add_provider(provider)
        main.add_transform(ResourcesAsTools(main))

        async with Client(main) as client:
            result = await client.call_tool("list_resources", {})
            resources = json.loads(result.data)

            # Resource should have namespaced URI
            assert len(resources) == 1
            assert resources[0]["uri"] == "config://sub/app"


class TestResourcesAsToolsRepr:
    """Test ResourcesAsTools repr."""

    def test_repr(self):
        """Transform has useful repr."""
        mcp = FastMCP("Test")
        transform = ResourcesAsTools(mcp)
        assert "ResourcesAsTools" in repr(transform)


class TestResourcesAsToolsAnnotations:
    """Test ToolAnnotations on generated resource tools."""

    async def test_list_resources_is_read_only(self):
        """list_resources is annotated as read-only by default."""
        mcp = FastMCP("Test")
        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "list_resources")
            assert tool.annotations is not None
            assert tool.annotations.read_only_hint is True

    async def test_read_resource_is_read_only(self):
        """read_resource is annotated as read-only by default."""
        mcp = FastMCP("Test")
        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "read_resource")
            assert tool.annotations is not None
            assert tool.annotations.read_only_hint is True


def _deny_all(ctx: AuthContext) -> bool:
    """Auth check that always denies access."""
    return False


class TestResourcesAsToolsAuthOnServer:
    """Auth checks work when using ResourcesAsTools on a FastMCP server."""

    async def test_auth_protected_resources_hidden_from_list(self):
        """Auth-protected resources are filtered from list_resources tool."""
        mcp = FastMCP("Test")

        @mcp.resource("test://open")
        def open_resource() -> str:
            return "open content"

        @mcp.resource("test://protected", auth=_deny_all)
        def protected_resource() -> str:
            return "protected content"

        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool("list_resources", {})
            items = json.loads(result.data)
            uris = [r.get("uri") for r in items if r.get("uri")]
            assert "test://open" in uris
            assert "test://protected" not in uris

    async def test_auth_protected_resource_cannot_be_read(self):
        """Auth-protected resources cannot be read via read_resource tool."""
        mcp = FastMCP("Test")

        @mcp.resource("test://protected", auth=_deny_all)
        def protected_resource() -> str:
            return "protected content"

        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            with pytest.raises(ToolError):
                await client.call_tool("read_resource", {"uri": "test://protected"})

    async def test_open_resource_still_accessible(self):
        """Non-auth-protected resources can still be read."""
        mcp = FastMCP("Test")

        @mcp.resource("test://open")
        def open_resource() -> str:
            return "open content"

        @mcp.resource("test://protected", auth=_deny_all)
        def protected_resource() -> str:
            return "protected content"

        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool("read_resource", {"uri": "test://open"})
            assert result.data == "open content"


class TestResourcesAsToolsVisibilityOnServer:
    """Visibility filtering works when using ResourcesAsTools on a server."""

    async def test_disabled_resources_hidden_from_list(self):
        """Disabled resources are not listed via list_resources tool."""
        mcp = FastMCP("Test")

        @mcp.resource("test://public")
        def public_resource() -> str:
            return "public content"

        @mcp.resource("test://secret")
        def secret_resource() -> str:
            return "secret content"

        mcp.disable(names={"test://secret"})
        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool("list_resources", {})
            items = json.loads(result.data)
            uris = [r.get("uri") for r in items if r.get("uri")]
            assert "test://public" in uris
            assert "test://secret" not in uris

    async def test_disabled_resource_cannot_be_read(self):
        """Disabled resources cannot be read via read_resource tool."""
        mcp = FastMCP("Test")

        @mcp.resource("test://secret")
        def secret_resource() -> str:
            return "secret content"

        mcp.disable(names={"test://secret"})
        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            with pytest.raises(ToolError):
                await client.call_tool("read_resource", {"uri": "test://secret"})
