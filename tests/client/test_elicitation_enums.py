"""Tests for enum-based elicitation, multi-select, and default values."""

from dataclasses import dataclass
from enum import Enum

import pytest
from pydantic import BaseModel, Field

from fastmcp import Context, FastMCP
from fastmcp.client.client import Client
from fastmcp.client.elicitation import ElicitResult
from fastmcp.exceptions import ToolError
from fastmcp.server.elicitation import (
    AcceptedElicitation,
    get_elicitation_schema,
    validate_elicitation_json_schema,
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
            assert isinstance(result, AcceptedElicitation)
            assert isinstance(result.data, Person)
            return f"Hello, {result.data.name}!"
        else:
            return "No name provided."

    @mcp.tool
    def simple_test() -> str:
        return "Hello!"

    return mcp


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
