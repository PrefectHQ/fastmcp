"""Tests for Docket-style dependency injection in FastMCP."""

from contextlib import asynccontextmanager, contextmanager

import mcp.types as mcp_types
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

    result = await mcp.call_tool("fetch_data", {"query": "users"})
    assert result.structured_content is not None
    text = result.structured_content["result"]
    assert "Fetching 'users' from https://api.example.com" in text
    assert "secret123" in text


async def test_depends_with_async_function(mcp: FastMCP):
    """Test that Depends works with async dependency functions."""

    async def get_user_id() -> int:
        return 42

    @mcp.tool()
    async def greet_user(name: str, user_id: int = Depends(get_user_id)) -> str:  # type: ignore[assignment]
        return f"Hello {name}, your ID is {user_id}"

    result = await mcp.call_tool("greet_user", {"name": "Alice"})
    assert result.structured_content is not None
    assert result.structured_content["result"] == "Hello Alice, your ID is 42"


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
    async def query_db(sql: str, db: str = Depends(get_database)) -> str:  # type: ignore[assignment]
        return f"Executing '{sql}' on {db}"

    result = await mcp.call_tool("query_db", {"sql": "SELECT * FROM users"})
    assert result.structured_content is not None
    assert (
        "Executing 'SELECT * FROM users' on db_connection"
        in result.structured_content["result"]
    )
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

    result = await mcp.call_tool("call_api", {"endpoint": "users"})
    assert result.structured_content is not None
    assert (
        result.structured_content["result"]
        == "Calling https://api.example.com/v1/users"
    )


async def test_dependencies_excluded_from_schema(mcp: FastMCP):
    """Test that dependency parameters don't appear in the tool schema."""

    def get_config() -> dict[str, str]:
        return {"key": "value"}

    @mcp.tool()
    async def my_tool(
        name: str, age: int, config: dict[str, str] = Depends(get_config)
    ) -> str:
        return f"{name} is {age} years old"

    result = await mcp._list_tools_mcp(mcp_types.ListToolsRequest())
    tool = next(t for t in result.tools if t.name == "my_tool")

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

    result = await mcp.call_tool("use_context", {})
    assert result.structured_content is not None
    assert result.structured_content["result"] == HUZZAH


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

    result = await mcp.call_tool("use_both_contexts", {})
    assert result.structured_content is not None
    assert result.structured_content["result"] == HUZZAH


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
    def process_data(value: int, config: str = Depends(fetch_config)) -> str:  # type: ignore[assignment]
        return f"Processing {value} with {config}"

    result = await mcp.call_tool("process_data", {"value": 100})
    assert result.structured_content is not None
    assert result.structured_content["result"] == "Processing 100 with loaded_config"


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

    result = await mcp.call_tool("tool_with_cached_dep", {})
    assert result.structured_content is not None
    assert result.structured_content["result"] == "42 + 42 = 84"
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

    result = await mcp.read_resource("config://settings")
    assert len(result.contents) == 1
    assert result.contents[0].content == "Settings loaded from /data/config"


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

    result = await mcp.render_prompt("custom_prompt", {"topic": "Python"})
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

    result = await mcp.read_resource("data://config.txt")
    assert len(result.contents) == 1
    assert result.contents[0].content == "Reading /var/data/config.txt"


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
        query: str,
        connection: Connection = Depends(get_connection),  # type: ignore[assignment]
    ) -> str:
        assert connection.is_open
        return f"open={connection.is_open}"

    result = await mcp.call_tool("query_data", {"query": "test"})
    assert result.structured_content is not None
    assert result.structured_content["result"] == "open=True"


async def test_async_resource_context_manager_stays_open(mcp: FastMCP):
    """Test that context manager dependencies stay open during async resource execution."""

    @mcp.resource("data://config")
    async def load_config(connection: Connection = Depends(get_connection)) -> str:  # type: ignore[assignment]
        assert connection.is_open
        return f"open={connection.is_open}"

    result = await mcp.read_resource("data://config")
    assert result.contents[0].content == "open=True"


async def test_async_resource_template_context_manager_stays_open(mcp: FastMCP):
    """Test that context manager dependencies stay open during async resource template execution."""

    @mcp.resource("user://{user_id}")
    async def get_user(
        user_id: str,
        connection: Connection = Depends(get_connection),  # type: ignore[assignment]
    ) -> str:
        assert connection.is_open
        return f"open={connection.is_open},user={user_id}"

    result = await mcp.read_resource("user://123")
    assert isinstance(result.contents[0].content, str)
    assert "open=True" in result.contents[0].content


async def test_async_prompt_context_manager_stays_open(mcp: FastMCP):
    """Test that context manager dependencies stay open during async prompt execution."""

    @mcp.prompt()
    async def research_prompt(
        topic: str,
        connection: Connection = Depends(get_connection),  # type: ignore[assignment]
    ) -> str:
        assert connection.is_open
        return f"open={connection.is_open},topic={topic}"

    result = await mcp.render_prompt("research_prompt", {"topic": "AI"})
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

    # Valid argument
    result = await mcp.call_tool("validated_tool", {"age": 25})
    assert result.structured_content is not None
    assert result.structured_content["result"] == "age=25"

    # Invalid argument type should fail validation
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        await mcp.call_tool("validated_tool", {"age": "not a number"})


async def test_connection_dependency_excluded_from_tool_schema(mcp: FastMCP):
    """Test that Connection dependency parameter is excluded from tool schema."""

    @mcp.tool()
    async def with_connection(
        name: str,
        connection: Connection = Depends(get_connection),  # type: ignore[assignment]
    ) -> str:
        return name

    result = await mcp._list_tools_mcp(mcp_types.ListToolsRequest())
    tool = next(t for t in result.tools if t.name == "with_connection")

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
        query: str,
        connection: Connection = Depends(get_sync_connection),  # type: ignore[assignment]
    ) -> str:
        assert connection.is_open
        return f"open={connection.is_open}"

    result = await mcp.call_tool("query_sync", {"query": "test"})
    assert result.structured_content is not None
    assert result.structured_content["result"] == "open=True"
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
    async def load_sync(connection: Connection = Depends(get_sync_connection)) -> str:  # type: ignore[assignment]
        assert connection.is_open
        return f"open={connection.is_open}"

    result = await mcp.read_resource("data://sync")
    assert result.contents[0].content == "open=True"
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
        item_id: str,
        connection: Connection = Depends(get_sync_connection),  # type: ignore[assignment]
    ) -> str:
        assert connection.is_open
        return f"open={connection.is_open},item={item_id}"

    result = await mcp.read_resource("item://456")
    assert isinstance(result.contents[0].content, str)
    assert "open=True" in result.contents[0].content
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
        topic: str,
        connection: Connection = Depends(get_sync_connection),  # type: ignore[assignment]
    ) -> str:
        assert connection.is_open
        return f"open={connection.is_open},topic={topic}"

    result = await mcp.render_prompt("sync_prompt", {"topic": "test"})
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
    result = await mcp._list_tools_mcp(mcp_types.ListToolsRequest())
    tool = next(t for t in result.tools if t.name == "check_permission")
    assert "admin" not in tool.inputSchema["properties"]

    # Normal call - dependency is resolved
    result = await mcp.call_tool("check_permission", {"action": "read"})
    assert result.structured_content is not None
    assert "admin=not_admin" in result.structured_content["result"]

    # Try to override dependency - rejected (not in schema)
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        await mcp.call_tool("check_permission", {"action": "read", "admin": "hacker"})


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

    # Normal call - should use dependency
    result = await mcp.render_prompt("secure_prompt", {"topic": "test"})
    message = result.messages[0]
    content = message.content
    assert isinstance(content, TextContent)
    assert "Secret: real_secret" in content.text

    # Try to override dependency - should be ignored/rejected
    result = await mcp.render_prompt(
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

    # Normal call
    result = await mcp.read_resource("data://config")
    assert isinstance(result.contents[0].content, str)
    assert "API Key: real_api_key" in result.contents[0].content

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

    # Normal call
    result = await mcp.read_resource("user://123")
    assert isinstance(result.contents[0].content, str)
    assert "User: 123, Token: real_token" in result.contents[0].content

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


async def test_toolerror_propagates_from_dependency(mcp: FastMCP):
    """ToolError raised in a dependency should propagate unchanged (issue #2633).

    When a dependency raises ToolError, it should not be wrapped in RuntimeError.
    This allows developers to use ToolError for validation in dependencies.
    """
    from fastmcp.exceptions import ToolError

    def validate_client_id() -> str:
        raise ToolError("Client ID is required - select a client first")

    @mcp.tool()
    async def my_tool(client_id: str = Depends(validate_client_id)) -> str:
        return f"Working with client: {client_id}"

    async with Client(mcp) as client:
        # ToolError is converted to an error result by the server
        result = await client.call_tool("my_tool", {}, raise_on_error=False)
        assert result.is_error
        # The original error message should be preserved (not wrapped in RuntimeError)
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "Client ID is required - select a client first"


async def test_validation_error_propagates_from_dependency(mcp: FastMCP):
    """ValidationError raised in a dependency should propagate unchanged."""
    from fastmcp.exceptions import ValidationError

    def validate_input() -> str:
        raise ValidationError("Invalid input format")

    @mcp.tool()
    async def tool_with_validation(val: str = Depends(validate_input)) -> str:
        return val

    async with Client(mcp) as client:
        # ValidationError is re-raised by the server and becomes an error result
        # The original error message should be preserved (not wrapped in RuntimeError)
        result = await client.call_tool(
            "tool_with_validation", {}, raise_on_error=False
        )
        assert result.is_error
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "Invalid input format"


# --- Tests for transform_context_annotations ---


class TestTransformContextAnnotations:
    """Tests for the transform_context_annotations function."""

    async def test_basic_context_transformation(self, mcp: FastMCP):
        """Test basic Context type annotation is transformed."""

        @mcp.tool()
        async def tool_with_context(name: str, ctx: Context) -> str:
            return f"session={ctx.session_id}, name={name}"

        async with Client(mcp) as client:
            result = await client.call_tool("tool_with_context", {"name": "test"})
            assert "session=" in result.content[0].text
            assert "name=test" in result.content[0].text

    async def test_transform_with_var_params(self):
        """Test transform_context_annotations handles *args and **kwargs correctly."""
        import inspect

        from fastmcp.server.dependencies import transform_context_annotations

        # This function can't be a tool (FastMCP doesn't support *args/**kwargs),
        # but transform should handle it gracefully for signature inspection
        async def fn_with_var_params(
            first: str, ctx: Context, *args: str, **kwargs: str
        ) -> str:
            return f"first={first}"

        transform_context_annotations(fn_with_var_params)
        sig = inspect.signature(fn_with_var_params)

        # Verify structure is preserved
        param_kinds = {p.name: p.kind for p in sig.parameters.values()}
        assert param_kinds["first"] == inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert param_kinds["ctx"] == inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert param_kinds["args"] == inspect.Parameter.VAR_POSITIONAL
        assert param_kinds["kwargs"] == inspect.Parameter.VAR_KEYWORD

        # ctx should now have a default
        assert sig.parameters["ctx"].default is not inspect.Parameter.empty

    async def test_context_keyword_only(self, mcp: FastMCP):
        """Test Context transformation preserves keyword-only parameter semantics."""
        import inspect

        from fastmcp.server.dependencies import transform_context_annotations

        # Define function with keyword-only Context param
        async def fn_with_kw_only(a: str, *, ctx: Context, b: str = "default") -> str:
            return f"a={a}, b={b}"

        # Transform and check signature structure
        transform_context_annotations(fn_with_kw_only)
        sig = inspect.signature(fn_with_kw_only)
        params = list(sig.parameters.values())

        # 'a' should be POSITIONAL_OR_KEYWORD
        assert params[0].name == "a"
        assert params[0].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD

        # 'ctx' should still be KEYWORD_ONLY (after transformation)
        ctx_param = sig.parameters["ctx"]
        assert ctx_param.kind == inspect.Parameter.KEYWORD_ONLY

        # 'b' should still be KEYWORD_ONLY
        b_param = sig.parameters["b"]
        assert b_param.kind == inspect.Parameter.KEYWORD_ONLY

    async def test_context_with_annotated(self, mcp: FastMCP):
        """Test Context with Annotated type is transformed."""
        from typing import Annotated

        @mcp.tool()
        async def tool_with_annotated_ctx(
            name: str, ctx: Annotated[Context, "custom annotation"]
        ) -> str:
            return f"session={ctx.session_id}"

        async with Client(mcp) as client:
            result = await client.call_tool("tool_with_annotated_ctx", {"name": "test"})
            assert "session=" in result.content[0].text

    async def test_context_already_has_dependency_default(self, mcp: FastMCP):
        """Test that Context with existing Depends default is not re-transformed."""

        @mcp.tool()
        async def tool_with_explicit_context(
            name: str, ctx: Context = CurrentContext()
        ) -> str:
            return f"session={ctx.session_id}"

        async with Client(mcp) as client:
            result = await client.call_tool(
                "tool_with_explicit_context", {"name": "test"}
            )
            assert "session=" in result.content[0].text

    async def test_multiple_context_params(self, mcp: FastMCP):
        """Test multiple Context-typed parameters are all transformed."""

        @mcp.tool()
        async def tool_with_multiple_ctx(
            name: str, ctx1: Context, ctx2: Context
        ) -> str:
            # Both should refer to same context
            assert ctx1.session_id == ctx2.session_id
            return f"same={ctx1 is ctx2}"

        # Both ctx params should be excluded from schema
        result = await mcp._list_tools_mcp(mcp_types.ListToolsRequest())
        tool = next(t for t in result.tools if t.name == "tool_with_multiple_ctx")
        assert "name" in tool.inputSchema["properties"]
        assert "ctx1" not in tool.inputSchema["properties"]
        assert "ctx2" not in tool.inputSchema["properties"]

    async def test_context_in_class_method(self, mcp: FastMCP):
        """Test Context transformation works with bound methods."""

        class MyTools:
            def __init__(self, prefix: str):
                self.prefix = prefix

            async def greet(self, name: str, ctx: Context) -> str:
                return f"{self.prefix} {name}, session={ctx.session_id}"

        tools = MyTools("Hello")
        mcp.tool()(tools.greet)

        async with Client(mcp) as client:
            result = await client.call_tool("greet", {"name": "World"})
            assert "Hello World" in result.content[0].text
            assert "session=" in result.content[0].text

    async def test_context_in_static_method(self, mcp: FastMCP):
        """Test Context transformation works with static methods."""

        class MyTools:
            @staticmethod
            async def static_tool(name: str, ctx: Context) -> str:
                return f"name={name}, session={ctx.session_id}"

        mcp.tool()(MyTools.static_tool)

        async with Client(mcp) as client:
            result = await client.call_tool("static_tool", {"name": "test"})
            assert "name=test" in result.content[0].text
            assert "session=" in result.content[0].text

    async def test_context_in_callable_class(self, mcp: FastMCP):
        """Test Context transformation works with callable class instances."""
        from fastmcp.tools import Tool

        class CallableTool:
            def __init__(self, multiplier: int):
                self.multiplier = multiplier

            async def __call__(self, x: int, ctx: Context) -> str:
                return f"result={x * self.multiplier}, session={ctx.session_id}"

        # Use Tool.from_function directly (mcp.tool() decorator doesn't support callable instances)
        tool = Tool.from_function(CallableTool(3))
        mcp.add_tool(tool)

        async with Client(mcp) as client:
            result = await client.call_tool("CallableTool", {"x": 5})
            assert "result=15" in result.content[0].text
            assert "session=" in result.content[0].text

    async def test_context_param_reordering(self, mcp: FastMCP):
        """Test that Context params are reordered correctly to maintain valid signature."""
        import inspect

        from fastmcp.server.dependencies import transform_context_annotations

        # Context in middle without default - should be moved after non-default params
        async def fn_with_middle_ctx(a: str, ctx: Context, b: str) -> str:
            return f"{a},{b}"

        transform_context_annotations(fn_with_middle_ctx)
        sig = inspect.signature(fn_with_middle_ctx)
        params = list(sig.parameters.values())

        # After transform: a, b should come before ctx (which now has default)
        param_names = [p.name for p in params]
        assert param_names == ["a", "b", "ctx"]

        # ctx should have a default now
        assert sig.parameters["ctx"].default is not inspect.Parameter.empty

    async def test_context_resource(self, mcp: FastMCP):
        """Test Context transformation works with resources."""

        @mcp.resource("data://test")
        async def resource_with_ctx(ctx: Context) -> str:
            return f"session={ctx.session_id}"

        async with Client(mcp) as client:
            result = await client.read_resource("data://test")
            assert len(result) == 1
            assert "session=" in result[0].text

    async def test_context_resource_template(self, mcp: FastMCP):
        """Test Context transformation works with resource templates."""

        @mcp.resource("item://{item_id}")
        async def template_with_ctx(item_id: str, ctx: Context) -> str:
            return f"item={item_id}, session={ctx.session_id}"

        async with Client(mcp) as client:
            result = await client.read_resource("item://123")
            assert len(result) == 1
            assert "item=123" in result[0].text
            assert "session=" in result[0].text

    async def test_context_prompt(self, mcp: FastMCP):
        """Test Context transformation works with prompts."""

        @mcp.prompt()
        async def prompt_with_ctx(topic: str, ctx: Context) -> str:
            return f"Write about {topic} (session: {ctx.session_id})"

        async with Client(mcp) as client:
            result = await client.get_prompt("prompt_with_ctx", {"topic": "AI"})
            assert "Write about AI" in result.messages[0].content.text
            assert "session:" in result.messages[0].content.text


class TestVendoredDI:
    """Tests for vendored DI when docket is not installed."""

    def test_is_docket_available(self):
        """Test is_docket_available returns True when docket is installed."""
        from fastmcp.server.dependencies import is_docket_available

        # In dev environment, docket should be available
        assert is_docket_available() is True

    def test_require_docket_passes_when_installed(self):
        """Test require_docket doesn't raise when docket is installed."""
        from fastmcp.server.dependencies import require_docket

        # Should not raise
        require_docket("test feature")

    def test_vendored_dependency_class_exists(self):
        """Test vendored Dependency class is importable."""
        from fastmcp._vendor.docket_di import Dependency, Depends

        assert Dependency is not None
        assert Depends is not None

    def test_vendored_depends_works(self):
        """Test vendored Depends() creates proper dependency wrapper."""
        from fastmcp._vendor.docket_di import Depends, _Depends

        def get_value() -> str:
            return "test_value"

        dep = Depends(get_value)
        assert isinstance(dep, _Depends)
        assert dep.dependency is get_value

    async def test_depends_import_fallback(self):
        """Test that Depends can be imported from fastmcp.dependencies."""
        # This tests the import path, not the actual fallback behavior
        # since docket is always installed in dev
        from fastmcp.dependencies import Depends

        def get_config() -> dict:
            return {"key": "value"}

        dep = Depends(get_config)
        # Should work regardless of whether docket or vendored is used
        assert dep is not None

    def test_vendored_get_dependency_parameters(self):
        """Test vendored get_dependency_parameters finds dependency defaults."""
        from fastmcp._vendor.docket_di import (
            Depends,
            _Depends,
            get_dependency_parameters,
        )

        def get_db() -> str:
            return "database"

        def my_func(name: str, db: str = Depends(get_db)) -> str:
            return f"{name}: {db}"

        deps = get_dependency_parameters(my_func)
        assert "db" in deps
        db_dep = deps["db"]
        assert isinstance(db_dep, _Depends)
        assert db_dep.dependency is get_db
