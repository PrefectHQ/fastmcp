"""Tests for Docket-style dependency injection in FastMCP."""

from contextlib import asynccontextmanager, contextmanager

import pytest
from mcp.types import TextContent, TextResourceContents

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.dependencies import CurrentContext, Depends
from fastmcp.server.context import Context

HUZZAH = "huzzah!"


class Connection:
    """Test connection that tracks whether it's currently open."""

    def __init__(self):
        self.is_open = False

    async def __aenter__(self):
        self.is_open = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.is_open = False


@asynccontextmanager
async def get_connection():
    """Dependency that provides an open connection."""
    async with Connection() as conn:
        yield conn


@pytest.fixture
def mcp():
    """Create a FastMCP server for testing."""
    return FastMCP("test-server")


async def test_depends_with_sync_function(mcp: FastMCP):
    """Test that Depends works with sync dependency functions."""

    def get_config() -> dict[str, str]:
        return {"api_key": "secret123", "endpoint": "https://api.example.com"}

    @mcp.tool()
    def fetch_data(query: str, config: dict[str, str] = Depends(get_config)) -> str:
        return (
            f"Fetching '{query}' from {config['endpoint']} with key {config['api_key']}"
        )

    async with Client(mcp) as client:
        result = await client.call_tool("fetch_data", {"query": "users"})
        assert len(result.content) == 1
        content = result.content[0]
        assert isinstance(content, TextContent)
        assert "Fetching 'users' from https://api.example.com" in content.text
        assert "secret123" in content.text


async def test_depends_with_async_function(mcp: FastMCP):
    """Test that Depends works with async dependency functions."""

    async def get_user_id() -> int:
        return 42

    @mcp.tool()
    async def greet_user(name: str, user_id: int = Depends(get_user_id)) -> str:
        return f"Hello {name}, your ID is {user_id}"

    async with Client(mcp) as client:
        result = await client.call_tool("greet_user", {"name": "Alice"})
        assert len(result.content) == 1
        content = result.content[0]
        assert isinstance(content, TextContent)
        assert content.text == "Hello Alice, your ID is 42"


async def test_depends_with_async_context_manager(mcp: FastMCP):
    """Test that Depends works with async context managers for resource management."""
    cleanup_called = False

    @asynccontextmanager
    async def get_database():
        db = "db_connection"
        try:
            yield db
        finally:
            nonlocal cleanup_called
            cleanup_called = True

    @mcp.tool()
    async def query_db(sql: str, db: str = Depends(get_database)) -> str:
        return f"Executing '{sql}' on {db}"

    async with Client(mcp) as client:
        result = await client.call_tool("query_db", {"sql": "SELECT * FROM users"})
        assert len(result.content) == 1
        content = result.content[0]
        assert isinstance(content, TextContent)
        assert "Executing 'SELECT * FROM users' on db_connection" in content.text
        assert cleanup_called


async def test_nested_dependencies(mcp: FastMCP):
    """Test that dependencies can depend on other dependencies."""

    def get_base_url() -> str:
        return "https://api.example.com"

    def get_api_client(base_url: str = Depends(get_base_url)) -> dict[str, str]:
        return {"base_url": base_url, "version": "v1"}

    @mcp.tool()
    async def call_api(
        endpoint: str, client: dict[str, str] = Depends(get_api_client)
    ) -> str:
        return f"Calling {client['base_url']}/{client['version']}/{endpoint}"

    async with Client(mcp) as client:
        result = await client.call_tool("call_api", {"endpoint": "users"})
        assert len(result.content) == 1
        content = result.content[0]
        assert isinstance(content, TextContent)
        assert content.text == "Calling https://api.example.com/v1/users"


async def test_dependencies_excluded_from_schema(mcp: FastMCP):
    """Test that dependency parameters don't appear in the tool schema."""

    def get_config() -> dict[str, str]:
        return {"key": "value"}

    @mcp.tool()
    async def my_tool(
        name: str, age: int, config: dict[str, str] = Depends(get_config)
    ) -> str:
        return f"{name} is {age} years old"

    tools = await mcp._list_tools_mcp()
    tool = next(t for t in tools if t.name == "my_tool")

    assert "name" in tool.inputSchema["properties"]
    assert "age" in tool.inputSchema["properties"]
    assert "config" not in tool.inputSchema["properties"]
    assert len(tool.inputSchema["properties"]) == 2


async def test_current_context_dependency(mcp: FastMCP):
    """Test that CurrentContext dependency provides access to FastMCP Context."""

    @mcp.tool()
    def use_context(ctx: Context = CurrentContext()) -> str:
        assert isinstance(ctx, Context)
        return HUZZAH

    async with Client(mcp) as client:
        result = await client.call_tool("use_context", {})
        assert HUZZAH in str(result)


async def test_current_context_and_legacy_context_coexist(mcp: FastMCP):
    """Test that CurrentContext dependency and legacy Context injection work together."""

    @mcp.tool()
    def use_both_contexts(
        legacy_ctx: Context,
        dep_ctx: Context = CurrentContext(),
    ) -> str:
        assert isinstance(legacy_ctx, Context)
        assert isinstance(dep_ctx, Context)
        assert legacy_ctx is dep_ctx
        return HUZZAH

    async with Client(mcp) as client:
        result = await client.call_tool("use_both_contexts", {})
        assert HUZZAH in str(result)


async def test_backward_compat_context_still_works(mcp: FastMCP):
    """Test that existing Context injection via type annotation still works."""

    @mcp.tool()
    async def get_request_id(ctx: Context) -> str:
        return ctx.request_id

    async with Client(mcp) as client:
        result = await client.call_tool("get_request_id", {})
        assert len(result.content) == 1
        content = result.content[0]
        assert isinstance(content, TextContent)
        assert len(content.text) > 0


async def test_sync_tool_with_async_dependency(mcp: FastMCP):
    """Test that sync tools work with async dependencies."""

    async def fetch_config() -> str:
        return "loaded_config"

    @mcp.tool()
    def process_data(value: int, config: str = Depends(fetch_config)) -> str:
        return f"Processing {value} with {config}"

    async with Client(mcp) as client:
        result = await client.call_tool("process_data", {"value": 100})
        assert len(result.content) == 1
        content = result.content[0]
        assert isinstance(content, TextContent)
        assert content.text == "Processing 100 with loaded_config"


async def test_dependency_caching(mcp: FastMCP):
    """Test that dependencies are cached within a single tool call."""
    call_count = 0

    def expensive_dependency() -> int:
        nonlocal call_count
        call_count += 1
        return 42

    @mcp.tool()
    async def tool_with_cached_dep(
        dep1: int = Depends(expensive_dependency),
        dep2: int = Depends(expensive_dependency),
    ) -> str:
        return f"{dep1} + {dep2} = {dep1 + dep2}"

    async with Client(mcp) as client:
        result = await client.call_tool("tool_with_cached_dep", {})
        assert len(result.content) == 1
        content = result.content[0]
        assert isinstance(content, TextContent)
        assert content.text == "42 + 42 = 84"
        assert call_count == 1


async def test_context_and_depends_together(mcp: FastMCP):
    """Test that Context type injection and Depends can be used together."""

    def get_multiplier() -> int:
        return 10

    @mcp.tool()
    async def mixed_deps(
        value: int, ctx: Context, multiplier: int = Depends(get_multiplier)
    ) -> str:
        assert isinstance(ctx, Context)
        assert ctx.request_id
        assert len(ctx.request_id) > 0
        return (
            f"Request {ctx.request_id}: {value} * {multiplier} = {value * multiplier}"
        )

    async with Client(mcp) as client:
        result = await client.call_tool("mixed_deps", {"value": 5})
        assert len(result.content) == 1
        content = result.content[0]
        assert isinstance(content, TextContent)
        assert "5 * 10 = 50" in content.text
        assert "Request " in content.text


async def test_resource_with_dependency(mcp: FastMCP):
    """Test that resources support dependency injection."""

    def get_storage_path() -> str:
        return "/data/config"

    @mcp.resource("config://settings")
    async def get_settings(storage: str = Depends(get_storage_path)) -> str:
        return f"Settings loaded from {storage}"

    async with Client(mcp) as client:
        result = await client.read_resource("config://settings")
        assert len(result) == 1
        content = result[0]
        assert isinstance(content, TextResourceContents)
        assert content.text == "Settings loaded from /data/config"


async def test_resource_with_context_and_dependency(mcp: FastMCP):
    """Test that resources can use both Context and Depends."""

    def get_prefix() -> str:
        return "DATA"

    @mcp.resource("config://info")
    async def get_info(ctx: Context, prefix: str = Depends(get_prefix)) -> str:
        return f"{prefix}: Request {ctx.request_id}"

    async with Client(mcp) as client:
        result = await client.read_resource("config://info")
        assert len(result) == 1
        content = result[0]
        assert isinstance(content, TextResourceContents)
        assert "DATA: Request " in content.text
        assert len(content.text.split("Request ")[1]) > 0


async def test_prompt_with_dependency(mcp: FastMCP):
    """Test that prompts support dependency injection."""

    def get_tone() -> str:
        return "friendly and helpful"

    @mcp.prompt()
    async def custom_prompt(topic: str, tone: str = Depends(get_tone)) -> str:
        return f"Write about {topic} in a {tone} tone"

    async with Client(mcp) as client:
        result = await client.get_prompt("custom_prompt", {"topic": "Python"})
        assert len(result.messages) == 1
        message = result.messages[0]
        content = message.content
        assert isinstance(content, TextContent)
        assert content.text == "Write about Python in a friendly and helpful tone"


async def test_prompt_with_context_and_dependency(mcp: FastMCP):
    """Test that prompts can use both Context and Depends."""

    def get_style() -> str:
        return "concise"

    @mcp.prompt()
    async def styled_prompt(
        query: str, ctx: Context, style: str = Depends(get_style)
    ) -> str:
        assert isinstance(ctx, Context)
        assert ctx.request_id
        return f"Answer '{query}' in a {style} style"

    async with Client(mcp) as client:
        result = await client.get_prompt("styled_prompt", {"query": "What is MCP?"})
        assert len(result.messages) == 1
        message = result.messages[0]
        content = message.content
        assert isinstance(content, TextContent)
        assert content.text == "Answer 'What is MCP?' in a concise style"


async def test_resource_template_with_dependency(mcp: FastMCP):
    """Test that resource templates support dependency injection."""

    def get_base_path() -> str:
        return "/var/data"

    @mcp.resource("data://{filename}")
    async def get_file(filename: str, base_path: str = Depends(get_base_path)) -> str:
        return f"Reading {base_path}/{filename}"

    async with Client(mcp) as client:
        result = await client.read_resource("data://config.txt")
        assert len(result) == 1
        content = result[0]
        assert isinstance(content, TextResourceContents)
        assert content.text == "Reading /var/data/config.txt"


async def test_resource_template_with_context_and_dependency(mcp: FastMCP):
    """Test that resource templates can use both Context and Depends."""

    def get_version() -> str:
        return "v2"

    @mcp.resource("api://{endpoint}")
    async def call_endpoint(
        endpoint: str, ctx: Context, version: str = Depends(get_version)
    ) -> str:
        assert isinstance(ctx, Context)
        assert ctx.request_id
        return f"Calling {version}/{endpoint}"

    async with Client(mcp) as client:
        result = await client.read_resource("api://users")
        assert len(result) == 1
        content = result[0]
        assert isinstance(content, TextResourceContents)
        assert content.text == "Calling v2/users"


async def test_async_tool_context_manager_stays_open(mcp: FastMCP):
    """Test that context manager dependencies stay open during async tool execution.

    Context managers must remain open while the async function executes, not just
    while it's being called (which only returns a coroutine).
    """

    @mcp.tool()
    async def query_data(
        query: str, connection: Connection = Depends(get_connection)
    ) -> str:
        assert connection.is_open
        return f"open={connection.is_open}"

    async with Client(mcp) as client:
        result = await client.call_tool("query_data", {"query": "test"})
        content = result.content[0]
        assert isinstance(content, TextContent)
        assert content.text == "open=True"


async def test_async_resource_context_manager_stays_open(mcp: FastMCP):
    """Test that context manager dependencies stay open during async resource execution."""

    @mcp.resource("data://config")
    async def load_config(connection: Connection = Depends(get_connection)) -> str:
        assert connection.is_open
        return f"open={connection.is_open}"

    async with Client(mcp) as client:
        result = await client.read_resource("data://config")
        content = result[0]
        assert isinstance(content, TextResourceContents)
        assert content.text == "open=True"


async def test_async_resource_template_context_manager_stays_open(mcp: FastMCP):
    """Test that context manager dependencies stay open during async resource template execution."""

    @mcp.resource("user://{user_id}")
    async def get_user(
        user_id: str, connection: Connection = Depends(get_connection)
    ) -> str:
        assert connection.is_open
        return f"open={connection.is_open},user={user_id}"

    async with Client(mcp) as client:
        result = await client.read_resource("user://123")
        content = result[0]
        assert isinstance(content, TextResourceContents)
        assert "open=True" in content.text


async def test_async_prompt_context_manager_stays_open(mcp: FastMCP):
    """Test that context manager dependencies stay open during async prompt execution."""

    @mcp.prompt()
    async def research_prompt(
        topic: str, connection: Connection = Depends(get_connection)
    ) -> str:
        assert connection.is_open
        return f"open={connection.is_open},topic={topic}"

    async with Client(mcp) as client:
        result = await client.get_prompt("research_prompt", {"topic": "AI"})
        message = result.messages[0]
        content = message.content
        assert isinstance(content, TextContent)
        assert "open=True" in content.text


async def test_argument_validation_with_dependencies(mcp: FastMCP):
    """Test that user arguments are still validated when dependencies are present."""

    def get_config() -> dict[str, str]:
        return {"key": "value"}

    @mcp.tool()
    async def validated_tool(
        age: int,  # Should validate type
        config: dict[str, str] = Depends(get_config),
    ) -> str:
        return f"age={age}"

    async with Client(mcp) as client:
        # Valid argument
        result = await client.call_tool("validated_tool", {"age": 25})
        content = result.content[0]
        assert isinstance(content, TextContent)
        assert content.text == "age=25"

        # Invalid argument type should fail validation
        with pytest.raises(Exception):  # Will be ToolError wrapping validation error
            await client.call_tool("validated_tool", {"age": "not a number"})


async def test_connection_dependency_excluded_from_tool_schema(mcp: FastMCP):
    """Test that Connection dependency parameter is excluded from tool schema."""

    @mcp.tool()
    async def with_connection(
        name: str, connection: Connection = Depends(get_connection)
    ) -> str:
        return name

    tools = await mcp._list_tools_mcp()
    tool = next(t for t in tools if t.name == "with_connection")

    assert "name" in tool.inputSchema["properties"]
    assert "connection" not in tool.inputSchema["properties"]


async def test_sync_tool_context_manager_stays_open(mcp: FastMCP):
    """Test that sync context manager dependencies work with tools."""
    conn = Connection()

    @contextmanager
    def get_sync_connection():
        conn.is_open = True
        try:
            yield conn
        finally:
            conn.is_open = False

    @mcp.tool()
    async def query_sync(
        query: str, connection: Connection = Depends(get_sync_connection)
    ) -> str:
        assert connection.is_open
        return f"open={connection.is_open}"

    async with Client(mcp) as client:
        result = await client.call_tool("query_sync", {"query": "test"})
        content = result.content[0]
        assert isinstance(content, TextContent)
        assert content.text == "open=True"
        assert not conn.is_open


async def test_sync_resource_context_manager_stays_open(mcp: FastMCP):
    """Test that sync context manager dependencies work with resources."""
    conn = Connection()

    @contextmanager
    def get_sync_connection():
        conn.is_open = True
        try:
            yield conn
        finally:
            conn.is_open = False

    @mcp.resource("data://sync")
    async def load_sync(connection: Connection = Depends(get_sync_connection)) -> str:
        assert connection.is_open
        return f"open={connection.is_open}"

    async with Client(mcp) as client:
        result = await client.read_resource("data://sync")
        content = result[0]
        assert isinstance(content, TextResourceContents)
        assert content.text == "open=True"
        assert not conn.is_open


async def test_sync_resource_template_context_manager_stays_open(mcp: FastMCP):
    """Test that sync context manager dependencies work with resource templates."""
    conn = Connection()

    @contextmanager
    def get_sync_connection():
        conn.is_open = True
        try:
            yield conn
        finally:
            conn.is_open = False

    @mcp.resource("item://{item_id}")
    async def get_item(
        item_id: str, connection: Connection = Depends(get_sync_connection)
    ) -> str:
        assert connection.is_open
        return f"open={connection.is_open},item={item_id}"

    async with Client(mcp) as client:
        result = await client.read_resource("item://456")
        content = result[0]
        assert isinstance(content, TextResourceContents)
        assert "open=True" in content.text
        assert not conn.is_open


async def test_sync_prompt_context_manager_stays_open(mcp: FastMCP):
    """Test that sync context manager dependencies work with prompts."""
    conn = Connection()

    @contextmanager
    def get_sync_connection():
        conn.is_open = True
        try:
            yield conn
        finally:
            conn.is_open = False

    @mcp.prompt()
    async def sync_prompt(
        topic: str, connection: Connection = Depends(get_sync_connection)
    ) -> str:
        assert connection.is_open
        return f"open={connection.is_open},topic={topic}"

    async with Client(mcp) as client:
        result = await client.get_prompt("sync_prompt", {"topic": "test"})
        message = result.messages[0]
        content = message.content
        assert isinstance(content, TextContent)
        assert "open=True" in content.text
        assert not conn.is_open


async def test_external_user_cannot_override_dependency(mcp: FastMCP):
    """Test that external MCP clients cannot override dependency parameters."""

    def get_admin_status() -> str:
        return "not_admin"

    @mcp.tool()
    async def check_permission(
        action: str, admin: str = Depends(get_admin_status)
    ) -> str:
        return f"action={action},admin={admin}"

    # Verify dependency is NOT in the schema
    tools = await mcp._list_tools_mcp()
    tool = next(t for t in tools if t.name == "check_permission")
    assert "admin" not in tool.inputSchema["properties"]

    async with Client(mcp) as client:
        # Normal call - dependency is resolved
        result = await client.call_tool("check_permission", {"action": "read"})
        content = result.content[0]
        assert isinstance(content, TextContent)
        assert "admin=not_admin" in content.text

        # Try to override dependency - rejected (not in schema)
        with pytest.raises(Exception):
            await client.call_tool(
                "check_permission", {"action": "read", "admin": "hacker"}
            )


async def test_prompt_dependency_cannot_be_overridden_externally(mcp: FastMCP):
    """Test that external callers cannot override prompt dependencies.

    This is a security test - dependencies should NEVER be overridable from
    outside the server, even for prompts which don't validate against strict schemas.
    """

    def get_secret() -> str:
        return "real_secret"

    @mcp.prompt()
    async def secure_prompt(topic: str, secret: str = Depends(get_secret)) -> str:
        return f"Topic: {topic}, Secret: {secret}"

    async with Client(mcp) as client:
        # Normal call - should use dependency
        result = await client.get_prompt("secure_prompt", {"topic": "test"})
        message = result.messages[0]
        content = message.content
        assert isinstance(content, TextContent)
        assert "Secret: real_secret" in content.text

        # Try to override dependency - should be ignored/rejected
        result = await client.get_prompt(
            "secure_prompt",
            {"topic": "test", "secret": "HACKED"},  # Attempt override
        )
        message = result.messages[0]
        content = message.content
        assert isinstance(content, TextContent)
        # Should still use real dependency, not hacked value
        assert "Secret: real_secret" in content.text
        assert "HACKED" not in content.text


async def test_resource_dependency_cannot_be_overridden_externally(mcp: FastMCP):
    """Test that external callers cannot override resource dependencies."""

    def get_api_key() -> str:
        return "real_api_key"

    @mcp.resource("data://config")
    async def get_config(api_key: str = Depends(get_api_key)) -> str:
        return f"API Key: {api_key}"

    async with Client(mcp) as client:
        # Normal call
        result = await client.read_resource("data://config")
        content = result[0]
        assert isinstance(content, TextResourceContents)
        assert "API Key: real_api_key" in content.text

        # Resources don't accept arguments from clients (static URI)
        # so this scenario is less of a concern, but documenting it


async def test_resource_template_dependency_cannot_be_overridden_externally(
    mcp: FastMCP,
):
    """Test that external callers cannot override resource template dependencies.

    Resource templates extract parameters from the URI path, so there's a risk
    that a dependency parameter name could match a URI parameter.
    """

    def get_auth_token() -> str:
        return "real_token"

    @mcp.resource("user://{user_id}")
    async def get_user(user_id: str, token: str = Depends(get_auth_token)) -> str:
        return f"User: {user_id}, Token: {token}"

    async with Client(mcp) as client:
        # Normal call
        result = await client.read_resource("user://123")
        content = result[0]
        assert isinstance(content, TextResourceContents)
        assert "User: 123, Token: real_token" in content.text

        # Try to inject token via URI (shouldn't be possible with this pattern)
        # But if URI was user://{token}, it could extract it


async def test_resource_template_uri_cannot_match_dependency_name(mcp: FastMCP):
    """Test that URI parameters cannot have the same name as dependencies.

    If a URI template tries to use a parameter name that's also a dependency,
    the template creation should fail because the dependency is excluded from
    the user-facing signature.
    """

    def get_token() -> str:
        return "real_token"

    # This should fail - {token} in URI but token is a dependency parameter
    with pytest.raises(ValueError, match="URI parameters.*must be a subset"):

        @mcp.resource("auth://{token}/validate")
        async def validate(token: str = Depends(get_token)) -> str:
            return f"Validating with: {token}"
