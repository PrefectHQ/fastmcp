from dataclasses import dataclass

import pytest
from mcp.types import ElicitResult

from fastmcp import Context, FastMCP
from fastmcp.client.client import Client
from fastmcp.server.elicitation import (
    AcceptedElicitation,
    CancelledElicitation,
    DeclinedElicitation,
)


@pytest.fixture
def fastmcp_server():
    mcp = FastMCP("TestServer")

    @dataclass
    class Person:
        name: str

    @mcp.tool
    async def ask_for_name(context: Context) -> str:
        result = await context.elicit(
            message="What is your name?",
            response_type=Person,
        )
        if result.action == "accept":
            return f"Hello, {result.data.name}!"
        else:
            return "No name provided."

    @mcp.tool
    def simple_test() -> str:
        return "Hello!"

    return mcp


async def test_elicitation_accept_content(fastmcp_server):
    """Test basic elicitation functionality."""

    async def elicitation_handler(message, schema, ctx):
        # Mock user providing their name
        return ElicitResult(action="accept", content={"name": "Alice"})

    async with Client(
        fastmcp_server, elicitation_handler=elicitation_handler
    ) as client:
        result = await client.call_tool("ask_for_name", {})
        assert result[0].text == "Hello, Alice!"  # type: ignore[attr-defined]


async def test_elicitation_decline(fastmcp_server):
    """Test that elicitation handler receives correct parameters."""

    async def elicitation_handler(message, schema, ctx):
        return ElicitResult(action="decline")

    async with Client(
        fastmcp_server, elicitation_handler=elicitation_handler
    ) as client:
        result = await client.call_tool("ask_for_name", {})
        assert result[0].text == "No name provided."  # type: ignore[attr-defined]


async def test_default_response_type(fastmcp_server):
    """Test elicitation with string content."""
    mcp = FastMCP("TestServer")

    @mcp.tool
    async def ask_for_color(context: Context) -> str:
        result = await context.elicit(
            message="What is your favorite color?"
            # Default schema should be string
        )
        if result.action == "accept":
            assert isinstance(result.data, str)
            return f"Your favorite color is {result.data}!"
        return "No color provided"

    async def elicitation_handler(message, schema, ctx):
        # Mock user providing their favorite color as string in content dict
        return ElicitResult(action="accept", content={"value": "blue"})

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("ask_for_color", {})
        assert result[0].text == "Your favorite color is blue!"  # type: ignore[attr-defined]


async def test_elicitation_handler_parameters():
    """Test that elicitation handler receives correct parameters."""
    mcp = FastMCP("TestServer")
    captured_params = {}

    @mcp.tool
    async def test_tool(context: Context) -> str:
        await context.elicit(
            message="Test message",
            response_type=int,
        )
        return "done"

    async def elicitation_handler(message, schema, ctx):
        captured_params["message"] = message
        captured_params["schema"] = schema
        captured_params["ctx"] = ctx
        return ElicitResult(action="accept", content={"value": 42})

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        await client.call_tool("test_tool", {})

        assert captured_params["message"] == "Test message"
        assert captured_params["schema"] == {
            "properties": {"value": {"title": "Value", "type": "integer"}},
            "required": ["value"],
            "title": "PrimitiveElicitationType",
            "type": "object",
        }
        assert captured_params["ctx"] is not None


async def test_elicitation_default_string_schema():
    """Test elicitation with default string schema."""
    mcp = FastMCP("TestServer")

    @mcp.tool
    async def ask_for_input(context: Context) -> str:
        result = await context.elicit(
            message="Please provide some input"
            # No schema provided - should default to string
        )
        if result.action == "accept":
            return f"You said: {result.data}"
        return "No input provided"

    async def elicitation_handler(message, schema, ctx):
        # Verify default schema is wrapped string object
        expected_schema = {
            "properties": {"value": {"title": "Value", "type": "string"}},
            "required": ["value"],
            "title": "PrimitiveElicitationType",
            "type": "object",
        }
        assert schema == expected_schema
        return ElicitResult(action="accept", content={"value": "Hello world!"})

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("ask_for_input", {})
        assert result[0].text == "You said: Hello world!"  # type: ignore[attr-defined]


async def test_elicitation_cancel_action():
    """Test user canceling elicitation request."""
    mcp = FastMCP("TestServer")

    @mcp.tool
    async def ask_for_optional_info(context: Context) -> str:
        result = await context.elicit(
            message="Optional: What's your age?", response_type=int
        )
        if result.action == "cancel":
            return "Request was canceled"
        elif result.action == "accept":
            return f"Age: {result.data}"
        else:
            return "No response provided"

    async def elicitation_handler(message, schema, ctx):
        return ElicitResult(action="cancel")

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("ask_for_optional_info", {})
        assert result[0].text == "Request was canceled"  # type: ignore[attr-defined]


async def test_elicitation_number_schema():
    """Test elicitation with number schema."""
    mcp = FastMCP("TestServer")

    @mcp.tool
    async def get_age(context: Context) -> str:
        result = await context.elicit(message="How old are you?", response_type=int)
        if result.action == "accept":
            return f"You are {result.data} years old"
        return "No age provided"

    async def elicitation_handler(message, schema, ctx):
        expected_schema = {
            "properties": {"value": {"title": "Value", "type": "integer"}},
            "required": ["value"],
            "title": "PrimitiveElicitationType",
            "type": "object",
        }
        assert schema == expected_schema
        return ElicitResult(action="accept", content={"value": 25})

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("get_age", {})
        assert result[0].text == "You are 25 years old"  # type: ignore[attr-defined]


async def test_elicitation_handler_error():
    """Test error handling in elicitation handler."""
    mcp = FastMCP("TestServer")

    @mcp.tool
    async def failing_elicit(context: Context) -> str:
        try:
            result = await context.elicit(message="This will fail", response_type=str)
            assert result.action == "accept"
            return f"Got: {result.data}"
        except Exception as e:
            return f"Error: {str(e)}"

    async def elicitation_handler(message, schema, ctx):
        raise ValueError("Handler failed!")

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("failing_elicit", {})
        assert "Error:" in result[0].text  # type: ignore[attr-defined]


async def test_elicitation_multiple_calls():
    """Test multiple elicitation calls in sequence."""
    mcp = FastMCP("TestServer")

    @mcp.tool
    async def multi_step_form(context: Context) -> str:
        # First question
        name_result = await context.elicit(
            message="What's your name?", response_type=str
        )
        if name_result.action != "accept":
            return "Form abandoned"

        # Second question
        age_result = await context.elicit(message="What's your age?", response_type=int)
        if age_result.action != "accept":
            return f"Hello {name_result.data}, form incomplete"

        return f"Hello {name_result.data}, you are {age_result.data} years old"

    call_count = 0

    async def elicitation_handler(message, schema, ctx):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            assert "name" in message.lower()
            expected_schema = {
                "properties": {"value": {"title": "Value", "type": "string"}},
                "required": ["value"],
                "title": "PrimitiveElicitationType",
                "type": "object",
            }
            assert schema == expected_schema
            return ElicitResult(action="accept", content={"value": "Bob"})
        elif call_count == 2:
            assert "age" in message.lower()
            expected_schema = {
                "properties": {"value": {"title": "Value", "type": "integer"}},
                "required": ["value"],
                "title": "PrimitiveElicitationType",
                "type": "object",
            }
            assert schema == expected_schema
            return ElicitResult(action="accept", content={"value": 25})
        else:
            raise ValueError("Unexpected call")

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("multi_step_form", {})
        assert result[0].text == "Hello Bob, you are 25 years old"  # type: ignore[attr-defined]
        assert call_count == 2


async def test_dataclass_response_type():
    """Test elicitation with dataclass response type."""
    mcp = FastMCP("TestServer")

    @dataclass
    class UserInfo:
        name: str
        age: int

    @mcp.tool
    async def get_user_info(context: Context) -> str:
        result = await context.elicit(
            message="Please provide your information", response_type=UserInfo
        )
        if result.action == "accept":
            user = result.data
            return f"User: {user.name}, age: {user.age}"
        return "No user info provided"

    async def elicitation_handler(message, schema, ctx):
        # Verify the schema has the dataclass fields
        assert schema["type"] == "object"
        assert "name" in schema["properties"]
        assert "age" in schema["properties"]
        assert schema["properties"]["name"]["type"] == "string"
        assert schema["properties"]["age"]["type"] == "integer"

        return ElicitResult(action="accept", content={"name": "Alice", "age": 30})

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("get_user_info", {})
        assert result[0].text == "User: Alice, age: 30"  # type: ignore[attr-defined]


async def test_primitive_type_string():
    """Test elicitation with string primitive type."""
    mcp = FastMCP("TestServer")

    @mcp.tool
    async def test_string(context: Context) -> str:
        result = await context.elicit("Enter text:", response_type=str)
        assert result.action == "accept"
        return f"Got: {result.data}"

    async def elicitation_handler(message, schema, ctx):
        assert schema["properties"]["value"]["type"] == "string"
        return ElicitResult(action="accept", content={"value": "hello"})

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("test_string", {})
        assert result[0].text == "Got: hello"  # type: ignore[attr-defined]


async def test_primitive_type_int():
    """Test elicitation with integer primitive type."""
    mcp = FastMCP("TestServer")

    @mcp.tool
    async def test_int(context: Context) -> str:
        result = await context.elicit("Enter number:", response_type=int)
        assert result.action == "accept"
        return f"Got: {result.data}"

    async def elicitation_handler(message, schema, ctx):
        assert schema["properties"]["value"]["type"] == "integer"
        return ElicitResult(action="accept", content={"value": 42})

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("test_int", {})
        assert result[0].text == "Got: 42"  # type: ignore[attr-defined]


async def test_primitive_type_float():
    """Test elicitation with float primitive type."""
    mcp = FastMCP("TestServer")

    @mcp.tool
    async def test_float(context: Context) -> str:
        result = await context.elicit("Enter decimal:", response_type=float)
        assert result.action == "accept"
        return f"Got: {result.data}"

    async def elicitation_handler(message, schema, ctx):
        assert schema["properties"]["value"]["type"] == "number"
        return ElicitResult(action="accept", content={"value": 3.14})

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("test_float", {})
        assert result[0].text == "Got: 3.14"  # type: ignore[attr-defined]


async def test_primitive_type_bool():
    """Test elicitation with boolean primitive type."""
    mcp = FastMCP("TestServer")

    @mcp.tool
    async def test_bool(context: Context) -> str:
        result = await context.elicit("Enter true/false:", response_type=bool)
        assert result.action == "accept"
        return f"Got: {result.data}"

    async def elicitation_handler(message, schema, ctx):
        assert schema["properties"]["value"]["type"] == "boolean"
        return ElicitResult(action="accept", content={"value": True})

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("test_bool", {})
        assert result[0].text == "Got: True"  # type: ignore[attr-defined]


async def test_schema_validation_rejects_non_object():
    """Test that non-object schemas are rejected."""
    from fastmcp.server.elicitation import validate_elicitation_json_schema

    with pytest.raises(TypeError, match="must be an object schema"):
        validate_elicitation_json_schema({"type": "string"})


async def test_schema_validation_rejects_empty_object():
    """Test that object schemas without properties are rejected."""
    from fastmcp.server.elicitation import validate_elicitation_json_schema

    with pytest.raises(TypeError, match="must have at least one property"):
        validate_elicitation_json_schema({"type": "object"})


async def test_schema_validation_rejects_nested_objects():
    """Test that nested object schemas are rejected."""
    from fastmcp.server.elicitation import validate_elicitation_json_schema

    with pytest.raises(
        TypeError, match="has type 'object' which is not a primitive type"
    ):
        validate_elicitation_json_schema(
            {
                "type": "object",
                "properties": {
                    "user": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    }
                },
            }
        )


async def test_schema_validation_rejects_arrays():
    """Test that array schemas are rejected."""
    from fastmcp.server.elicitation import validate_elicitation_json_schema

    with pytest.raises(
        TypeError, match="has type 'array' which is not a primitive type"
    ):
        validate_elicitation_json_schema(
            {
                "type": "object",
                "properties": {"users": {"type": "array", "items": {"type": "string"}}},
            }
        )


async def test_pattern_matching_accept():
    """Test pattern matching with AcceptedElicitation."""
    mcp = FastMCP("TestServer")

    @mcp.tool
    async def pattern_match_tool(context: Context) -> str:
        result = await context.elicit("Enter your name:", response_type=str)

        match result:
            case AcceptedElicitation(data=name):
                return f"Hello {name}!"
            case DeclinedElicitation():
                return "You declined"
            case CancelledElicitation():
                return "Cancelled"

    async def elicitation_handler(message, schema, ctx):
        return ElicitResult(action="accept", content={"value": "Alice"})

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("pattern_match_tool", {})
        assert result[0].text == "Hello Alice!"  # type: ignore[attr-defined]


async def test_pattern_matching_decline():
    """Test pattern matching with DeclinedElicitation."""
    mcp = FastMCP("TestServer")

    @mcp.tool
    async def pattern_match_tool(context: Context) -> str:
        result = await context.elicit("Enter your name:", response_type=str)

        match result:
            case AcceptedElicitation(data=name):
                return f"Hello {name}!"
            case DeclinedElicitation():
                return "You declined"
            case CancelledElicitation():
                return "Cancelled"

    async def elicitation_handler(message, schema, ctx):
        return ElicitResult(action="decline")

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("pattern_match_tool", {})
        assert result[0].text == "You declined"  # type: ignore[attr-defined]


async def test_pattern_matching_cancel():
    """Test pattern matching with CancelledElicitation."""
    mcp = FastMCP("TestServer")

    @mcp.tool
    async def pattern_match_tool(context: Context) -> str:
        result = await context.elicit("Enter your name:", response_type=str)

        match result:
            case AcceptedElicitation(data=name):
                return f"Hello {name}!"
            case DeclinedElicitation():
                return "You declined"
            case CancelledElicitation():
                return "Cancelled"

    async def elicitation_handler(message, schema, ctx):
        return ElicitResult(action="cancel")

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("pattern_match_tool", {})
        assert result[0].text == "Cancelled"  # type: ignore[attr-defined]
