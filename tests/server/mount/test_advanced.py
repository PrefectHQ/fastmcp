"""Advanced mounting scenarios."""

import pytest
from mcp.types import TextContent

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.providers import FastMCPProvider
from fastmcp.server.providers.wrapped_provider import _WrappedProvider


class TestDynamicChanges:
    """Test that changes to mounted servers are reflected dynamically."""

    async def test_adding_tool_after_mounting(self):
        """Test that tools added after mounting are accessible."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        # Mount the sub-app before adding any tools
        main_app.mount(sub_app, "sub")

        # Initially, there should be no tools from sub_app
        tools = await main_app.list_tools()
        assert not any(t.name.startswith("sub_") for t in tools)

        # Add a tool to the sub-app after mounting
        @sub_app.tool
        def dynamic_tool() -> str:
            return "Added after mounting"

        # The tool should be accessible through the main app
        tools = await main_app.list_tools()
        assert any(t.name == "sub_dynamic_tool" for t in tools)

        # Call the dynamically added tool
        result = await main_app.call_tool("sub_dynamic_tool", {})
        assert result.structured_content == {"result": "Added after mounting"}

    async def test_removing_tool_after_mounting(self):
        """Test that tools removed from mounted servers are no longer accessible."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.tool
        def temp_tool() -> str:
            return "Temporary tool"

        # Mount the sub-app
        main_app.mount(sub_app, "sub")

        # Initially, the tool should be accessible
        tools = await main_app.list_tools()
        assert any(t.name == "sub_temp_tool" for t in tools)

        # Remove the tool from sub_app using public API
        sub_app.remove_tool("temp_tool")

        # The tool should no longer be accessible
        tools = await main_app.list_tools()
        assert not any(t.name == "sub_temp_tool" for t in tools)


class TestCustomRouteForwarding:
    """Test that custom HTTP routes from mounted servers are forwarded."""

    async def test_get_additional_http_routes_empty(self):
        """Test _get_additional_http_routes returns empty list for server with no routes."""
        server = FastMCP("TestServer")
        routes = server._get_additional_http_routes()
        assert routes == []

    async def test_get_additional_http_routes_with_custom_route(self):
        """Test _get_additional_http_routes returns server's own routes."""
        server = FastMCP("TestServer")

        @server.custom_route("/test", methods=["GET"])
        async def test_route(request):
            from starlette.responses import JSONResponse

            return JSONResponse({"message": "test"})

        routes = server._get_additional_http_routes()
        assert len(routes) == 1
        assert hasattr(routes[0], "path")
        assert routes[0].path == "/test"

    async def test_mounted_servers_tracking(self):
        """Test that providers list tracks mounted servers correctly."""
        from fastmcp.server.providers.local_provider import LocalProvider

        main_server = FastMCP("MainServer")
        sub_server1 = FastMCP("SubServer1")
        sub_server2 = FastMCP("SubServer2")

        @sub_server1.tool
        def tool1() -> str:
            return "1"

        @sub_server2.tool
        def tool2() -> str:
            return "2"

        # Initially only LocalProvider
        assert len(main_server.providers) == 1
        assert isinstance(main_server.providers[0], LocalProvider)

        # Mount first server
        main_server.mount(sub_server1, "sub1")
        assert len(main_server.providers) == 2
        # LocalProvider is at index 0, mounted provider (wrapped) at index 1
        provider1 = main_server.providers[1]
        assert isinstance(provider1, _WrappedProvider)
        assert isinstance(provider1._inner, FastMCPProvider)
        assert provider1._inner.server == sub_server1

        # Mount second server
        main_server.mount(sub_server2, "sub2")
        assert len(main_server.providers) == 3
        provider2 = main_server.providers[2]
        assert isinstance(provider2, _WrappedProvider)
        assert isinstance(provider2._inner, FastMCPProvider)
        assert provider2._inner.server == sub_server2

        # Verify namespacing is applied by checking tool names
        tools = await main_server.list_tools()
        tool_names = {t.name for t in tools}
        assert tool_names == {"sub1_tool1", "sub2_tool2"}

    async def test_multiple_routes_same_server(self):
        """Test that multiple custom routes from same server are all included."""
        server = FastMCP("TestServer")

        @server.custom_route("/route1", methods=["GET"])
        async def route1(request):
            from starlette.responses import JSONResponse

            return JSONResponse({"message": "route1"})

        @server.custom_route("/route2", methods=["POST"])
        async def route2(request):
            from starlette.responses import JSONResponse

            return JSONResponse({"message": "route2"})

        routes = server._get_additional_http_routes()
        assert len(routes) == 2
        route_paths = [route.path for route in routes if hasattr(route, "path")]
        assert "/route1" in route_paths
        assert "/route2" in route_paths


class TestDeeplyNestedMount:
    """Test deeply nested mount scenarios (3+ levels deep).

    This tests the fix for https://github.com/jlowin/fastmcp/issues/2583
    where tools/resources/prompts mounted more than 2 levels deep would fail
    to invoke even though they were correctly listed.
    """

    async def test_three_level_nested_tool_invocation(self):
        """Test invoking tools from servers mounted 3 levels deep."""
        root = FastMCP("root")
        middle = FastMCP("middle")
        leaf = FastMCP("leaf")

        @leaf.tool
        def add(a: int, b: int) -> int:
            return a + b

        @middle.tool
        def multiply(a: int, b: int) -> int:
            return a * b

        middle.mount(leaf, namespace="leaf")
        root.mount(middle, namespace="middle")

        # Tool at level 2 should work
        result = await root.call_tool("middle_multiply", {"a": 3, "b": 4})
        assert result.structured_content == {"result": 12}

        # Tool at level 3 should also work (this was the bug)
        result = await root.call_tool("middle_leaf_add", {"a": 5, "b": 7})
        assert result.structured_content == {"result": 12}

    async def test_three_level_nested_resource_invocation(self):
        """Test reading resources from servers mounted 3 levels deep."""
        root = FastMCP("root")
        middle = FastMCP("middle")
        leaf = FastMCP("leaf")

        @leaf.resource("leaf://data")
        def leaf_data() -> str:
            return "leaf data"

        @middle.resource("middle://data")
        def middle_data() -> str:
            return "middle data"

        middle.mount(leaf, namespace="leaf")
        root.mount(middle, namespace="middle")

        # Resource at level 2 should work
        result = await root.read_resource("middle://middle/data")
        assert result.contents[0].content == "middle data"

        # Resource at level 3 should also work
        result = await root.read_resource("leaf://middle/leaf/data")
        assert result.contents[0].content == "leaf data"

    async def test_three_level_nested_resource_template_invocation(self):
        """Test reading resource templates from servers mounted 3 levels deep."""
        root = FastMCP("root")
        middle = FastMCP("middle")
        leaf = FastMCP("leaf")

        @leaf.resource("leaf://item/{id}")
        def leaf_item(id: str) -> str:
            return f"leaf item {id}"

        @middle.resource("middle://item/{id}")
        def middle_item(id: str) -> str:
            return f"middle item {id}"

        middle.mount(leaf, namespace="leaf")
        root.mount(middle, namespace="middle")

        # Resource template at level 2 should work
        result = await root.read_resource("middle://middle/item/42")
        assert result.contents[0].content == "middle item 42"

        # Resource template at level 3 should also work
        result = await root.read_resource("leaf://middle/leaf/item/99")
        assert result.contents[0].content == "leaf item 99"

    async def test_three_level_nested_prompt_invocation(self):
        """Test getting prompts from servers mounted 3 levels deep."""
        root = FastMCP("root")
        middle = FastMCP("middle")
        leaf = FastMCP("leaf")

        @leaf.prompt
        def leaf_prompt(name: str) -> str:
            return f"Hello from leaf: {name}"

        @middle.prompt
        def middle_prompt(name: str) -> str:
            return f"Hello from middle: {name}"

        middle.mount(leaf, namespace="leaf")
        root.mount(middle, namespace="middle")

        # Prompt at level 2 should work
        result = await root.render_prompt("middle_middle_prompt", {"name": "World"})
        assert isinstance(result.messages[0].content, TextContent)
        assert "Hello from middle: World" in result.messages[0].content.text

        # Prompt at level 3 should also work
        result = await root.render_prompt("middle_leaf_leaf_prompt", {"name": "Test"})
        assert isinstance(result.messages[0].content, TextContent)
        assert "Hello from leaf: Test" in result.messages[0].content.text

    async def test_four_level_nested_tool_invocation(self):
        """Test invoking tools from servers mounted 4 levels deep."""
        root = FastMCP("root")
        level1 = FastMCP("level1")
        level2 = FastMCP("level2")
        level3 = FastMCP("level3")

        @level3.tool
        def deep_tool() -> str:
            return "very deep"

        level2.mount(level3, namespace="l3")
        level1.mount(level2, namespace="l2")
        root.mount(level1, namespace="l1")

        # Verify tool is listed
        tools = await root.list_tools()
        tool_names = [t.name for t in tools]
        assert "l1_l2_l3_deep_tool" in tool_names

        # Tool at level 4 should work
        result = await root.call_tool("l1_l2_l3_deep_tool", {})
        assert result.structured_content == {"result": "very deep"}


class TestToolNameOverrides:
    """Test tool and prompt name overrides in mount() (issue #2596)."""

    async def test_tool_names_override_via_transforms(self):
        """Test that tool_names renames tools via ToolTransform layer.

        Tool renames are applied first, then namespace prefixing.
        So original_tool → custom_name → prefix_custom_name.
        """
        sub = FastMCP("Sub")

        @sub.tool
        def original_tool() -> str:
            return "test"

        main = FastMCP("Main")
        # tool_names renames first, then namespace is applied
        main.mount(
            sub,
            namespace="prefix",
            tool_names={"original_tool": "custom_name"},
        )

        # Server introspection shows renamed + namespaced names
        tools = await main.list_tools()
        tool_names = [t.name for t in tools]
        assert "prefix_custom_name" in tool_names
        assert "original_tool" not in tool_names
        assert "prefix_original_tool" not in tool_names
        assert "custom_name" not in tool_names

    async def test_tool_names_override_applied_in_list_tools(self):
        """Test that tool_names override is reflected in list_tools()."""
        sub = FastMCP("Sub")

        @sub.tool
        def original_tool() -> str:
            return "test"

        main = FastMCP("Main")
        main.mount(
            sub,
            namespace="prefix",
            tool_names={"original_tool": "custom_name"},
        )

        tools = await main.list_tools()
        tool_names = [t.name for t in tools]
        assert "prefix_custom_name" in tool_names
        assert "prefix_original_tool" not in tool_names

    async def test_tool_call_with_overridden_name(self):
        """Test that overridden tool can be called by its new name."""
        sub = FastMCP("Sub")

        @sub.tool
        def original_tool() -> str:
            return "success"

        main = FastMCP("Main")
        main.mount(
            sub,
            namespace="prefix",
            tool_names={"original_tool": "renamed"},
        )

        # Tool is renamed then namespaced: original_tool → renamed → prefix_renamed
        result = await main.call_tool("prefix_renamed", {})
        assert result.structured_content == {"result": "success"}

    def test_duplicate_tool_rename_targets_raises_error(self):
        """Test that duplicate target names in tool_renames raises ValueError."""
        sub = FastMCP("Sub")
        main = FastMCP("Main")

        with pytest.raises(ValueError, match="duplicate target name"):
            main.mount(
                sub,
                tool_names={"tool_a": "same_name", "tool_b": "same_name"},
            )


class TestMountedServerDocketBehavior:
    """Regression tests for mounted server lifecycle behavior.

    These tests guard against architectural changes that could accidentally
    start Docket instances for mounted servers. Mounted servers should only
    run their user-defined lifespan, not the full _lifespan_manager which
    includes Docket creation.
    """

    async def test_mounted_server_does_not_have_docket(self):
        """Test that a mounted server doesn't create its own Docket.

        MountedProvider.lifespan() should call only the server's _lifespan
        (user-defined lifespan), not _lifespan_manager (which includes Docket).
        """
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        # Need a task-enabled component to trigger Docket initialization
        @main_app.tool(task=True)
        async def _trigger_docket() -> str:
            return "trigger"

        @sub_app.tool
        def my_tool() -> str:
            return "test"

        main_app.mount(sub_app, "sub")

        # After running the main app's lifespan, the sub app should not have
        # its own Docket instance
        async with Client(main_app) as client:
            # The main app should have a docket (created by _lifespan_manager)
            # because it has a task-enabled component
            assert main_app.docket is not None

            # The mounted sub app should NOT have its own docket
            # It uses the parent's docket for background tasks
            assert sub_app.docket is None

            # But the tool should still work (prefixed as sub_my_tool)
            result = await client.call_tool("sub_my_tool", {})
            assert result.data == "test"


class TestComponentServicePrefixLess:
    """Test that enable/disable works with prefix-less mounted servers."""

    async def test_enable_tool_prefixless_mount(self):
        """Test enabling a tool on a prefix-less mounted server."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.tool
        def my_tool() -> str:
            return "test"

        # Mount without prefix
        main_app.mount(sub_app)

        # Initially the tool is enabled
        tools = await main_app.list_tools()
        assert any(t.name == "my_tool" for t in tools)

        # Disable and re-enable
        main_app.disable(names={"my_tool"}, components={"tool"})
        # Verify tool is now disabled
        tools = await main_app.list_tools()
        assert not any(t.name == "my_tool" for t in tools)

        main_app.enable(names={"my_tool"}, components={"tool"})
        # Verify tool is now enabled
        tools = await main_app.list_tools()
        assert any(t.name == "my_tool" for t in tools)

    async def test_enable_resource_prefixless_mount(self):
        """Test enabling a resource on a prefix-less mounted server."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.resource(uri="data://test")
        def my_resource() -> str:
            return "test data"

        # Mount without prefix
        main_app.mount(sub_app)

        # Disable and re-enable
        main_app.disable(names={"data://test"}, components={"resource"})
        # Verify resource is now disabled
        resources = await main_app.list_resources()
        assert not any(str(r.uri) == "data://test" for r in resources)

        main_app.enable(names={"data://test"}, components={"resource"})
        # Verify resource is now enabled
        resources = await main_app.list_resources()
        assert any(str(r.uri) == "data://test" for r in resources)

    async def test_enable_prompt_prefixless_mount(self):
        """Test enabling a prompt on a prefix-less mounted server."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.prompt
        def my_prompt() -> str:
            return "test prompt"

        # Mount without prefix
        main_app.mount(sub_app)

        # Disable and re-enable
        main_app.disable(names={"my_prompt"}, components={"prompt"})
        # Verify prompt is now disabled
        prompts = await main_app.list_prompts()
        assert not any(p.name == "my_prompt" for p in prompts)

        main_app.enable(names={"my_prompt"}, components={"prompt"})
        # Verify prompt is now enabled
        prompts = await main_app.list_prompts()
        assert any(p.name == "my_prompt" for p in prompts)
