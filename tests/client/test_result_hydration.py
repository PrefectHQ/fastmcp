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


class Reading(BaseModel):
    ts: datetime
    day: date
    at: time
    dur: timedelta
    ident: UUID


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
