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
    tool = main_app._tool_manager._tools["sub_sub_tool"]
    assert tool.name == "sub_sub_tool"
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
