"""
Tests for the Testing Demo server.

Demonstrates pytest-asyncio patterns, fixtures, and testing best practices.
"""

import pytest
from dirty_equals import IsStr

from fastmcp.client import Client


@pytest.fixture
async def client():
    """
    Client fixture for testing.

    Uses async context manager and yields client synchronously.
    No @pytest.mark.asyncio needed - asyncio_mode = "auto" handles it.
    """
    # Import here to avoid import-time side effects
    from server import mcp

    async with Client(mcp) as client:
        yield client


async def test_add_tool(client: Client):
    """Test the add tool with simple addition"""
    result = await client.call_tool("add", {"a": 2, "b": 3})
    assert result.data == 5


async def test_greet_tool_default(client: Client):
    """Test the greet tool with default greeting"""
    result = await client.call_tool("greet", {"name": "Alice"})
    assert result.data == "Hello, Alice!"


async def test_greet_tool_custom(client: Client):
    """Test the greet tool with custom greeting"""
    result = await client.call_tool("greet", {"name": "Bob", "greeting": "Hi"})
    assert result.data == "Hi, Bob!"


async def test_async_multiply_tool(client: Client):
    """Test the async multiply tool"""
    result = await client.call_tool("async_multiply", {"x": 3.5, "y": 2.0})
    assert result.data == 7.0


@pytest.mark.parametrize(
    "a,b,expected",
    [
        (0, 0, 0),
        (1, 1, 2),
        (-1, 1, 0),
        (100, 200, 300),
    ],
)
async def test_add_parametrized(client: Client, a: int, b: int, expected: int):
    """Test add tool with multiple parameter combinations"""
    result = await client.call_tool("add", {"a": a, "b": b})
    assert result.data == expected


async def test_server_info_resource(client: Client):
    """Test the server info resource"""
    result = await client.read_resource("demo://info")
    assert len(result) == 1
    assert result[0].text == "This is the FastMCP Testing Demo server"


async def test_greeting_resource_template(client: Client):
    """Test the greeting resource template"""
    result = await client.read_resource("demo://greeting/Charlie")
    assert len(result) == 1
    assert result[0].text == "Welcome to FastMCP, Charlie!"


async def test_hello_prompt_default(client: Client):
    """Test hello prompt with default parameter"""
    result = await client.get_prompt("hello")
    assert result.messages[0].content.text == "Say hello to World in a friendly way."


async def test_hello_prompt_custom(client: Client):
    """Test hello prompt with custom name"""
    result = await client.get_prompt("hello", {"name": "Dave"})
    assert result.messages[0].content.text == "Say hello to Dave in a friendly way."


async def test_explain_prompt_levels(client: Client):
    """Test explain prompt with different detail levels"""
    # Simple level
    result = await client.get_prompt(
        "explain", {"topic": "MCP", "detail_level": "simple"}
    )
    assert "simple terms" in result.messages[0].content.text
    assert "MCP" in result.messages[0].content.text

    # Detailed level
    result = await client.get_prompt(
        "explain", {"topic": "MCP", "detail_level": "detailed"}
    )
    assert "detailed" in result.messages[0].content.text
    assert "technical" in result.messages[0].content.text


async def test_list_tools(client: Client):
    """Test listing available tools"""
    tools = await client.list_tools()
    tool_names = [tool.name for tool in tools]

    assert "add" in tool_names
    assert "greet" in tool_names
    assert "async_multiply" in tool_names


async def test_list_resources(client: Client):
    """Test listing available resources"""
    resources = await client.list_resources()
    resource_uris = [str(resource.uri) for resource in resources]

    # Check that we have at least the static resource
    assert "demo://info" in resource_uris
    # There should be at least one resource listed
    assert len(resource_uris) >= 1


async def test_list_prompts(client: Client):
    """Test listing available prompts"""
    prompts = await client.list_prompts()
    prompt_names = [prompt.name for prompt in prompts]

    assert "hello" in prompt_names
    assert "explain" in prompt_names


# Example using dirty-equals for flexible assertions
async def test_greet_with_dirty_equals(client: Client):
    """Test greet tool using dirty-equals for pattern matching"""
    result = await client.call_tool("greet", {"name": "Eve"})
    # Check that result data matches the pattern
    assert result.data == IsStr(regex=r"^Hello, \w+!$")


# Example using inline-snapshot for complex data
async def test_tool_schema_structure(client: Client):
    """Test tool schema structure"""
    tools = await client.list_tools()
    add_tool = next(tool for tool in tools if tool.name == "add")

    # Verify basic schema structure
    assert add_tool.name == "add"
    assert add_tool.description == "Add two numbers together"
    assert "a" in add_tool.inputSchema["properties"]
    assert "b" in add_tool.inputSchema["properties"]
    assert add_tool.inputSchema["properties"]["a"]["type"] == "integer"
    assert add_tool.inputSchema["properties"]["b"]["type"] == "integer"
