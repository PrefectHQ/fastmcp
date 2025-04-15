import contextlib

import pytest

from fastmcp.server.server import FastMCP


async def test_mount_basic_functionality():
    """Test that the mount method properly imports tools and other resources."""
    # Create main app and sub-app
    main_app = FastMCP("MainApp")
    sub_app = FastMCP("SubApp")

    # Add a tool to the sub-app
    @sub_app.tool()
    def sub_tool() -> str:
        return "This is from the sub app"

    # Mount the sub-app to the main app
    main_app.mount("sub", sub_app)

    # Verify the tool was imported with the prefix
    assert "sub_sub_tool" in main_app._tool_manager._tools
    assert "sub_tool" in sub_app._tool_manager._tools

    # Verify the original tool still exists in the sub-app
    tool = main_app._tool_manager.get_tool("sub_sub_tool")
    assert tool is not None
    assert tool.name == "sub_tool"
    assert callable(tool.fn)


async def test_mount_multiple_apps():
    """Test mounting multiple apps to a main app."""
    # Create main app and multiple sub-apps
    main_app = FastMCP("MainApp")
    weather_app = FastMCP("WeatherApp")
    news_app = FastMCP("NewsApp")

    # Add tools to each sub-app
    @weather_app.tool()
    def get_forecast() -> str:
        return "Weather forecast"

    @news_app.tool()
    def get_headlines() -> str:
        return "News headlines"

    # Mount both sub-apps to the main app
    main_app.mount("weather", weather_app)
    main_app.mount("news", news_app)

    # Verify tools were imported with the correct prefixes
    assert "weather_get_forecast" in main_app._tool_manager._tools
    assert "news_get_headlines" in main_app._tool_manager._tools


async def test_mount_combines_tools():
    """Test that mounting preserves existing tools with the same prefix."""
    # Create apps
    main_app = FastMCP("MainApp")
    first_app = FastMCP("FirstApp")
    second_app = FastMCP("SecondApp")

    # Add tools to each sub-app
    @first_app.tool()
    def first_tool() -> str:
        return "First app tool"

    @second_app.tool()
    def second_tool() -> str:
        return "Second app tool"

    # Mount first app
    main_app.mount("api", first_app)
    assert "api_first_tool" in main_app._tool_manager._tools

    # Mount second app to same prefix
    main_app.mount("api", second_app)

    # Verify second tool is there
    assert "api_second_tool" in main_app._tool_manager._tools

    # Tools from both mounts are combined
    assert "api_first_tool" in main_app._tool_manager._tools


async def test_mount_with_resources():
    """Test mounting with resources."""
    # Create apps
    main_app = FastMCP("MainApp")
    data_app = FastMCP("DataApp")

    # Add a resource to the data app
    @data_app.resource(uri="data://users")
    async def get_users():
        return ["user1", "user2"]

    # Mount the data app
    main_app.mount("data", data_app)

    # Verify the resource was imported with the prefix
    assert "data+data://users" in main_app._resource_manager._resources


async def test_mount_with_resource_templates():
    """Test mounting with resource templates."""
    # Create apps
    main_app = FastMCP("MainApp")
    user_app = FastMCP("UserApp")

    # Add a resource template to the user app
    @user_app.resource(uri="users://{user_id}/profile")
    def get_user_profile(user_id: str) -> dict:
        return {"id": user_id, "name": f"User {user_id}"}

    # Mount the user app
    main_app.mount("api", user_app)

    # Verify the template was imported with the prefix
    assert "api+users://{user_id}/profile" in main_app._resource_manager._templates


async def test_mount_with_prompts():
    """Test mounting with prompts."""
    # Create apps
    main_app = FastMCP("MainApp")
    assistant_app = FastMCP("AssistantApp")

    # Add a prompt to the assistant app
    @assistant_app.prompt()
    def greeting(name: str) -> str:
        return f"Hello, {name}!"

    # Mount the assistant app
    main_app.mount("assistant", assistant_app)

    # Verify the prompt was imported with the prefix
    assert "assistant_greeting" in main_app._prompt_manager._prompts


async def test_mount_multiple_resource_templates():
    """Test mounting multiple apps with resource templates."""
    # Create apps
    main_app = FastMCP("MainApp")
    weather_app = FastMCP("WeatherApp")
    news_app = FastMCP("NewsApp")

    # Add templates to each app
    @weather_app.resource(uri="weather://{city}")
    def get_weather(city: str) -> str:
        return f"Weather for {city}"

    @news_app.resource(uri="news://{category}")
    def get_news(category: str) -> str:
        return f"News for {category}"

    # Mount both apps
    main_app.mount("data", weather_app)
    main_app.mount("content", news_app)

    # Verify templates were imported with correct prefixes
    assert "data+weather://{city}" in main_app._resource_manager._templates
    assert "content+news://{category}" in main_app._resource_manager._templates


async def test_mount_multiple_prompts():
    """Test mounting multiple apps with prompts."""
    # Create apps
    main_app = FastMCP("MainApp")
    python_app = FastMCP("PythonApp")
    sql_app = FastMCP("SQLApp")

    # Add prompts to each app
    @python_app.prompt()
    def review_python(code: str) -> str:
        return f"Reviewing Python code:\n{code}"

    @sql_app.prompt()
    def explain_sql(query: str) -> str:
        return f"Explaining SQL query:\n{query}"

    # Mount both apps
    main_app.mount("python", python_app)
    main_app.mount("sql", sql_app)

    # Verify prompts were imported with correct prefixes
    assert "python_review_python" in main_app._prompt_manager._prompts
    assert "sql_explain_sql" in main_app._prompt_manager._prompts


@pytest.mark.anyio
async def test_mount_lifespan():
    """Test that the lifespan of a mounted app is properly handled."""
    # Create apps

    lifespan_checkpoints = []

    @contextlib.asynccontextmanager
    async def lifespan(app: FastMCP):
        lifespan_checkpoints.append(f"enter {app.name}")
        try:
            yield
        finally:
            lifespan_checkpoints.append(f"exit {app.name}")

    main_app = FastMCP("MainApp", lifespan=lifespan)
    sub_app = FastMCP("SubApp", lifespan=lifespan)
    sub_app_2 = FastMCP("SubApp2", lifespan=lifespan)

    main_app.mount("sub", sub_app)
    main_app.mount("sub2", sub_app_2)

    low_level_server = main_app._mcp_server
    async with contextlib.AsyncExitStack() as stack:
        # Note: this imitates the way that lifespans are entered for mounted
        # apps It is presently difficult to stop a running server
        # programmatically without error in order to test the exit conditions,
        # so this is the next best thing
        await stack.enter_async_context(low_level_server.lifespan(low_level_server))
        assert lifespan_checkpoints == [
            "enter MainApp",
            "enter SubApp",
            "enter SubApp2",
        ]
    assert lifespan_checkpoints == [
        "enter MainApp",
        "enter SubApp",
        "enter SubApp2",
        "exit SubApp2",
        "exit SubApp",
        "exit MainApp",
    ]


async def test_mount_with_proxy_tools():
    """Test mounting with tools that have custom names (proxy tools)."""
    # Create apps
    main_app = FastMCP("MainApp")
    api_app = FastMCP("APIApp")

    # Create a tool function
    def fetch_data(query: str) -> str:
        return f"Data for query: {query}"

    # Add the tool to the API app with a custom name
    api_app.add_tool(fetch_data, name="get_data")

    # Verify the tool is registered with the custom name in the source app
    assert api_app._tool_manager.get_tool("get_data") is not None

    # Mount the API app to the main app
    main_app.mount("api", api_app)

    # Verify the tool was imported with the prefixed custom name
    tool = main_app._tool_manager.get_tool("api_get_data")
    assert tool is not None

    # The internal function name should be preserved
    assert tool.fn.__name__ == "fetch_data"

    # The tool should be callable through the mounted name
    context = main_app.get_context()
    result = await main_app._tool_manager.call_tool(
        "api_get_data", {"query": "test"}, context=context
    )
    assert result == "Data for query: test"


async def test_mount_nested_prefixed_tools():
    """Test mounting tools with multiple layers of prefixes."""
    # Create apps
    main_app = FastMCP("MainApp")
    service_app = FastMCP("ServiceApp")
    provider_app = FastMCP("ProviderApp")

    # Create a tool function
    def calculate_value(input: int) -> int:
        return input * 2

    # Add the tool to the provider app with a custom name
    provider_app.add_tool(calculate_value, name="compute")

    # The provider has a tool registered with a custom name
    assert provider_app._tool_manager.get_tool("compute") is not None

    # First mount: Mount the provider app to the service app
    service_app.mount("provider", provider_app)

    # Verify the tool is accessible in the service app with the first prefix
    assert service_app._tool_manager.get_tool("provider_compute") is not None

    # Second mount: Mount the service app to the main app
    main_app.mount("service", service_app)

    # Verify the tool is accessible in the main app with both prefixes
    nested_tool = main_app._tool_manager.get_tool("service_provider_compute")
    assert nested_tool is not None

    # The internal function name should still be preserved after multiple mounts
    assert nested_tool.fn.__name__ == "calculate_value"

    # The tool should be callable through the fully-qualified name
    context = main_app.get_context()
    result = await main_app._tool_manager.call_tool(
        "service_provider_compute", {"input": 21}, context=context
    )
    assert result == 42
