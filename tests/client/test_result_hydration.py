"""Hydration of ``Client.call_tool(...).data`` from the server's output schema.

The client rebuilds Python types from the output schema, so every ``format``
and positional item schema the server emits has to survive that round trip.
"""

from datetime import date, datetime, time, timedelta
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict, Field

from fastmcp import Client, FastMCP
from fastmcp.tools import ToolResult


class Reading(BaseModel):
    ts: datetime
    day: date
    at: time
    dur: timedelta
    ident: UUID


class Counters(BaseModel):
    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, int] = Field(init=False)
    name: str


class CounterCollection(BaseModel):
    counters: list[Counters]


@pytest.mark.parametrize("shape", ["model", "list", "nested"])
async def test_typed_extra_fields_survive_tool_result_hydration(shape: str) -> None:
    mcp = FastMCP()
    counters = Counters(name="cache", hits=12, misses=3)

    @mcp.tool
    def model_result() -> Counters:
        return counters

    @mcp.tool
    def list_result() -> list[Counters]:
        return [counters]

    @mcp.tool
    def nested_result() -> CounterCollection:
        return CounterCollection(counters=[counters])

    async with Client(mcp) as client:
        result = await client.call_tool(f"{shape}_result")

    data = result.data
    if shape == "list":
        data = data[0]
    elif shape == "nested":
        data = data.counters[0]

    assert data.name == "cache"
    assert data.hits == 12
    assert type(data.hits) is int
    assert data.misses == 3


class TestStructuredResultHydration:
    @pytest.mark.parametrize("title", ["Node", "Order Item (v2)"])
    async def test_typed_extras_survive_titled_recursive_results(self, title: str):
        mcp = FastMCP()
        schema = {
            "type": "object",
            "title": title,
            "properties": {
                "day": {"type": "string", "format": "date"},
                "children": {"type": "array", "items": {"$ref": "#"}},
            },
            "required": ["day"],
            "additionalProperties": {"type": "integer"},
        }
        payload = {
            "day": "2026-10-17",
            "hits": 12,
            "children": [{"day": "2026-10-18", "misses": 3}],
        }

        @mcp.tool(output_schema=schema)
        def recursive_counters() -> ToolResult:
            return ToolResult(content=[], structured_content=payload)

        async with Client(mcp) as client:
            result = await client.call_tool("recursive_counters")

        assert result.structured_content == payload
        assert result.data.day == date(2026, 10, 17)
        assert result.data.hits == 12
        assert type(result.data.hits) is int
        child = result.data.children[0]
        assert isinstance(child, type(result.data))
        assert child.day == date(2026, 10, 18)
        assert child.misses == 3
        assert type(child.misses) is int

    async def test_invalid_typed_extra_is_rejected_before_hydration(self):
        mcp = FastMCP()
        payload = {"name": "cache", "hits": "many"}

        @mcp.tool(output_schema=Counters.model_json_schema())
        def invalid_counters() -> ToolResult:
            return ToolResult(content=[], structured_content=payload)

        async with Client(mcp) as client:
            with pytest.raises(
                RuntimeError,
                match="Invalid structured content returned by tool invalid_counters",
            ):
                await client.call_tool("invalid_counters")

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
