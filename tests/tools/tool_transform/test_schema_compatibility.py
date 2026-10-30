"""Consumer-visible schema contracts for transform_fn reannotations."""

from typing import Annotated, Any, Literal

import jsonschema
import pytest
from pydantic import Field

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool, ToolResult, forward
from fastmcp.tools.function_tool import FunctionTool
from fastmcp.tools.tool_transform import ArgTransform


@pytest.mark.parametrize("with_kwargs", [False, True])
async def test_nullable_integer_reannotation_preserves_parent_bounds(
    with_kwargs: bool,
) -> None:
    """A repeated nullable type must still advertise the bounds enforced by the parent."""

    def parent(n: Annotated[int | None, Field(ge=1, le=10)]) -> str:
        return "none" if n is None else str(n)

    async def child(n: int | None) -> ToolResult:
        return await forward(n=n)

    async def child_with_kwargs(n: int | None, **kwargs: Any) -> ToolResult:
        return await forward(n=n, **kwargs)

    parent_tool = Tool.from_function(parent)
    assert isinstance(parent_tool, FunctionTool)
    transformed = Tool.from_tool(
        parent_tool, transform_fn=child_with_kwargs if with_kwargs else child
    )

    async with Client(FastMCP(tools=[transformed])) as client:
        assert (await client.call_tool("parent", {"n": 3})).data == "3"
        assert (await client.call_tool("parent", {"n": None})).data == "none"
        with pytest.raises(ToolError):
            await client.call_tool("parent", {"n": 0})
        advertised = (await client.list_tools())[0].input_schema

    validator = jsonschema.Draft202012Validator(advertised)
    assert validator.is_valid({"n": None})
    assert validator.is_valid({"n": 1})
    assert validator.is_valid({"n": 10})
    assert not validator.is_valid({"n": 0})
    assert not validator.is_valid({"n": 11})


@pytest.mark.parametrize("with_kwargs", [False, True])
async def test_list_reannotation_preserves_parent_length_constraint(
    with_kwargs: bool,
) -> None:
    """Repeating list[int] must retain the parent minimum length on the wire."""

    def parent(values: Annotated[list[int], Field(min_length=2)]) -> str:
        return ",".join(str(value) for value in values)

    async def child(values: list[int]) -> ToolResult:
        return await forward(values=values)

    async def child_with_kwargs(values: list[int], **kwargs: Any) -> ToolResult:
        return await forward(values=values, **kwargs)

    transformed = Tool.from_tool(
        Tool.from_function(parent),
        transform_fn=child_with_kwargs if with_kwargs else child,
    )
    async with Client(FastMCP(tools=[transformed])) as client:
        assert (await client.call_tool("parent", {"values": [1, 2]})).data == "1,2"
        with pytest.raises(ToolError):
            await client.call_tool("parent", {"values": [1]})
        advertised = (await client.list_tools())[0].input_schema

    validator = jsonschema.Draft202012Validator(advertised)
    assert validator.is_valid({"values": [1, 2]})
    assert not validator.is_valid({"values": [1]})
    assert not validator.is_valid({"values": ["1", "2"]})


@pytest.mark.parametrize("with_kwargs", [False, True])
async def test_changed_list_items_replace_incompatible_parent_schema(
    with_kwargs: bool,
) -> None:
    """A string-list transform must advertise the string input it converts for the parent."""

    def parent(values: list[int]) -> str:
        return ",".join(str(value) for value in values)

    async def child(values: list[str]) -> ToolResult:
        return await forward(values=[int(value) for value in values])

    async def child_with_kwargs(values: list[str], **kwargs: Any) -> ToolResult:
        return await forward(values=[int(value) for value in values], **kwargs)

    transformed = Tool.from_tool(
        Tool.from_function(parent),
        transform_fn=child_with_kwargs if with_kwargs else child,
    )
    async with Client(FastMCP(tools=[transformed])) as client:
        assert (await client.call_tool("parent", {"values": ["1", "2"]})).data == "1,2"
        advertised = (await client.list_tools())[0].input_schema

    validator = jsonschema.Draft202012Validator(advertised)
    assert validator.is_valid({"values": ["1", "2"]})
    assert not validator.is_valid({"values": [1, 2]})


@pytest.mark.parametrize(
    ("parent_type", "child_type", "allowed", "disallowed"),
    [
        (
            Annotated[list[int] | None, Field(min_length=2)],
            list[int] | None,
            {"value": [1, 2]},
            {"value": [1]},
        ),
        (
            list[list[int]],
            list[list[str]],
            {"value": [["1"]]},
            {"value": [[1]]},
        ),
        (
            dict[str, int],
            dict[str, str],
            {"value": {"one": "1"}},
            {"value": {"one": 1}},
        ),
        (
            tuple[int, int],
            tuple[str, str],
            {"value": ["1", "2"]},
            {"value": [1, 2]},
        ),
        (
            tuple[int, str],
            tuple[str, int],
            {"value": ["1", 2]},
            {"value": [1, "2"]},
        ),
        (
            list[Annotated[str, Field(min_length=2, pattern="^a")]] | None,
            list[str] | None,
            {"value": ["ab"]},
            {"value": ["bb"]},
        ),
        (
            dict[Annotated[str, Field(pattern="^a")], Literal[1, "one"] | None],
            dict[str, int | str | None],
            {"value": {"abc": None}},
            {"value": {"abc": 2}},
        ),
    ],
    ids=[
        "nullable-list-constraint",
        "nested-items",
        "mapping-values",
        "tuple-items",
        "tuple-item-order",
        "nested-string-constraints",
        "key-pattern-preserves-nullable-literal-values",
    ],
)
def test_nested_schema_contracts(
    parent_type: Any,
    child_type: Any,
    allowed: dict[str, Any],
    disallowed: dict[str, Any],
) -> None:
    """Consumers must see compatible constraints and genuinely changed nested types."""

    def parent(value: Any) -> str:
        return str(value)

    async def child(value: Any) -> str:
        return str(value)

    parent.__annotations__["value"] = parent_type
    child.__annotations__["value"] = child_type

    transformed = Tool.from_tool(Tool.from_function(parent), transform_fn=child)
    validator = jsonschema.Draft202012Validator(transformed.parameters)
    assert validator.is_valid(allowed)
    assert not validator.is_valid(disallowed)


@pytest.mark.parametrize("with_kwargs", [False, True])
@pytest.mark.parametrize(
    ("parent_type", "child_type", "allowed", "disallowed"),
    [
        (
            Annotated[int, Field(ge=1, le=10)] | None | str,
            str | int | None,
            2,
            0,
        ),
        (Literal["fast", "slow"] | None, str | None, "fast", "other"),
        (Literal[1, "one"], str | int, "one", 2),
        (Literal[True, "one"], str | bool, True, False),
        (Literal[1, "one"] | None, str | int | None, None, 2),
        (
            list[Literal[1, "one"] | None],
            list[int | str | None],
            [None, "one"],
            ["two"],
        ),
        (
            Annotated[int, Field(ge=1)] | Annotated[int, Field(le=-1)],
            int,
            -1,
            0,
        ),
        (Literal[1] | Literal[2], int, 2, 3),
        (list[Literal[1] | Literal[2]], list[int], [1, 2], [3]),
    ],
    ids=[
        "reordered-union",
        "nullable-literal",
        "mixed-literal",
        "boolean-literal",
        "nullable-mixed-literal",
        "nested-nullable-mixed-literal",
        "same-primitive-constrained-union",
        "same-primitive-literal-union",
        "same-primitive-nested-literal-union",
    ],
)
async def test_union_reannotation_preserves_parent_constraints(
    with_kwargs: bool,
    parent_type: Any,
    child_type: Any,
    allowed: Any,
    disallowed: Any,
) -> None:
    """Union order and Literal values do not change the underlying primitive types."""

    def parent(value: Any) -> str:
        return str(value)

    async def child(value: Any) -> ToolResult:
        return await forward(value=value)

    async def child_with_kwargs(value: Any, **kwargs: Any) -> ToolResult:
        return await forward(value=value, **kwargs)

    parent.__annotations__["value"] = parent_type
    child.__annotations__["value"] = child_type
    child_with_kwargs.__annotations__["value"] = child_type
    transformed = Tool.from_tool(
        Tool.from_function(parent),
        transform_fn=child_with_kwargs if with_kwargs else child,
    )
    async with Client(FastMCP(tools=[transformed])) as client:
        assert (await client.call_tool("parent", {"value": allowed})).data == str(
            allowed
        )
        with pytest.raises(ToolError):
            await client.call_tool("parent", {"value": disallowed})
        advertised = (await client.list_tools())[0].input_schema

    validator = jsonschema.Draft202012Validator(advertised)
    assert validator.is_valid({"value": allowed})
    assert not validator.is_valid({"value": disallowed})


@pytest.mark.parametrize("with_kwargs", [False, True])
async def test_constrained_key_mapping_replaces_changed_value_type(
    with_kwargs: bool,
) -> None:
    """Key patterns must not hide a change in the dictionary's value type."""

    def parent(values: dict[Annotated[str, Field(pattern="^a")], int]) -> str:
        return str(values["abc"])

    async def child(
        values: dict[Annotated[str, Field(pattern="^a")], str],
    ) -> ToolResult:
        return await forward(values={key: int(value) for key, value in values.items()})

    async def child_with_kwargs(
        values: dict[Annotated[str, Field(pattern="^a")], str], **kwargs: Any
    ) -> ToolResult:
        return await forward(
            values={key: int(value) for key, value in values.items()}, **kwargs
        )

    transformed = Tool.from_tool(
        Tool.from_function(parent),
        transform_fn=child_with_kwargs if with_kwargs else child,
    )
    async with Client(FastMCP(tools=[transformed])) as client:
        assert (await client.call_tool("parent", {"values": {"abc": "1"}})).data == "1"
        advertised = (await client.list_tools())[0].input_schema

    validator = jsonschema.Draft202012Validator(advertised)
    assert validator.is_valid({"values": {"abc": "1"}})
    assert not validator.is_valid({"values": {"abc": 1}})


async def test_explicit_type_override_replaces_bounds_and_keeps_renamed_default() -> (
    None
):
    """Explicit type overrides intentionally replace validation even for the same type."""

    def parent(
        n: Annotated[int | None, Field(ge=1, le=10, description="bounded")],
    ) -> str:
        return str(n)

    async def child(value: int | None = 0) -> str:
        return str(value)

    transformed = Tool.from_tool(
        Tool.from_function(parent),
        transform_fn=child,
        transform_args={"n": ArgTransform(name="value", type=int | None)},
    )

    async with Client(FastMCP(tools=[transformed])) as client:
        assert (await client.call_tool("parent", {})).data == "0"
        advertised = (await client.list_tools())[0].input_schema

    assert advertised["properties"]["value"]["description"] == "bounded"
    assert advertised["properties"]["value"]["default"] == 0
    validator = jsonschema.Draft202012Validator(advertised)
    assert validator.is_valid({})
    assert validator.is_valid({"value": 0})
    assert validator.is_valid({"value": None})
