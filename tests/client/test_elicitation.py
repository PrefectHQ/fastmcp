from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Literal, cast

import pytest
from mcp.types import ElicitRequestFormParams, ElicitRequestParams
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from fastmcp import Context, FastMCP
from fastmcp.client.client import Client
from fastmcp.client.elicitation import ElicitResult
from fastmcp.exceptions import ToolError
from fastmcp.server.elicitation import (
    AcceptedElicitation,
    CancelledElicitation,
    DeclinedElicitation,
    get_elicitation_schema,
    validate_elicitation_json_schema,
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
            assert isinstance(result, AcceptedElicitation)
            assert isinstance(result.data, Person)
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
            await client.call_tool("ask_for_name")


async def test_elicitation_accept_content(fastmcp_server):
    """Test basic elicitation functionality."""

    async def elicitation_handler(message, response_type, params, ctx):
        # Mock user providing their name
        return ElicitResult(action="accept", content=response_type(name="Alice"))

    async with Client(
        fastmcp_server, elicitation_handler=elicitation_handler
    ) as client:
        result = await client.call_tool("ask_for_name")
        assert result.data == "Hello, Alice!"


async def test_elicitation_decline(fastmcp_server):
    """Test that elicitation handler receives correct parameters."""

    async def elicitation_handler(message, response_type, params, ctx):
        return ElicitResult(action="decline")

    async with Client(
        fastmcp_server, elicitation_handler=elicitation_handler
    ) as client:
        result = await client.call_tool("ask_for_name")
        assert result.data == "No name provided."


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
            assert isinstance(result, AcceptedElicitation)
            assert isinstance(result.data, int)
            return f"Age: {result.data}"
        else:
            return "No response provided"

    async def elicitation_handler(message, response_type, params, ctx):
        return ElicitResult(action="cancel")

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("ask_for_optional_info", {})
        assert result.data == "Request was canceled"


class TestScalarResponseTypes:
    async def test_elicitation_no_response(self):
        """Test elicitation with no response type."""
        mcp = FastMCP("TestServer")

        @mcp.tool
        async def my_tool(context: Context) -> dict[str, Any]:
            result = await context.elicit(message="", response_type=None)
            assert isinstance(result, AcceptedElicitation)
            assert isinstance(result.data, dict)
            return cast(dict[str, Any], result.data)

        async def elicitation_handler(
            message, response_type, params: ElicitRequestParams, ctx
        ):
            assert isinstance(params, ElicitRequestFormParams)
            assert params.requestedSchema == {"type": "object", "properties": {}}
            assert response_type is None
            return ElicitResult(action="accept")

        async with Client(mcp, elicitation_handler=elicitation_handler) as client:
            result = await client.call_tool("my_tool", {})
            assert result.data is None

    async def test_elicitation_empty_response(self):
        """Test elicitation with empty response type."""
        mcp = FastMCP("TestServer")

        @mcp.tool
        async def my_tool(context: Context) -> dict[str, Any]:
            result = await context.elicit(message="", response_type=None)
            assert result.action == "accept"
            assert isinstance(result, AcceptedElicitation)
            accepted = cast(AcceptedElicitation[dict[str, Any]], result)
            assert isinstance(accepted.data, dict)
            return accepted.data

        async def elicitation_handler(
            message, response_type, params: ElicitRequestParams, ctx
        ):
            return ElicitResult(action="accept", content={})

        async with Client(mcp, elicitation_handler=elicitation_handler) as client:
            result = await client.call_tool("my_tool", {})
            assert result.data is None

    async def test_elicitation_response_when_no_response_requested(self):
        """Test elicitation with no response type."""
        mcp = FastMCP("TestServer")

        @mcp.tool
        async def my_tool(context: Context) -> dict[str, Any]:
            result = await context.elicit(message="", response_type=None)
            assert result.action == "accept"
            assert isinstance(result, AcceptedElicitation)
            accepted = cast(AcceptedElicitation[dict[str, Any]], result)
            assert isinstance(accepted.data, dict)
            return accepted.data

        async def elicitation_handler(message, response_type, params, ctx):
            return ElicitResult(action="accept", content={"value": "hello"})

        async with Client(mcp, elicitation_handler=elicitation_handler) as client:
            with pytest.raises(
                ToolError, match="Elicitation expected an empty response"
            ):
                await client.call_tool("my_tool", {})

    async def test_elicitation_str_response(self):
        """Test elicitation with string schema."""
        mcp = FastMCP("TestServer")

        @mcp.tool
        async def my_tool(context: Context) -> str:
            result = await context.elicit(message="", response_type=str)
            assert isinstance(result, AcceptedElicitation)
            assert isinstance(result.data, str)
            return result.data

        async def elicitation_handler(message, response_type, params, ctx):
            return ElicitResult(action="accept", content={"value": "hello"})

        async with Client(mcp, elicitation_handler=elicitation_handler) as client:
            result = await client.call_tool("my_tool", {})
            assert result.data == "hello"

    async def test_elicitation_int_response(self):
        """Test elicitation with number schema."""
        mcp = FastMCP("TestServer")

        @mcp.tool
        async def my_tool(context: Context) -> int:
            result = await context.elicit(message="", response_type=int)
            assert isinstance(result, AcceptedElicitation)
            assert isinstance(result.data, int)
            return result.data

        async def elicitation_handler(message, response_type, params, ctx):
            return ElicitResult(action="accept", content={"value": 42})

        async with Client(mcp, elicitation_handler=elicitation_handler) as client:
            result = await client.call_tool("my_tool", {})
            assert result.data == 42

    async def test_elicitation_float_response(self):
        """Test elicitation with number schema."""
        mcp = FastMCP("TestServer")

        @mcp.tool
        async def my_tool(context: Context) -> float:
            result = await context.elicit(message="", response_type=float)
            assert isinstance(result, AcceptedElicitation)
            assert isinstance(result.data, float)
            return result.data

        async def elicitation_handler(message, response_type, params, ctx):
            return ElicitResult(action="accept", content={"value": 3.14})

        async with Client(mcp, elicitation_handler=elicitation_handler) as client:
            result = await client.call_tool("my_tool", {})
            assert result.data == 3.14

    async def test_elicitation_bool_response(self):
        """Test elicitation with boolean schema."""
        mcp = FastMCP("TestServer")

        @mcp.tool
        async def my_tool(context: Context) -> bool:
            result = await context.elicit(message="", response_type=bool)
            assert isinstance(result, AcceptedElicitation)
            assert isinstance(result.data, bool)
            return result.data

        async def elicitation_handler(message, response_type, params, ctx):
            return ElicitResult(action="accept", content={"value": True})

        async with Client(mcp, elicitation_handler=elicitation_handler) as client:
            result = await client.call_tool("my_tool", {})
            assert result.data is True

    async def test_elicitation_literal_response(self):
        """Test elicitation with literal schema."""
        mcp = FastMCP("TestServer")

        @mcp.tool
        async def my_tool(context: Context) -> Literal["x", "y"]:
            # Literal types work at runtime but type checker doesn't recognize them in overloads
            result = await context.elicit(message="", response_type=Literal["x", "y"])  # type: ignore[arg-type]
            assert isinstance(result, AcceptedElicitation)
            accepted = cast(AcceptedElicitation[Literal["x", "y"]], result)
            assert isinstance(accepted.data, str)
            return accepted.data

        async def elicitation_handler(message, response_type, params, ctx):
            return ElicitResult(action="accept", content={"value": "x"})

        async with Client(mcp, elicitation_handler=elicitation_handler) as client:
            result = await client.call_tool("my_tool", {})
            assert result.data == "x"

    async def test_elicitation_enum_response(self):
        """Test elicitation with enum schema."""
        mcp = FastMCP("TestServer")

        class ResponseEnum(Enum):
            X = "x"
            Y = "y"

        @mcp.tool
        async def my_tool(context: Context) -> ResponseEnum:
            result = await context.elicit(message="", response_type=ResponseEnum)
            assert isinstance(result, AcceptedElicitation)
            assert isinstance(result.data, ResponseEnum)
            return result.data

        async def elicitation_handler(message, response_type, params, ctx):
            return ElicitResult(action="accept", content={"value": "x"})

        async with Client(mcp, elicitation_handler=elicitation_handler) as client:
            result = await client.call_tool("my_tool", {})
            assert result.data == "x"

    async def test_elicitation_list_of_strings_response(self):
        """Test elicitation with list schema."""
        mcp = FastMCP("TestServer")

        @mcp.tool
        async def my_tool(context: Context) -> str:
            result = await context.elicit(message="", response_type=["x", "y"])
            assert isinstance(result, AcceptedElicitation)
            assert isinstance(result.data, str)
            return result.data

        async def elicitation_handler(message, response_type, params, ctx):
            return ElicitResult(action="accept", content={"value": "x"})

        async with Client(mcp, elicitation_handler=elicitation_handler) as client:
            result = await client.call_tool("my_tool", {})
            assert result.data == "x"


async def test_elicitation_handler_error():
    """Test error handling in elicitation handler."""
    mcp = FastMCP("TestServer")

    @mcp.tool
    async def failing_elicit(context: Context) -> str:
        try:
            result = await context.elicit(message="This will fail", response_type=str)

            assert isinstance(result, AcceptedElicitation)

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

        assert isinstance(name_result, AcceptedElicitation)

        if name_result.action != "accept":
            return "Form abandoned"

        # Second question
        age_result = await context.elicit(message="What's your age?", response_type=int)

        assert isinstance(age_result, AcceptedElicitation)

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


@dataclass
class UserInfo:
    name: str
    age: int


class UserInfoTypedDict(TypedDict):
    name: str
    age: int


class UserInfoPydantic(BaseModel):
    name: str
    age: int


@pytest.mark.parametrize(
    "structured_type", [UserInfo, UserInfoTypedDict, UserInfoPydantic]
)
async def test_structured_response_type(
    structured_type: type[UserInfo | UserInfoTypedDict | UserInfoPydantic],
):
    """Test elicitation with dataclass response type."""
    mcp = FastMCP("TestServer")

    @mcp.tool
    async def get_user_info(context: Context) -> str:
        result = await context.elicit(
            message="Please provide your information", response_type=structured_type
        )

        assert isinstance(result, AcceptedElicitation)

        if result.action == "accept":
            assert isinstance(result, AcceptedElicitation)
            if isinstance(result.data, dict):
                data_dict = cast(dict[str, Any], result.data)
                name = data_dict.get("name")
                age = data_dict.get("age")
                assert name is not None
                assert age is not None
                return f"User: {name}, age: {age}"
            else:
                # result.data is a structured type (UserInfo, UserInfoTypedDict, or UserInfoPydantic)
                assert hasattr(result.data, "name")
                assert hasattr(result.data, "age")
                return f"User: {result.data.name}, age: {result.data.age}"
        return "No user info provided"

    async def elicitation_handler(message, response_type, params, ctx):
        # Verify we get the dataclass type
        assert (
            TypeAdapter(response_type).json_schema()
            == TypeAdapter(structured_type).json_schema()
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
        assert isinstance(result, AcceptedElicitation)
        assert isinstance(result.data, Data)
        return result.data

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

        with pytest.raises(TypeError, match="must be an object schema"):
            validate_elicitation_json_schema({"type": "string"})

    async def test_schema_validation_rejects_nested_objects(self):
        """Test that nested object schemas are rejected."""

        with pytest.raises(
            TypeError, match="is an object, but nested objects are not allowed"
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
        """Test that non-enum array schemas are rejected."""

        with pytest.raises(TypeError, match="is an array, but arrays are only allowed"):
            validate_elicitation_json_schema(
                {
                    "type": "object",
                    "properties": {
                        "users": {"type": "array", "items": {"type": "string"}}
                    },
                }
            )


class TestPatternMatching:
    async def test_pattern_matching_accept(self):
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
                case _:
                    return "Unknown result"

        async def elicitation_handler(message, response_type, params, ctx):
            return ElicitResult(action="accept", content={"value": "Alice"})

        async with Client(mcp, elicitation_handler=elicitation_handler) as client:
            result = await client.call_tool("pattern_match_tool", {})
            assert result.data == "Hello Alice!"

    async def test_pattern_matching_decline(self):
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
                case _:
                    return "Unknown result"

        async def elicitation_handler(message, response_type, params, ctx):
            return ElicitResult(action="decline")

        async with Client(mcp, elicitation_handler=elicitation_handler) as client:
            result = await client.call_tool("pattern_match_tool", {})
            assert result.data == "You declined"

    async def test_pattern_matching_cancel(self):
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
                case _:
                    return "Unknown result"

        async def elicitation_handler(message, response_type, params, ctx):
            return ElicitResult(action="cancel")

        async with Client(mcp, elicitation_handler=elicitation_handler) as client:
            result = await client.call_tool("pattern_match_tool", {})
            assert result.data == "Cancelled"


async def test_elicitation_implicit_acceptance(fastmcp_server):
    """Test that elicitation handler can return data directly without ElicitResult wrapper."""

    async def elicitation_handler(message, response_type, params, ctx):
        # Return data directly without wrapping in ElicitResult
        # This should be treated as implicit acceptance
        return response_type(name="Bob")

    async with Client(
        fastmcp_server, elicitation_handler=elicitation_handler
    ) as client:
        result = await client.call_tool("ask_for_name")
        assert result.data == "Hello, Bob!"


async def test_elicitation_implicit_acceptance_must_be_dict(fastmcp_server):
    """Test that elicitation handler can return data directly without ElicitResult wrapper."""

    async def elicitation_handler(message, response_type, params, ctx):
        # Return data directly without wrapping in ElicitResult
        # This should be treated as implicit acceptance
        return "Bob"

    async with Client(
        fastmcp_server, elicitation_handler=elicitation_handler
    ) as client:
        with pytest.raises(
            ToolError,
            match="Elicitation responses must be serializable as a JSON object",
        ):
            await client.call_tool("ask_for_name")


def test_enum_elicitation_schema_inline():
    """Test that enum schemas are generated inline without $ref/$defs for MCP compatibility."""

    class Priority(Enum):
        LOW = "low"
        MEDIUM = "medium"
        HIGH = "high"

    @dataclass
    class TaskRequest:
        title: str
        priority: Priority

    # Generate elicitation schema
    schema = get_elicitation_schema(TaskRequest)

    # Verify no $defs section exists (enums should be inlined)
    assert "$defs" not in schema, (
        "Schema should not contain $defs - enums must be inline"
    )

    # Verify no $ref in properties
    for prop_name, prop_schema in schema.get("properties", {}).items():
        assert "$ref" not in prop_schema, (
            f"Property {prop_name} contains $ref - should be inline"
        )

    # Verify the priority field has inline enum values
    priority_schema = schema["properties"]["priority"]
    assert "enum" in priority_schema, "Priority should have enum values inline"
    assert priority_schema["enum"] == ["low", "medium", "high"]
    assert priority_schema.get("type") == "string"

    # Verify title field is a simple string
    assert schema["properties"]["title"]["type"] == "string"


def test_enum_elicitation_schema_inline_untitled():
    """Test that enum schemas generate simple enum pattern (no automatic titles)."""

    class TaskStatus(Enum):
        NOT_STARTED = "not_started"
        IN_PROGRESS = "in_progress"
        COMPLETED = "completed"
        ON_HOLD = "on_hold"

    @dataclass
    class TaskUpdate:
        task_id: str
        status: TaskStatus

    # Generate elicitation schema
    schema = get_elicitation_schema(TaskUpdate)

    # Verify enum is inline
    assert "$defs" not in schema
    assert "$ref" not in str(schema)

    status_schema = schema["properties"]["status"]
    # Should generate simple enum pattern (no automatic title generation)
    assert "enum" in status_schema
    assert "oneOf" not in status_schema
    assert "enumNames" not in status_schema
    assert status_schema["enum"] == [
        "not_started",
        "in_progress",
        "completed",
        "on_hold",
    ]


async def test_dict_based_titled_single_select():
    """Test dict-based titled single-select enum."""
    mcp = FastMCP("TestServer")

    @mcp.tool
    async def my_tool(ctx: Context) -> str:
        result = await ctx.elicit(
            "Choose priority",
            response_type={
                "low": {"title": "Low Priority"},
                "high": {"title": "High Priority"},
            },
        )
        if result.action == "accept":
            assert isinstance(result, AcceptedElicitation)
            assert isinstance(result.data, str)
            return result.data
        return "declined"

    async def elicitation_handler(message, response_type, params, ctx):
        # Verify schema follows SEP-1330 pattern with type: "string"
        schema = params.requestedSchema
        assert schema["type"] == "object"
        assert "value" in schema["properties"]
        value_schema = schema["properties"]["value"]
        assert value_schema["type"] == "string"
        assert "oneOf" in value_schema
        one_of = value_schema["oneOf"]
        assert {"const": "low", "title": "Low Priority"} in one_of
        assert {"const": "high", "title": "High Priority"} in one_of

        return ElicitResult(action="accept", content={"value": "low"})

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("my_tool", {})
        assert result.data == "low"


async def test_list_list_multi_select_untitled():
    """Test list[list[str]] for multi-select untitled shorthand."""
    mcp = FastMCP("TestServer")

    @mcp.tool
    async def my_tool(ctx: Context) -> str:
        result = await ctx.elicit(
            "Choose tags",
            response_type=[["bug", "feature", "documentation"]],
        )
        if result.action == "accept":
            assert isinstance(result, AcceptedElicitation)
            assert isinstance(result.data, list)
            return ",".join(result.data)  # type: ignore[no-matching-overload]
        return "declined"

    async def elicitation_handler(message, response_type, params, ctx):
        # Verify schema has array with enum pattern
        schema = params.requestedSchema
        assert schema["type"] == "object"
        assert "value" in schema["properties"]
        value_schema = schema["properties"]["value"]
        assert value_schema["type"] == "array"
        assert "enum" in value_schema["items"]
        assert value_schema["items"]["enum"] == ["bug", "feature", "documentation"]

        return ElicitResult(action="accept", content={"value": ["bug", "feature"]})

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("my_tool", {})
        assert result.data == "bug,feature"


async def test_list_dict_multi_select_titled():
    """Test list[dict] for multi-select titled."""
    mcp = FastMCP("TestServer")

    @mcp.tool
    async def my_tool(ctx: Context) -> str:
        result = await ctx.elicit(
            "Choose priorities",
            response_type=[
                {
                    "low": {"title": "Low Priority"},
                    "high": {"title": "High Priority"},
                }
            ],
        )
        if result.action == "accept":
            assert isinstance(result, AcceptedElicitation)
            assert isinstance(result.data, list)
            return ",".join(result.data)  # type: ignore[no-matching-overload]
        return "declined"

    async def elicitation_handler(message, response_type, params, ctx):
        # Verify schema has array with SEP-1330 compliant items (anyOf pattern)
        schema = params.requestedSchema
        assert schema["type"] == "object"
        assert "value" in schema["properties"]
        value_schema = schema["properties"]["value"]
        assert value_schema["type"] == "array"
        items_schema = value_schema["items"]
        assert "anyOf" in items_schema
        any_of = items_schema["anyOf"]
        assert {"const": "low", "title": "Low Priority"} in any_of
        assert {"const": "high", "title": "High Priority"} in any_of

        return ElicitResult(action="accept", content={"value": ["low", "high"]})

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("my_tool", {})
        assert result.data == "low,high"


async def test_list_enum_multi_select():
    """Test list[Enum] for multi-select with enum in dataclass field."""

    class Priority(Enum):
        LOW = "low"
        MEDIUM = "medium"
        HIGH = "high"

    @dataclass
    class TaskRequest:
        priorities: list[Priority]

    schema = get_elicitation_schema(TaskRequest)

    priorities_schema = schema["properties"]["priorities"]
    assert priorities_schema["type"] == "array"
    assert "items" in priorities_schema
    items_schema = priorities_schema["items"]
    # Should have enum pattern for untitled enums
    assert "enum" in items_schema
    assert items_schema["enum"] == ["low", "medium", "high"]


async def test_list_enum_multi_select_direct():
    """Test list[Enum] type annotation passed directly to ctx.elicit()."""
    mcp = FastMCP("TestServer")

    class Priority(Enum):
        LOW = "low"
        MEDIUM = "medium"
        HIGH = "high"

    @mcp.tool
    async def my_tool(ctx: Context) -> str:
        result = await ctx.elicit(
            "Choose priorities",
            response_type=list[Priority],  # Type annotation for multi-select
        )
        if result.action == "accept":
            assert isinstance(result, AcceptedElicitation)
            assert isinstance(result.data, list)
            priorities = result.data
            return ",".join(
                [p.value if isinstance(p, Priority) else str(p) for p in priorities]
            )
        return "declined"

    async def elicitation_handler(message, response_type, params, ctx):
        # Verify schema has array with enum pattern
        schema = params.requestedSchema
        assert schema["type"] == "object"
        assert "value" in schema["properties"]
        value_schema = schema["properties"]["value"]
        assert value_schema["type"] == "array"
        assert "enum" in value_schema["items"]
        assert value_schema["items"]["enum"] == ["low", "medium", "high"]

        return ElicitResult(action="accept", content={"value": ["low", "high"]})

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("my_tool", {})
        assert result.data == "low,high"


async def test_validation_allows_enum_arrays():
    """Test validation accepts arrays with enum items."""
    schema = {
        "type": "object",
        "properties": {
            "priorities": {
                "type": "array",
                "items": {"enum": ["low", "medium", "high"]},
            }
        },
    }
    validate_elicitation_json_schema(schema)  # Should not raise


async def test_validation_allows_enum_arrays_with_anyof():
    """Test validation accepts arrays with anyOf enum pattern (SEP-1330 compliant)."""
    schema = {
        "type": "object",
        "properties": {
            "priorities": {
                "type": "array",
                "items": {
                    "anyOf": [
                        {"const": "low", "title": "Low Priority"},
                        {"const": "high", "title": "High Priority"},
                    ]
                },
            }
        },
    }
    validate_elicitation_json_schema(schema)  # Should not raise


async def test_validation_rejects_non_enum_arrays():
    """Test validation still rejects arrays of objects."""
    schema = {
        "type": "object",
        "properties": {
            "users": {
                "type": "array",
                "items": {"type": "object", "properties": {"name": {"type": "string"}}},
            }
        },
    }
    with pytest.raises(TypeError, match="array of objects"):
        validate_elicitation_json_schema(schema)


async def test_validation_rejects_primitive_arrays():
    """Test validation rejects arrays of primitives without enum pattern."""
    schema = {
        "type": "object",
        "properties": {
            "names": {"type": "array", "items": {"type": "string"}},
        },
    }
    with pytest.raises(TypeError, match="arrays are only allowed"):
        validate_elicitation_json_schema(schema)


class TestElicitationDefaults:
    """Test suite for default values in elicitation schemas."""

    def test_string_default_preserved(self):
        """Test that string defaults are preserved in the schema."""

        class Model(BaseModel):
            email: str = Field(default="[email protected]")

        schema = get_elicitation_schema(Model)
        props = schema.get("properties", {})

        assert "email" in props
        assert "default" in props["email"]
        assert props["email"]["default"] == "[email protected]"
        assert props["email"]["type"] == "string"

    def test_integer_default_preserved(self):
        """Test that integer defaults are preserved in the schema."""

        class Model(BaseModel):
            count: int = Field(default=50)

        schema = get_elicitation_schema(Model)
        props = schema.get("properties", {})

        assert "count" in props
        assert "default" in props["count"]
        assert props["count"]["default"] == 50
        assert props["count"]["type"] == "integer"

    def test_number_default_preserved(self):
        """Test that number defaults are preserved in the schema."""

        class Model(BaseModel):
            price: float = Field(default=3.14)

        schema = get_elicitation_schema(Model)
        props = schema.get("properties", {})

        assert "price" in props
        assert "default" in props["price"]
        assert props["price"]["default"] == 3.14
        assert props["price"]["type"] == "number"

    def test_boolean_default_preserved(self):
        """Test that boolean defaults are preserved in the schema."""

        class Model(BaseModel):
            enabled: bool = Field(default=False)

        schema = get_elicitation_schema(Model)
        props = schema.get("properties", {})

        assert "enabled" in props
        assert "default" in props["enabled"]
        assert props["enabled"]["default"] is False
        assert props["enabled"]["type"] == "boolean"

    def test_enum_default_preserved(self):
        """Test that enum defaults are preserved in the schema."""

        class Priority(Enum):
            LOW = "low"
            MEDIUM = "medium"
            HIGH = "high"

        class Model(BaseModel):
            choice: Priority = Field(default=Priority.MEDIUM)

        schema = get_elicitation_schema(Model)
        props = schema.get("properties", {})

        assert "choice" in props
        assert "default" in props["choice"]
        assert props["choice"]["default"] == "medium"
        assert "enum" in props["choice"]
        assert props["choice"]["type"] == "string"

    def test_all_defaults_preserved_together(self):
        """Test that all default types are preserved when used together."""

        class Priority(Enum):
            A = "A"
            B = "B"

        class Model(BaseModel):
            string_field: str = Field(default="[email protected]")
            integer_field: int = Field(default=50)
            number_field: float = Field(default=3.14)
            boolean_field: bool = Field(default=False)
            enum_field: Priority = Field(default=Priority.A)

        schema = get_elicitation_schema(Model)
        props = schema.get("properties", {})

        assert props["string_field"]["default"] == "[email protected]"
        assert props["integer_field"]["default"] == 50
        assert props["number_field"]["default"] == 3.14
        assert props["boolean_field"]["default"] is False
        assert props["enum_field"]["default"] == "A"

    def test_mixed_defaults_and_required(self):
        """Test that fields with defaults are not in required list."""

        class Model(BaseModel):
            required_field: str = Field(description="Required field")
            optional_with_default: int = Field(default=42)

        schema = get_elicitation_schema(Model)
        props = schema.get("properties", {})
        required = schema.get("required", [])

        assert "required_field" in required
        assert "optional_with_default" not in required
        assert props["optional_with_default"]["default"] == 42

    def test_compress_schema_preserves_defaults(self):
        """Test that compress_schema() doesn't strip default values."""

        class Model(BaseModel):
            string_field: str = Field(default="test")
            integer_field: int = Field(default=42)

        schema = get_elicitation_schema(Model)
        props = schema.get("properties", {})

        assert "default" in props["string_field"]
        assert "default" in props["integer_field"]
