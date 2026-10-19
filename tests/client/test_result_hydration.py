"""Hydration of ``Client.call_tool(...).data`` from the server's output schema.

The client rebuilds Python types from the output schema, so every ``format``
and positional item schema the server emits has to survive that round trip.
"""

from datetime import date, datetime, time, timedelta
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


class Item(BaseModel):
    name: str
    when: date


class TreeNode(BaseModel):
    value: int
    children: list["TreeNode"] = []


class TestStructuredResultHydration:
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
