"""A custom `transform_fn` receives arguments validated against its signature.

A transformed tool advertises an input schema built from its `transform_fn`'s
type hints, so the function must receive values converted to those hints, the
same way a plain tool function does. See #6796.
"""

import datetime
import uuid
from enum import Enum
from typing import Annotated, Any

import pytest
from mcp_types import TextContent
from pydantic import BaseModel, BeforeValidator, Field
from pydantic import ValidationError as PydanticValidationError

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.exceptions import ValidationError
from fastmcp.server.context import Context
from fastmcp.server.transforms import Namespace
from fastmcp.tools import Tool, forward
from fastmcp.tools.base import ToolResult
from fastmcp.tools.function_tool import _ToolBodyError
from fastmcp.tools.tool_transform import ArgTransform
from fastmcp.utilities.types import get_cached_typeadapter


class Point(BaseModel):
    x: int
    y: int


class Color(Enum):
    RED = "red"


def _parent(p: Point, when: datetime.date) -> str:
    return f"parent p={type(p).__name__} when={type(when).__name__}"


PARENT = Tool.from_function(_parent)
VALID = {"p": {"x": 1, "y": 2}, "when": "2024-03-01"}


async def _describe_async(p: Point, when: datetime.date) -> str:
    return f"p={type(p).__name__} when={type(when).__name__}"


def _describe_sync(p: Point, when: datetime.date) -> str:
    return f"p={type(p).__name__} when={type(when).__name__}"


def _text(result: Any) -> str:
    assert isinstance(result.content[0], TextContent)
    return result.content[0].text


@pytest.mark.parametrize("transform_fn", [_describe_async, _describe_sync])
async def test_transform_fn_receives_converted_values(transform_fn):
    tool = Tool.from_tool(PARENT, name="described", transform_fn=transform_fn)
    mcp = FastMCP()
    mcp.add_tool(tool)

    async with Client(mcp) as client:
        result = await client.call_tool("described", VALID)

    assert _text(result) == "p=Point when=date"


async def test_transform_fn_converts_like_a_plain_tool():
    async def transform(
        stamp: datetime.datetime,
        ident: uuid.UUID,
        color: Color,
        ratio: float,
        positive: Annotated[int, Field(gt=0)],
    ) -> str:
        kinds = [stamp, ident, color, ratio, positive]
        return " ".join(type(value).__name__ for value in kinds)

    def parent(
        stamp: datetime.datetime,
        ident: uuid.UUID,
        color: Color,
        ratio: float,
        positive: Annotated[int, Field(gt=0)],
    ) -> str:
        return "parent"

    tool = Tool.from_tool(Tool.from_function(parent), transform_fn=transform)
    result = await tool.run(
        {
            "stamp": "2024-03-01T12:00:00",
            "ident": "12345678-1234-5678-1234-567812345678",
            "color": "red",
            "ratio": 3,
            "positive": 5,
        }
    )

    assert _text(result) == "datetime UUID Color float int"


@pytest.mark.parametrize("is_async", [True, False])
async def test_invalid_arguments_are_rejected_before_the_body_runs(is_async):
    calls: list[str] = []

    async def async_body(p: Point, when: datetime.date) -> str:
        calls.append("ran")
        return "ran"

    def sync_body(p: Point, when: datetime.date) -> str:
        calls.append("ran")
        return "ran"

    body = async_body if is_async else sync_body
    tool = Tool.from_tool(PARENT, transform_fn=body)

    with pytest.raises(ValidationError):
        await tool.run({"p": {"x": "bad", "y": 2}, "when": "not-a-date"})
    assert calls == []


async def test_constraints_on_transform_fn_parameters_are_enforced():
    async def transform(positive: Annotated[int, Field(gt=0)]) -> int:
        return positive

    def parent(positive: int) -> int:
        return positive

    tool = Tool.from_tool(Tool.from_function(parent), transform_fn=transform)

    with pytest.raises(ValidationError):
        await tool.run({"positive": -1})


async def test_validators_on_transform_fn_parameters_run():
    async def transform(note: Annotated[str, BeforeValidator(str.strip)]) -> str:
        return note

    def parent(note: str) -> str:
        return note

    tool = Tool.from_tool(Tool.from_function(parent), transform_fn=transform)

    assert _text(await tool.run({"note": "  hi  "})) == "hi"


async def test_unknown_argument_is_a_bad_call():
    tool = Tool.from_tool(PARENT, transform_fn=_describe_async)

    with pytest.raises(ValidationError):
        await tool.run({**VALID, "extra": 1})


async def test_strict_input_validation_applies_to_transform_fn():
    async def transform(n: int) -> int:
        return n

    def parent(n: int) -> int:
        return n

    strict = FastMCP(strict_input_validation=True)
    strict.add_tool(Tool.from_tool(Tool.from_function(parent), transform_fn=transform))
    lax = FastMCP()
    lax.add_tool(Tool.from_tool(Tool.from_function(parent), transform_fn=transform))

    async with Client(strict) as client:
        result = await client.call_tool("parent", {"n": "42"}, raise_on_error=False)
    assert result.is_error

    async with Client(lax) as client:
        result = await client.call_tool("parent", {"n": "42"})
    assert result.structured_content == {"result": 42}


async def test_arg_transform_type_is_validated_for_transform_fn():
    """`ArgTransform(type=...)` sets the advertised type, so it is the one validated."""

    def parent(x: str) -> str:
        return x

    async def transform(count) -> str:
        return type(count).__name__

    tool = Tool.from_tool(
        Tool.from_function(parent),
        transform_fn=transform,
        transform_args={"x": ArgTransform(name="count", type=int)},
    )

    assert _text(await tool.run({"count": "5"})) == "int"
    with pytest.raises(ValidationError):
        await tool.run({"count": "not-a-number"})


async def test_validation_target_is_built_once_for_copied_tools():
    """Namespace serves a copy of the tool per call; copies share one target."""

    def parent(x: str, limit: int = 5) -> str:
        return f"{x} {limit}"

    async def transform(count, limit: int = 5) -> str:
        return f"{type(count).__name__} {limit}"

    mcp = FastMCP()
    mcp.add_tool(
        Tool.from_tool(
            Tool.from_function(parent),
            name="counted",
            transform_fn=transform,
            transform_args={"x": ArgTransform(name="count", type=int)},
        )
    )
    mcp.add_transform(Namespace("ns"))

    async with Client(mcp) as client:
        await client.call_tool("ns_counted", {"count": "1"})
        misses = get_cached_typeadapter.cache_info().misses
        for n in range(3):
            result = await client.call_tool("ns_counted", {"count": str(n)})

    assert _text(result) == "int 5"
    assert get_cached_typeadapter.cache_info().misses == misses


async def test_omitted_argument_takes_the_parent_default_unvalidated():
    """An advertised default is applied as-is, like a plain tool's default."""

    def search(q: str, limit: int | None = None) -> str:
        return f"q={q} limit={limit}"

    async def narrowed(q: str, limit: int = 10) -> ToolResult:
        return await forward(q=q, limit=limit)

    tool = Tool.from_tool(Tool.from_function(search), transform_fn=narrowed)

    assert _text(await tool.run({"q": "hi"})) == "q=hi limit=None"
    assert _text(await tool.run({"q": "hi", "limit": 3})) == "q=hi limit=3"


async def test_omitted_argument_with_a_none_default_is_not_validated():
    async def transform(x: int = None) -> str:  # ty: ignore[invalid-parameter-default]
        return f"x={x}"

    def parent(x: int | None = None) -> str:
        return "parent"

    tool = Tool.from_tool(Tool.from_function(parent), transform_fn=transform)

    assert _text(await tool.run({})) == "x=None"


async def test_arg_transform_default_is_applied_as_its_python_value():
    def parent(ident: uuid.UUID, when: datetime.date) -> str:
        return f"parent ident={type(ident).__name__} when={when.isoformat()}"

    async def transform(ident: uuid.UUID, when: datetime.date) -> ToolResult:
        return await forward(ident=ident, when=when)

    mcp = FastMCP(strict_input_validation=True)
    mcp.add_tool(
        Tool.from_tool(
            Tool.from_function(parent),
            name="dated",
            transform_fn=transform,
            transform_args={"when": ArgTransform(default=datetime.date(2024, 1, 1))},
        )
    )

    async with Client(mcp) as client:
        result = await client.call_tool(
            "dated", {"ident": "12345678-1234-5678-1234-567812345678"}
        )

    assert _text(result) == "parent ident=UUID when=2024-01-01"


async def test_field_constraints_survive_an_advertised_default():
    async def transform(n: int = Field(default=5, gt=0)) -> int:
        return n

    def parent(n: int = 1) -> int:
        return n

    tool = Tool.from_tool(
        Tool.from_function(parent),
        transform_fn=transform,
        transform_args={"n": ArgTransform(default=7)},
    )

    assert (await tool.run({})).structured_content == {"result": 7}
    with pytest.raises(ValidationError):
        await tool.run({"n": -1})


async def test_field_factory_constraints_survive_an_inherited_default():
    async def transform(
        tags: list[str] = Field(default_factory=list, max_length=1),
    ) -> str:
        return ",".join(tags) or "none"

    def parent(tags: list[str] = []) -> str:  # noqa: B006
        return "parent"

    tool = Tool.from_tool(Tool.from_function(parent), transform_fn=transform)

    assert _text(await tool.run({})) == "none"
    with pytest.raises(ValidationError):
        await tool.run({"tags": ["a", "b"]})


async def test_function_default_is_used_when_the_parent_has_none():
    async def transform(p: Point, labels: set[str] = {"b", "a"}) -> str:  # noqa: B006
        return f"{type(labels).__name__} {sorted(labels)}"

    def parent(p: Point) -> str:
        return "parent"

    tool = Tool.from_tool(Tool.from_function(parent), transform_fn=transform)

    assert _text(await tool.run({"p": {"x": 1, "y": 2}})) == "set ['a', 'b']"


async def test_mutable_default_is_not_shared_between_calls():
    async def transform(p: Point, tags: list[str] = []) -> str:  # noqa: B006
        tags.append("seen")
        return ",".join(tags)

    def parent(p: Point, tags: list[str] = []) -> str:  # noqa: B006
        return "parent"

    tool = Tool.from_tool(Tool.from_function(parent), transform_fn=transform)

    assert _text(await tool.run({"p": {"x": 1, "y": 2}})) == "seen"
    assert _text(await tool.run({"p": {"x": 1, "y": 2}})) == "seen"


async def test_omitted_argument_default_is_converted():
    async def transform(
        p: Point, when: datetime.date = datetime.date(2024, 1, 1)
    ) -> str:
        return f"{type(when).__name__} {when.isoformat()}"

    tool = Tool.from_tool(PARENT, transform_fn=transform)

    assert _text(await tool.run({"p": {"x": 1, "y": 2}})) == "date 2024-01-01"


async def test_default_factory_parameter_gets_its_default():
    async def transform(p: Point, tags: list[str] = Field(default_factory=list)) -> str:
        return f"{type(tags).__name__} {tags}"

    def parent(p: Point) -> str:
        return "parent"

    tool = Tool.from_tool(Tool.from_function(parent), transform_fn=transform)

    assert _text(await tool.run({"p": {"x": 1, "y": 2}})) == "list []"


async def test_forward_receives_converted_values():
    async def guard(p: Point, when: datetime.date) -> ToolResult:
        if p.x < 0:
            raise ValueError("x must be non-negative")
        return await forward(p=p, when=when)

    tool = Tool.from_tool(PARENT, transform_fn=guard)

    assert _text(await tool.run(VALID)) == "parent p=Point when=date"


async def test_kwargs_transform_fn_still_forwards_to_the_parent():
    async def passthrough(**kwargs: Any) -> ToolResult:
        return await forward(**kwargs)

    tool = Tool.from_tool(PARENT, transform_fn=passthrough)

    assert _text(await tool.run(VALID)) == "parent p=Point when=date"


async def test_context_parameter_is_injected_and_others_converted():
    async def transform(p: Point, ctx: Context) -> str:
        return f"p={type(p).__name__} ctx={type(ctx).__name__}"

    def parent(p: Point) -> str:
        return "parent"

    mcp = FastMCP()
    mcp.add_tool(Tool.from_tool(Tool.from_function(parent), transform_fn=transform))

    async with Client(mcp) as client:
        result = await client.call_tool("parent", {"p": {"x": 1, "y": 2}})

    assert _text(result) == "p=Point ctx=Context"


@pytest.mark.parametrize("is_async", [True, False])
async def test_pydantic_error_from_the_body_is_not_a_bad_call(is_async):
    """A body error must stay a server error, so `mask_error_details` hides it."""

    async def async_body(p: Point) -> str:
        bad: Any = "not-an-int"
        Point(x=bad, y=0)
        return "unreachable"

    def sync_body(p: Point) -> str:
        bad: Any = "not-an-int"
        Point(x=bad, y=0)
        return "unreachable"

    def parent(p: Point) -> str:
        return "parent"

    body = async_body if is_async else sync_body
    tool = Tool.from_tool(Tool.from_function(parent), transform_fn=body)

    with pytest.raises(_ToolBodyError) as exc_info:
        await tool.run({"p": {"x": 1, "y": 2}})
    assert isinstance(exc_info.value.__cause__, PydanticValidationError)


async def test_masked_body_error_does_not_leak_details():
    async def body(p: Point) -> str:
        bad: Any = "not-an-int"
        Point(x=bad, y=0)
        return "unreachable"

    def parent(p: Point) -> str:
        return "parent"

    mcp = FastMCP(mask_error_details=True)
    mcp.add_tool(Tool.from_tool(Tool.from_function(parent), transform_fn=body))

    async with Client(mcp) as client:
        result = await client.call_tool(
            "parent", {"p": {"x": 1, "y": 2}}, raise_on_error=False
        )

    assert result.is_error
    assert "validation error" not in _text(result).lower()
    assert "not-an-int" not in _text(result)
