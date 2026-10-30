"""Hydration of ``Client.call_tool(...).data`` from the server's output schema.

The client rebuilds Python types from the output schema, so every ``format``
and positional item schema the server emits has to survive that round trip.
"""

from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel

from fastmcp import Client, FastMCP
from fastmcp.tools import Tool


class Reading(BaseModel):
    ts: datetime
    day: date
    at: time
    dur: timedelta
    ident: UUID


class Item(BaseModel):
    name: str
    when: date


class TreeNode(BaseModel):
    value: int
    children: list["TreeNode"] = []


class TestStructuredResultHydration:
    @pytest.mark.parametrize(
        ("field_schema", "value", "expected"),
        [
            ({"enum": [1, True]}, 1.0, 1),
            ({"enum": [True, 1]}, 1.0, 1),
            ({"enum": [1, True]}, 1, 1),
            ({"enum": [1, True]}, True, True),
            ({"enum": [0, False]}, 0.0, 0),
            ({"enum": [False, 0]}, 0.0, 0),
            ({"enum": [0, False]}, False, False),
            ({"enum": [1.0, True]}, 1, 1.0),
            ({"enum": [True, 1.0]}, 1, 1.0),
            ({"const": 1}, 1.0, 1),
            ({"const": 1.0}, 1, 1.0),
            ({"anyOf": [{"const": 1}, {}]}, True, True),
            ({"anyOf": [{"const": {}}, {"const": 1}]}, 1, 1),
            ({"anyOf": [{"const": []}, {"const": 1}]}, [], []),
        ],
    )
    async def test_const_enum_results_preserve_json_value_types(
        self, field_schema: dict[str, Any], value: Any, expected: Any
    ) -> None:
        mcp = FastMCP()

        @mcp.tool(
            output_schema={
                "type": "object",
                "properties": {"value": field_schema},
                "required": ["value"],
            }
        )
        def constrained_result() -> dict[str, Any]:
            return {"value": value}

        async with Client(mcp) as client:
            result = await client.call_tool("constrained_result")

        assert result.structured_content == {"value": value}
        assert type(result.structured_content["value"]) is type(value)
        assert result.data.value == expected
        assert type(result.data.value) is type(expected)

    async def test_annotated_scalar_formats_arrive_as_python_types(self):
        mcp = FastMCP()

        @mcp.tool
        def read() -> Reading:
            return Reading(
                ts=datetime(2024, 1, 1, 12),
                day=date(2024, 1, 1),
                at=time(1, 2),
                dur=timedelta(days=1),
                ident=UUID(int=7),
            )

        async with Client(mcp) as client:
            data = (await client.call_tool("read")).data

        assert data.ts == datetime(2024, 1, 1, 12)
        assert data.day == date(2024, 1, 1)
        assert data.at == time(1, 2)
        assert data.dur == timedelta(days=1)
        assert data.ident == UUID(int=7)

    async def test_fixed_length_tuple_result_keeps_positions(self):
        mcp = FastMCP()

        @mcp.tool
        def first_seen() -> tuple[date, int]:
            return (date(2026, 8, 27), 3)

        async with Client(mcp) as client:
            data = (await client.call_tool("first_seen")).data

        assert data == (date(2026, 8, 27), 3)

    async def test_formats_hydrate_inside_collections(self):
        mcp = FastMCP()

        @mcp.tool
        def recent() -> dict[str, list[date]]:
            return {"days": [date(2026, 8, 27), date(2026, 8, 28)]}

        async with Client(mcp) as client:
            data = (await client.call_tool("recent")).data

        assert data["days"] == [date(2026, 8, 27), date(2026, 8, 28)]


class TestWrappedResultKeepsDefs:
    """A non-object return is wrapped as `{"result": ...}` and its `$defs`
    stay at the root of the output schema, beside `properties.result`."""

    @pytest.mark.parametrize("dereference_schemas", [True, False])
    async def test_list_of_models_hydrates(self, dereference_schemas: bool):
        mcp = FastMCP(dereference_schemas=dereference_schemas)

        @mcp.tool
        def items() -> list[Item]:
            return [Item(name="a", when=date(2024, 1, 2))]

        async with Client(mcp) as client:
            data = (await client.call_tool("items")).data

        assert isinstance(data, list)
        assert not isinstance(data[0], dict)
        assert data[0].name == "a"
        assert data[0].when == date(2024, 1, 2)

    @pytest.mark.parametrize("dereference_schemas", [True, False])
    async def test_optional_model_hydrates(self, dereference_schemas: bool):
        mcp = FastMCP(dereference_schemas=dereference_schemas)

        @mcp.tool
        def maybe_item() -> Item | None:
            return Item(name="a", when=date(2024, 1, 2))

        async with Client(mcp) as client:
            data = (await client.call_tool("maybe_item")).data

        assert not isinstance(data, dict)
        assert data.name == "a"
        assert data.when == date(2024, 1, 2)

    @pytest.mark.parametrize("dereference_schemas", [True, False])
    async def test_list_of_recursive_models_hydrates(self, dereference_schemas: bool):
        mcp = FastMCP(dereference_schemas=dereference_schemas)

        @mcp.tool
        def trees() -> list[TreeNode]:
            return [TreeNode(value=1, children=[TreeNode(value=2)])]

        async with Client(mcp) as client:
            data = (await client.call_tool("trees")).data

        assert isinstance(data, list)
        assert not isinstance(data[0], dict)
        assert data[0].value == 1
        assert not isinstance(data[0].children[0], dict)
        assert data[0].children[0].value == 2

    async def test_boolean_result_schema_accepts_any_value(self):
        mcp = FastMCP()

        def example() -> str:
            return "example"

        mcp.add_tool(
            Tool.from_function(
                example,
                output_schema={
                    "type": "object",
                    "properties": {"result": True},
                    "required": ["result"],
                    "x-fastmcp-wrap-result": True,
                },
            )
        )

        async with Client(mcp) as client:
            data = (await client.call_tool("example")).data

        assert data == "example"
