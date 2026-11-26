"""Tests for enum-based elicitation, multi-select, and default values."""

from dataclasses import dataclass
from enum import Enum

import pytest
from pydantic import BaseModel, Field, ValidationError

from fastmcp import Context, FastMCP
from fastmcp.client.client import Client
from fastmcp.client.elicitation import ElicitResult
from fastmcp.exceptions import ToolError
from fastmcp.server.elicitation import (
    AcceptedElicitation,
    get_elicitation_schema,
    handle_elicit_accept,
    parse_elicit_response_type,
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
        fastmcp_server, mode="legacy", elicitation_handler=elicitation_handler
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
        fastmcp_server, mode="legacy", elicitation_handler=elicitation_handler
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


def test_enum_elicitation_schema_inline_when_enum_reused():
    """An enum shared by several fields is inlined in each of them."""

    class Currency(Enum):
        USD = "usd"
        EUR = "eur"

    class Conversion(BaseModel):
        source: Currency
        target: Currency = Field(default=Currency.EUR, description="Target")
        accepted: list[Currency]

    schema = get_elicitation_schema(Conversion)

    assert "$defs" not in schema
    assert "$ref" not in str(schema)
    assert schema["properties"] == {
        "source": {"enum": ["usd", "eur"], "title": "Currency", "type": "string"},
        "target": {
            "default": "eur",
            "description": "Target",
            "enum": ["usd", "eur"],
            "title": "Currency",
            "type": "string",
        },
        "accepted": {
            "items": {"enum": ["usd", "eur"], "title": "Currency", "type": "string"},
            "title": "Accepted",
            "type": "array",
        },
    }


async def test_elicit_model_with_reused_enum():
    """ctx.elicit() accepts a response type whose fields share an enum."""

    class Currency(Enum):
        USD = "usd"
        EUR = "eur"

    @dataclass
    class Conversion:
        source: Currency
        target: Currency

    mcp = FastMCP("TestServer")

    @mcp.tool
    async def convert(ctx: Context) -> str:
        result = await ctx.elicit("Pick currencies", response_type=Conversion)
        assert isinstance(result, AcceptedElicitation)
        assert result.data == Conversion(source=Currency.USD, target=Currency.EUR)
        return f"{result.data.source.value} -> {result.data.target.value}"

    async def elicitation_handler(message, response_type, params, ctx):
        return ElicitResult(action="accept", content={"source": "usd", "target": "eur"})

    async with Client(
        mcp, mode="legacy", elicitation_handler=elicitation_handler
    ) as client:
        result = await client.call_tool("convert", {})
        assert result.data == "usd -> eur"


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
        schema = params.requested_schema
        assert schema["type"] == "object"
        assert "value" in schema["properties"]
        value_schema = schema["properties"]["value"]
        assert value_schema["type"] == "string"
        assert "oneOf" in value_schema
        one_of = value_schema["oneOf"]
        assert {"const": "low", "title": "Low Priority"} in one_of
        assert {"const": "high", "title": "High Priority"} in one_of

        return ElicitResult(action="accept", content={"value": "low"})

    async with Client(
        mcp, mode="legacy", elicitation_handler=elicitation_handler
    ) as client:
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
        schema = params.requested_schema
        assert schema["type"] == "object"
        assert "value" in schema["properties"]
        value_schema = schema["properties"]["value"]
        assert value_schema["type"] == "array"
        assert "enum" in value_schema["items"]
        assert value_schema["items"]["enum"] == ["bug", "feature", "documentation"]
        # Required by the 2025-11-25 multi-select schema; validating clients reject it otherwise
        assert value_schema["items"]["type"] == "string"

        return ElicitResult(action="accept", content={"value": ["bug", "feature"]})

    async with Client(
        mcp, mode="legacy", elicitation_handler=elicitation_handler
    ) as client:
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
        schema = params.requested_schema
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

    async with Client(
        mcp, mode="legacy", elicitation_handler=elicitation_handler
    ) as client:
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
        schema = params.requested_schema
        assert schema["type"] == "object"
        assert "value" in schema["properties"]
        value_schema = schema["properties"]["value"]
        assert value_schema["type"] == "array"
        assert "enum" in value_schema["items"]
        assert value_schema["items"]["enum"] == ["low", "medium", "high"]

        return ElicitResult(action="accept", content={"value": ["low", "high"]})

    async with Client(
        mcp, mode="legacy", elicitation_handler=elicitation_handler
    ) as client:
        result = await client.call_tool("my_tool", {})
        assert result.data == "low,high"


CHOICE_SHORTHANDS = [
    pytest.param(["low", "high"], "high", "urgent", id="single-select"),
    pytest.param(
        {"low": {"title": "Low"}, "high": {"title": "High"}},
        "high",
        "urgent",
        id="titled-single-select",
    ),
    pytest.param([["bug", "feature"]], ["feature"], ["urgent"], id="multi-select"),
    pytest.param([["bug", "feature"]], ["feature"], "feature", id="multi-select-str"),
    pytest.param(
        [{"bug": {"title": "Bug"}, "feature": {"title": "Feature"}}],
        ["bug", "feature"],
        ["bug", "urgent"],
        id="titled-multi-select",
    ),
]


@pytest.mark.parametrize("response_type, valid, invalid", CHOICE_SHORTHANDS)
def test_choice_shorthand_accepts_offered_choices(response_type, valid, invalid):
    config = parse_elicit_response_type(response_type)

    result = handle_elicit_accept(config, {"value": valid})

    assert result.data == valid


@pytest.mark.parametrize("response_type, valid, invalid", CHOICE_SHORTHANDS)
def test_choice_shorthand_rejects_values_not_offered(response_type, valid, invalid):
    config = parse_elicit_response_type(response_type)

    with pytest.raises(ValidationError):
        handle_elicit_accept(config, {"value": invalid})


def test_integer_choices_render_as_string_enum_and_accept_string_values():
    """MCP 2025-11-25 enums are string-only: integer choices go on the wire
    as string enums, and a spec client's string answer coerces back."""
    from typing import Literal

    class Rating(Enum):
        LOW = 1
        HIGH = 2

    lit_config = parse_elicit_response_type(Literal[1, 2])
    assert lit_config.schema["properties"]["value"] == {
        "enum": ["1", "2"],
        "title": "Value",
        "type": "string",
    }
    assert handle_elicit_accept(lit_config, {"value": "1"}).data == 1
    # A numeric answer still validates directly.
    assert handle_elicit_accept(lit_config, {"value": 2}).data == 2
    # A value that was never offered is still rejected, not coerced.
    with pytest.raises(ValidationError):
        handle_elicit_accept(lit_config, {"value": "3"})

    enum_config = parse_elicit_response_type(Rating)
    assert enum_config.schema["properties"]["value"] == {
        "enum": ["1", "2"],
        "title": "Rating",
        "type": "string",
    }
    assert handle_elicit_accept(enum_config, {"value": "2"}).data is Rating.HIGH


def test_single_integer_literal_renders_as_string_enum():
    """A one-option integer choice is a string enum, not an integer `const`."""
    from typing import Literal

    config = parse_elicit_response_type(Literal[1])
    assert config.schema["properties"]["value"] == {
        "enum": ["1"],
        "title": "Value",
        "type": "string",
    }
    assert handle_elicit_accept(config, {"value": "1"}).data == 1


def test_numeric_multi_select_renders_as_string_enum():
    """Multi-select integer/float choices use string enum items."""
    from typing import Literal

    int_config = parse_elicit_response_type(list[Literal[1, 2]])
    assert int_config.schema["properties"]["value"] == {
        "type": "array",
        "items": {"enum": ["1", "2"], "type": "string"},
        "title": "Value",
    }
    assert handle_elicit_accept(int_config, {"value": ["2", "1"]}).data == [2, 1]

    class Score(Enum):
        LOW = 1.5
        HIGH = 2.5

    float_config = parse_elicit_response_type(list[Score])
    assert float_config.schema["properties"]["value"]["items"] == {
        "enum": ["1.5", "2.5"],
        "title": "Score",
        "type": "string",
    }
    assert handle_elicit_accept(float_config, {"value": ["2.5"]}).data == [Score.HIGH]


def test_float_and_mixed_literals_render_as_string_enum():
    """Float enums and mixed numeric/string literals are string enums too."""
    from typing import Literal

    class Score(Enum):
        LOW = 1.5
        HIGH = 2.5

    float_config = parse_elicit_response_type(Score)
    assert float_config.schema["properties"]["value"] == {
        "enum": ["1.5", "2.5"],
        "title": "Score",
        "type": "string",
    }
    assert handle_elicit_accept(float_config, {"value": "2.5"}).data is Score.HIGH

    mixed_config = parse_elicit_response_type(Literal[1, "a"])
    assert mixed_config.schema["properties"]["value"] == {
        "enum": ["1", "a"],
        "title": "Value",
        "type": "string",
    }
    assert handle_elicit_accept(mixed_config, {"value": "1"}).data == 1
    assert handle_elicit_accept(mixed_config, {"value": "a"}).data == "a"


def test_integer_enum_default_is_stringified():
    """A defaulted integer-enum field carries its default as a string."""

    class Rating(Enum):
        LOW = 1
        HIGH = 2

    class Model(BaseModel):
        rating: Rating = Field(default=Rating.LOW)

    schema = get_elicitation_schema(Model)
    assert schema["properties"]["rating"] == {
        "enum": ["1", "2"],
        "title": "Rating",
        "type": "string",
        "default": "1",
    }

    config = parse_elicit_response_type(Model)
    assert handle_elicit_accept(config, {}).data == Model(rating=Rating.LOW)
    assert handle_elicit_accept(config, {"rating": "2"}).data == Model(
        rating=Rating.HIGH
    )


def test_numeric_string_choice_beside_integer_choice_is_not_coerced():
    """A string choice that looks numeric ("10") must survive beside an
    integer choice: only the field validation failed on is coerced."""
    from typing import Literal

    class Model(BaseModel):
        count: Literal[1, 2]
        code: Literal["10", "20"]

    config = parse_elicit_response_type(Model)
    assert handle_elicit_accept(config, {"count": "1", "code": "10"}).data == Model(
        count=1, code="10"
    )
    with pytest.raises(ValidationError):
        handle_elicit_accept(config, {"count": "1", "code": "99"})


def test_multi_select_default_is_stringified():
    """A defaulted multi-select carries its default as string choices."""
    from typing import Literal

    class Model(BaseModel):
        choices: list[Literal[1, 2]] = Field(default=[1])

    schema = get_elicitation_schema(Model)
    assert schema["properties"]["choices"] == {
        "default": ["1"],
        "items": {"enum": ["1", "2"], "type": "string"},
        "title": "Choices",
        "type": "array",
    }

    config = parse_elicit_response_type(Model)
    assert handle_elicit_accept(config, {}).data == Model(choices=[1])
    assert handle_elicit_accept(config, {"choices": ["2"]}).data == Model(choices=[2])


def test_mixed_multi_select_coerces_only_failing_items():
    """In a mixed multi-select only the failing items coerce: a genuine
    numeric-looking string choice ("10") next to an integer choice survives."""
    from typing import Literal

    class Model(BaseModel):
        choices: list[Literal[1, "10"]]

    config = parse_elicit_response_type(Model)
    assert config.schema["properties"]["choices"]["items"] == {
        "enum": ["1", "10"],
        "type": "string",
    }
    assert handle_elicit_accept(config, {"choices": ["1", "10"]}).data == Model(
        choices=[1, "10"]
    )
    with pytest.raises(ValidationError):
        handle_elicit_accept(config, {"choices": ["3", "10"]})


def test_aliased_integer_choice_coerces():
    """An aliased field coerces too: error locations report the field name
    when the model sets loc_by_alias=False, while the schema uses the alias."""
    from typing import Literal

    from pydantic import ConfigDict

    class Model(BaseModel):
        model_config = ConfigDict(loc_by_alias=False)
        choice: Literal[1, 2] = Field(alias="selection")

    config = parse_elicit_response_type(Model)
    assert config.schema["properties"]["selection"] == {
        "enum": ["1", "2"],
        "title": "Selection",
        "type": "string",
    }
    assert handle_elicit_accept(config, {"selection": "1"}).data == (
        Model.model_validate({"selection": 1})
    )


def test_aliased_fields_do_not_mistranslate_by_default():
    """With the default loc_by_alias=True, error locations already use
    aliases, so no translation happens — even when one field's name is
    another field's alias."""
    from typing import Literal

    class Model(BaseModel):
        first: Literal[1] = Field(alias="second")
        second: Literal[2] = Field(alias="third")

    config = parse_elicit_response_type(Model)
    assert handle_elicit_accept(
        config, {"second": "1", "third": "2"}
    ).data == Model.model_validate({"second": 1, "third": 2})


def test_alias_choices_integer_choice_coerces():
    """An AliasChoices validation alias names the schema property by its
    first choice, and coercion follows it when loc_by_alias=False."""
    from typing import Literal

    from pydantic import AliasChoices, ConfigDict

    class Model(BaseModel):
        model_config = ConfigDict(loc_by_alias=False)
        choice: Literal[1, 2] = Field(
            validation_alias=AliasChoices("wire", "alternate")
        )

    config = parse_elicit_response_type(Model)
    assert config.schema["properties"]["wire"] == {
        "enum": ["1", "2"],
        "title": "Wire",
        "type": "string",
    }
    assert handle_elicit_accept(config, {"wire": "1"}).data == (
        Model.model_validate({"wire": 1})
    )


def test_alias_choices_skips_multi_segment_alias_path():
    """A multi-segment AliasPath inside AliasChoices does not name the
    schema property — the next string choice does — so coercion follows
    that, not the path's first segment."""
    from typing import Literal

    from pydantic import AliasChoices, AliasPath, ConfigDict

    class Model(BaseModel):
        model_config = ConfigDict(loc_by_alias=False)
        choice: Literal[1, 2] = Field(
            validation_alias=AliasChoices(AliasPath("nested", "choice"), "choice")
        )

    config = parse_elicit_response_type(Model)
    assert config.schema["properties"]["choice"] == {
        "enum": ["1", "2"],
        "title": "Choice",
        "type": "string",
    }
    assert handle_elicit_accept(config, {"choice": "1"}).data == (
        Model.model_validate({"choice": 1})
    )


async def test_integer_choice_round_trips_through_client_as_string():
    """End to end: a spec client answers the string enum with a string."""

    class Rating(Enum):
        LOW = 1
        HIGH = 2

    mcp = FastMCP("elicit-int")

    @mcp.tool
    async def ask(ctx: Context) -> str:
        result = await ctx.elicit("pick one", response_type=Rating)
        assert result.action == "accept"
        assert isinstance(result, AcceptedElicitation)
        return f"got {result.data.name}"

    async def elicitation_handler(message, response_type, params, context):
        # A spec client sees the string enum and answers with a string.
        assert params.requested_schema["properties"]["value"] == {
            "enum": ["1", "2"],
            "title": "Rating",
            "type": "string",
        }
        return {"value": "2"}

    async with Client(
        mcp, mode="legacy", elicitation_handler=elicitation_handler
    ) as client:
        result = await client.call_tool("ask")
        assert result.data == "got HIGH"


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


def test_scalar_elicitation_schema_omits_wrapper_title() -> None:
    """Scalar/list wrappers must not leak the internal class name on the wire."""
    from fastmcp.server.elicitation import parse_elicit_response_type

    schema = parse_elicit_response_type(["yes", "no"]).schema

    assert "title" not in schema
    assert schema["properties"]["value"]["enum"] == ["yes", "no"]
