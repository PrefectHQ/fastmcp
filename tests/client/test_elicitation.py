from dataclasses import asdict, dataclass
from enum import Enum
from typing import Literal

import pytest

from fastmcp import Context, FastMCP
from fastmcp.client.client import Client
from fastmcp.client.elicitation import ElicitResult
from fastmcp.exceptions import ToolError
from fastmcp.server.elicitation import (
    AcceptedElicitation,
    CancelledElicitation,
    DeclinedElicitation,
)
from fastmcp.utilities.types import TypeAdapter


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


async def test_elicitation_with_no_handler(fastmcp_server):
    """Test that elicitation works without a handler."""

    async with Client(fastmcp_server) as client:
        with pytest.raises(ToolError, match="Elicitation not supported"):
            await client.call_tool("ask_for_name", {})


async def test_elicitation_accept_content(fastmcp_server):
    """Test basic elicitation functionality."""

    async def elicitation_handler(message, response_type, params, ctx):
        # Mock user providing their name
        return ElicitResult(action="accept", content=response_type(name="Alice"))

    async with Client(
        fastmcp_server, elicitation_handler=elicitation_handler
    ) as client:
        result = await client.call_tool("ask_for_name", {})
        assert result.data == "Hello, Alice!"


async def test_elicitation_decline(fastmcp_server):
    """Test that elicitation handler receives correct parameters."""

    async def elicitation_handler(message, response_type, params, ctx):
        return ElicitResult(action="decline")

    async with Client(
        fastmcp_server, elicitation_handler=elicitation_handler
    ) as client:
        result = await client.call_tool("ask_for_name", {})
        assert result.data == "No name provided."


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

    async def elicitation_handler(message, response_type, params, ctx):
        # Mock user providing their favorite color as string in content dict
        return ElicitResult(action="accept", content={"value": "blue"})

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("ask_for_color", {})
        assert result.data == "Your favorite color is blue!"


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

    async def elicitation_handler(message, response_type, params, ctx):
        captured_params["message"] = message
        captured_params["response_type"] = str(response_type)
        captured_params["params"] = params
        captured_params["ctx"] = ctx
        return ElicitResult(action="accept", content={"value": 42})

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        await client.call_tool("test_tool", {})

        assert captured_params["message"] == "Test message"
        assert "ScalarElicitationType" in str(captured_params["response_type"])
        assert captured_params["params"].requestedSchema == {
            "properties": {"value": {"title": "Value", "type": "integer"}},
            "required": ["value"],
            "title": "ScalarElicitationType",
            "type": "object",
        }
        assert captured_params["ctx"] is not None


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

    async def elicitation_handler(message, response_type, params, ctx):
        return ElicitResult(action="cancel")

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("ask_for_optional_info", {})
        assert result.data == "Request was canceled"


async def test_elicitation_number_schema():
    """Test elicitation with number schema."""
    mcp = FastMCP("TestServer")

    @mcp.tool
    async def get_age(context: Context) -> str:
        result = await context.elicit(message="How old are you?", response_type=int)
        if result.action == "accept":
            return f"You are {result.data} years old"
        return "No age provided"

    async def elicitation_handler(message, response_type, params, ctx):
        return ElicitResult(action="accept", content=response_type(value=25))

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("get_age", {})
        assert result.data == "You are 25 years old"


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

    async def elicitation_handler(message, response_type, params, ctx):
        raise ValueError("Handler failed!")

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("failing_elicit", {})
        assert "Error:" in result.data


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

    async def elicitation_handler(message, response_type, params, ctx):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ElicitResult(action="accept", content={"value": "Bob"})
        elif call_count == 2:
            return ElicitResult(action="accept", content={"value": 25})
        else:
            raise ValueError("Unexpected call")

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("multi_step_form", {})
        assert result.data == "Hello Bob, you are 25 years old"
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
            return f"User: {result.data.name}, age: {result.data.age}"
        return "No user info provided"

    async def elicitation_handler(message, response_type, params, ctx):
        # Verify we get the dataclass type
        assert (
            TypeAdapter(response_type).json_schema()
            == TypeAdapter(UserInfo).json_schema()
        )

        # Verify the schema has the dataclass fields (available in params)
        schema = params.requestedSchema
        assert schema["type"] == "object"
        assert "name" in schema["properties"]
        assert "age" in schema["properties"]
        assert schema["properties"]["name"]["type"] == "string"
        assert schema["properties"]["age"]["type"] == "integer"

        return ElicitResult(action="accept", content=UserInfo(name="Alice", age=30))

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("get_user_info", {})
        assert result.data == "User: Alice, age: 30"


async def test_all_primitive_field_types():
    class DataEnum(Enum):
        X = "x"
        Y = "y"

    @dataclass
    class Data:
        integer: int
        float_: float
        number: int | float
        boolean: bool
        string: str
        constant: Literal["x"]
        union: Literal["x"] | Literal["y"]
        choice: Literal["x", "y"]
        enum: DataEnum

    mcp = FastMCP("TestServer")

    @mcp.tool
    async def get_data(context: Context) -> Data:
        result = await context.elicit(message="Enter data", response_type=Data)
        return result.data  # type: ignore[attr-defined]

    async def elicitation_handler(message, response_type, params, ctx):
        return ElicitResult(
            action="accept",
            content=Data(
                integer=1,
                float_=1.0,
                number=1.0,
                boolean=True,
                string="hello",
                constant="x",
                union="x",
                choice="x",
                enum=DataEnum.X,
            ),
        )

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("get_data", {})

        # Now all literal/enum fields should be preserved as strings
        result_data = asdict(result.data)
        result_data_enum = result_data.pop("enum")
        assert result_data_enum == "x"  # Should be a string now, not an enum
        assert result_data == {
            "integer": 1,
            "float_": 1.0,
            "number": 1.0,
            "boolean": True,
            "string": "hello",
            "constant": "x",
            "union": "x",
            "choice": "x",
        }


class TestValidation:
    async def test_schema_validation_rejects_non_object(self):
        """Test that non-object schemas are rejected."""
        from fastmcp.server.elicitation import validate_elicitation_json_schema

        with pytest.raises(TypeError, match="must be an object schema"):
            validate_elicitation_json_schema({"type": "string"})

    async def test_schema_validation_rejects_empty_object(self):
        """Test that object schemas without properties are rejected."""
        from fastmcp.server.elicitation import validate_elicitation_json_schema

        with pytest.raises(TypeError, match="must have at least one property"):
            validate_elicitation_json_schema({"type": "object"})

    async def test_schema_validation_rejects_nested_objects(self):
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

    async def test_schema_validation_rejects_arrays(self):
        """Test that array schemas are rejected."""
        from fastmcp.server.elicitation import validate_elicitation_json_schema

        with pytest.raises(
            TypeError, match="has type 'array' which is not a primitive type"
        ):
            validate_elicitation_json_schema(
                {
                    "type": "object",
                    "properties": {
                        "users": {"type": "array", "items": {"type": "string"}}
                    },
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

    async def elicitation_handler(message, response_type, params, ctx):
        return ElicitResult(action="accept", content={"value": "Alice"})

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("pattern_match_tool", {})
        assert result.data == "Hello Alice!"


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

    async def elicitation_handler(message, response_type, params, ctx):
        return ElicitResult(action="decline")

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("pattern_match_tool", {})
        assert result.data == "You declined"


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

    async def elicitation_handler(message, response_type, params, ctx):
        return ElicitResult(action="cancel")

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("pattern_match_tool", {})
        assert result.data == "Cancelled"
