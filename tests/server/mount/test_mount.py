"""Basic mounting functionality tests."""

import logging
import sys

import pytest
from mcp.types import TextContent

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.transports import SSETransport
from fastmcp.tools.tool import Tool
from fastmcp.tools.tool_transform import TransformedTool


class TestBasicMount:
    """Test basic mounting functionality."""

    async def test_mount_simple_server(self):
        """Test mounting a simple server and accessing its tool."""
        # Create main app and sub-app
        main_app = FastMCP("MainApp")

        # Add a tool to the sub-app
        def tool() -> str:
            return "This is from the sub app"

        sub_tool = Tool.from_function(tool)

        transformed_tool = TransformedTool.from_tool(
            name="transformed_tool", tool=sub_tool
        )

        sub_app = FastMCP("SubApp", tools=[transformed_tool, sub_tool])

        # Mount the sub-app to the main app
        main_app.mount(sub_app, "sub")

        # Get tools from main app, should include sub_app's tools
        tools = await main_app.list_tools()
        assert any(t.name == "sub_tool" for t in tools)
        assert any(t.name == "sub_transformed_tool" for t in tools)

        result = await main_app.call_tool("sub_tool", {})
        assert result.structured_content == {"result": "This is from the sub app"}

    async def test_mount_with_custom_separator(self):
        """Test mounting with a custom tool separator (deprecated but still supported)."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.tool
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        # Mount without custom separator - custom separators are deprecated
        main_app.mount(sub_app, "sub")

        # Tool should be accessible with the default separator
        tools = await main_app.list_tools()
        assert any(t.name == "sub_greet" for t in tools)

        # Call the tool
        result = await main_app.call_tool("sub_greet", {"name": "World"})
        assert result.structured_content == {"result": "Hello, World!"}

    @pytest.mark.parametrize("prefix", ["", None])
    async def test_mount_with_no_prefix(self, prefix):
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.tool
        def sub_tool() -> str:
            return "This is from the sub app"

        # Mount with empty prefix but without deprecated separators
        main_app.mount(sub_app, namespace=prefix)

        tools = await main_app.list_tools()
        # With empty prefix, the tool should keep its original name
        assert any(t.name == "sub_tool" for t in tools)

    async def test_mount_with_no_prefix_provided(self):
        """Test mounting without providing a prefix at all."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.tool
        def sub_tool() -> str:
            return "This is from the sub app"

        # Mount without providing a prefix (should be None)
        main_app.mount(sub_app)

        tools = await main_app.list_tools()
        # Without prefix, the tool should keep its original name
        assert any(t.name == "sub_tool" for t in tools)

        # Call the tool to verify it works
        result = await main_app.call_tool("sub_tool", {})
        assert result.structured_content == {"result": "This is from the sub app"}

    async def test_mount_tools_no_prefix(self):
        """Test mounting a server with tools without prefix."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.tool
        def sub_tool() -> str:
            return "Sub tool result"

        # Mount without prefix
        main_app.mount(sub_app)

        # Verify tool is accessible with original name
        tools = await main_app.list_tools()
        assert any(t.name == "sub_tool" for t in tools)

        # Test actual functionality
        tool_result = await main_app.call_tool("sub_tool", {})
        assert tool_result.structured_content == {"result": "Sub tool result"}

    async def test_mount_resources_no_prefix(self):
        """Test mounting a server with resources without prefix."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.resource(uri="data://config")
        def sub_resource():
            return "Sub resource data"

        # Mount without prefix
        main_app.mount(sub_app)

        # Verify resource is accessible with original URI
        resources = await main_app.list_resources()
        assert any(str(r.uri) == "data://config" for r in resources)

        # Test actual functionality
        resource_result = await main_app.read_resource("data://config")
        assert resource_result.contents[0].content == "Sub resource data"

    async def test_mount_resource_templates_no_prefix(self):
        """Test mounting a server with resource templates without prefix."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.resource(uri="users://{user_id}/info")
        def sub_template(user_id: str):
            return f"Sub template for user {user_id}"

        # Mount without prefix
        main_app.mount(sub_app)

        # Verify template is accessible with original URI template
        templates = await main_app.list_resource_templates()
        assert any(t.uri_template == "users://{user_id}/info" for t in templates)

        # Test actual functionality
        template_result = await main_app.read_resource("users://123/info")
        assert template_result.contents[0].content == "Sub template for user 123"

    async def test_mount_prompts_no_prefix(self):
        """Test mounting a server with prompts without prefix."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.prompt
        def sub_prompt() -> str:
            return "Sub prompt content"

        # Mount without prefix
        main_app.mount(sub_app)

        # Verify prompt is accessible with original name
        prompts = await main_app.list_prompts()
        assert any(p.name == "sub_prompt" for p in prompts)

        # Test actual functionality
        prompt_result = await main_app.render_prompt("sub_prompt")
        assert prompt_result.messages is not None


class TestMultipleServerMount:
    """Test mounting multiple servers simultaneously."""

    async def test_mount_multiple_servers(self):
        """Test mounting multiple servers with different prefixes."""
        main_app = FastMCP("MainApp")
        weather_app = FastMCP("WeatherApp")
        news_app = FastMCP("NewsApp")

        @weather_app.tool
        def get_forecast() -> str:
            return "Weather forecast"

        @news_app.tool
        def get_headlines() -> str:
            return "News headlines"

        # Mount both apps
        main_app.mount(weather_app, "weather")
        main_app.mount(news_app, "news")

        # Check both are accessible
        tools = await main_app.list_tools()
        assert any(t.name == "weather_get_forecast" for t in tools)
        assert any(t.name == "news_get_headlines" for t in tools)

        # Call tools from both mounted servers
        result1 = await main_app.call_tool("weather_get_forecast", {})
        assert result1.structured_content == {"result": "Weather forecast"}
        result2 = await main_app.call_tool("news_get_headlines", {})
        assert result2.structured_content == {"result": "News headlines"}

    async def test_mount_same_prefix(self):
        """Test that mounting with the same prefix replaces the previous mount."""
        main_app = FastMCP("MainApp")
        first_app = FastMCP("FirstApp")
        second_app = FastMCP("SecondApp")

        @first_app.tool
        def first_tool() -> str:
            return "First app tool"

        @second_app.tool
        def second_tool() -> str:
            return "Second app tool"

        # Mount first app
        main_app.mount(first_app, "api")
        tools = await main_app.list_tools()
        assert any(t.name == "api_first_tool" for t in tools)

        # Mount second app with same prefix
        main_app.mount(second_app, "api")
        tools = await main_app.list_tools()

        # Both apps' tools should be accessible (new behavior)
        assert any(t.name == "api_first_tool" for t in tools)
        assert any(t.name == "api_second_tool" for t in tools)

    @pytest.mark.skipif(
        sys.platform == "win32", reason="Windows asyncio networking timeouts."
    )
    async def test_mount_with_unreachable_proxy_servers(self, caplog):
        """Test graceful handling when multiple mounted servers fail to connect."""
        caplog.set_level(logging.DEBUG, logger="fastmcp")

        main_app = FastMCP("MainApp")
        working_app = FastMCP("WorkingApp")

        @working_app.tool
        def working_tool() -> str:
            return "Working tool"

        @working_app.resource(uri="working://data")
        def working_resource():
            return "Working resource"

        @working_app.prompt
        def working_prompt() -> str:
            return "Working prompt"

        # Mount the working server
        main_app.mount(working_app, "working")

        # Use an unreachable port
        unreachable_client = Client(
            transport=SSETransport("http://127.0.0.1:9999/sse/"),
            name="unreachable_client",
        )

        # Create a proxy server that will fail to connect
        unreachable_proxy = FastMCP.as_proxy(
            unreachable_client, name="unreachable_proxy"
        )

        # Mount the unreachable proxy
        main_app.mount(unreachable_proxy, "unreachable")

        # All object types should work from working server despite unreachable proxy
        async with Client(main_app, name="main_app_client") as client:
            # Test tools
            tools = await client.list_tools()
            tool_names = [tool.name for tool in tools]
            assert "working_working_tool" in tool_names

            # Test calling a tool
            result = await client.call_tool("working_working_tool", {})
            assert result.data == "Working tool"

            # Test resources
            resources = await client.list_resources()
            resource_uris = [str(resource.uri) for resource in resources]
            assert "working://working/data" in resource_uris

            # Test prompts
            prompts = await client.list_prompts()
            prompt_names = [prompt.name for prompt in prompts]
            assert "working_working_prompt" in prompt_names

        # Verify that errors were logged for the unreachable provider (at DEBUG level)
        debug_messages = [
            record.message for record in caplog.records if record.levelname == "DEBUG"
        ]
        assert any(
            "Error during list_tools from provider" in msg for msg in debug_messages
        )
        assert any(
            "Error during list_resources from provider" in msg for msg in debug_messages
        )
        assert any(
            "Error during list_prompts from provider" in msg for msg in debug_messages
        )


class TestPrefixConflictResolution:
    """Test that first registered provider wins when there are conflicts.

    Provider semantics: 'Providers are queried in registration order; first non-None wins'
    """

    async def test_first_server_wins_tools_no_prefix(self):
        """Test that first mounted server wins for tools when no prefix is used."""
        main_app = FastMCP("MainApp")
        first_app = FastMCP("FirstApp")
        second_app = FastMCP("SecondApp")

        @first_app.tool(name="shared_tool")
        def first_shared_tool() -> str:
            return "First app tool"

        @second_app.tool(name="shared_tool")
        def second_shared_tool() -> str:
            return "Second app tool"

        # Mount both apps without prefix
        main_app.mount(first_app)
        main_app.mount(second_app)

        # list_tools returns all components; execution uses first match
        tools = await main_app.list_tools()
        tool_names = [t.name for t in tools]
        assert "shared_tool" in tool_names

        # Test that calling the tool uses the first server's implementation
        result = await main_app.call_tool("shared_tool", {})
        assert result.structured_content == {"result": "First app tool"}

    async def test_first_server_wins_tools_same_prefix(self):
        """Test that first mounted server wins for tools when same prefix is used."""
        main_app = FastMCP("MainApp")
        first_app = FastMCP("FirstApp")
        second_app = FastMCP("SecondApp")

        @first_app.tool(name="shared_tool")
        def first_shared_tool() -> str:
            return "First app tool"

        @second_app.tool(name="shared_tool")
        def second_shared_tool() -> str:
            return "Second app tool"

        # Mount both apps with same prefix
        main_app.mount(first_app, "api")
        main_app.mount(second_app, "api")

        # list_tools returns all components; execution uses first match
        tools = await main_app.list_tools()
        tool_names = [t.name for t in tools]
        assert "api_shared_tool" in tool_names

        # Test that calling the tool uses the first server's implementation
        result = await main_app.call_tool("api_shared_tool", {})
        assert result.structured_content == {"result": "First app tool"}

    async def test_first_server_wins_resources_no_prefix(self):
        """Test that first mounted server wins for resources when no prefix is used."""
        main_app = FastMCP("MainApp")
        first_app = FastMCP("FirstApp")
        second_app = FastMCP("SecondApp")

        @first_app.resource(uri="shared://data")
        def first_resource():
            return "First app data"

        @second_app.resource(uri="shared://data")
        def second_resource():
            return "Second app data"

        # Mount both apps without prefix
        main_app.mount(first_app)
        main_app.mount(second_app)

        # list_resources returns all components; execution uses first match
        resources = await main_app.list_resources()
        resource_uris = [str(r.uri) for r in resources]
        assert "shared://data" in resource_uris

        # Test that reading the resource uses the first server's implementation
        result = await main_app.read_resource("shared://data")
        assert result.contents[0].content == "First app data"

    async def test_first_server_wins_resources_same_prefix(self):
        """Test that first mounted server wins for resources when same prefix is used."""
        main_app = FastMCP("MainApp")
        first_app = FastMCP("FirstApp")
        second_app = FastMCP("SecondApp")

        @first_app.resource(uri="shared://data")
        def first_resource():
            return "First app data"

        @second_app.resource(uri="shared://data")
        def second_resource():
            return "Second app data"

        # Mount both apps with same prefix
        main_app.mount(first_app, "api")
        main_app.mount(second_app, "api")

        # list_resources returns all components; execution uses first match
        resources = await main_app.list_resources()
        resource_uris = [str(r.uri) for r in resources]
        assert "shared://api/data" in resource_uris

        # Test that reading the resource uses the first server's implementation
        result = await main_app.read_resource("shared://api/data")
        assert result.contents[0].content == "First app data"

    async def test_first_server_wins_resource_templates_no_prefix(self):
        """Test that first mounted server wins for resource templates when no prefix is used."""
        main_app = FastMCP("MainApp")
        first_app = FastMCP("FirstApp")
        second_app = FastMCP("SecondApp")

        @first_app.resource(uri="users://{user_id}/profile")
        def first_template(user_id: str):
            return f"First app user {user_id}"

        @second_app.resource(uri="users://{user_id}/profile")
        def second_template(user_id: str):
            return f"Second app user {user_id}"

        # Mount both apps without prefix
        main_app.mount(first_app)
        main_app.mount(second_app)

        # list_resource_templates returns all components; execution uses first match
        templates = await main_app.list_resource_templates()
        template_uris = [t.uri_template for t in templates]
        assert "users://{user_id}/profile" in template_uris

        # Test that reading the resource uses the first server's implementation
        result = await main_app.read_resource("users://123/profile")
        assert result.contents[0].content == "First app user 123"

    async def test_first_server_wins_resource_templates_same_prefix(self):
        """Test that first mounted server wins for resource templates when same prefix is used."""
        main_app = FastMCP("MainApp")
        first_app = FastMCP("FirstApp")
        second_app = FastMCP("SecondApp")

        @first_app.resource(uri="users://{user_id}/profile")
        def first_template(user_id: str):
            return f"First app user {user_id}"

        @second_app.resource(uri="users://{user_id}/profile")
        def second_template(user_id: str):
            return f"Second app user {user_id}"

        # Mount both apps with same prefix
        main_app.mount(first_app, "api")
        main_app.mount(second_app, "api")

        # list_resource_templates returns all components; execution uses first match
        templates = await main_app.list_resource_templates()
        template_uris = [t.uri_template for t in templates]
        assert "users://api/{user_id}/profile" in template_uris

        # Test that reading the resource uses the first server's implementation
        result = await main_app.read_resource("users://api/123/profile")
        assert result.contents[0].content == "First app user 123"

    async def test_first_server_wins_prompts_no_prefix(self):
        """Test that first mounted server wins for prompts when no prefix is used."""
        main_app = FastMCP("MainApp")
        first_app = FastMCP("FirstApp")
        second_app = FastMCP("SecondApp")

        @first_app.prompt(name="shared_prompt")
        def first_shared_prompt() -> str:
            return "First app prompt"

        @second_app.prompt(name="shared_prompt")
        def second_shared_prompt() -> str:
            return "Second app prompt"

        # Mount both apps without prefix
        main_app.mount(first_app)
        main_app.mount(second_app)

        # list_prompts returns all components; execution uses first match
        prompts = await main_app.list_prompts()
        prompt_names = [p.name for p in prompts]
        assert "shared_prompt" in prompt_names

        # Test that getting the prompt uses the first server's implementation
        result = await main_app.render_prompt("shared_prompt")
        assert result.messages is not None
        assert isinstance(result.messages[0].content, TextContent)
        assert result.messages[0].content.text == "First app prompt"

    async def test_first_server_wins_prompts_same_prefix(self):
        """Test that first mounted server wins for prompts when same prefix is used."""
        main_app = FastMCP("MainApp")
        first_app = FastMCP("FirstApp")
        second_app = FastMCP("SecondApp")

        @first_app.prompt(name="shared_prompt")
        def first_shared_prompt() -> str:
            return "First app prompt"

        @second_app.prompt(name="shared_prompt")
        def second_shared_prompt() -> str:
            return "Second app prompt"

        # Mount both apps with same prefix
        main_app.mount(first_app, "api")
        main_app.mount(second_app, "api")

        # list_prompts returns all components; execution uses first match
        prompts = await main_app.list_prompts()
        prompt_names = [p.name for p in prompts]
        assert "api_shared_prompt" in prompt_names

        # Test that getting the prompt uses the first server's implementation
        result = await main_app.render_prompt("api_shared_prompt")
        assert result.messages is not None
        assert isinstance(result.messages[0].content, TextContent)
        assert result.messages[0].content.text == "First app prompt"
