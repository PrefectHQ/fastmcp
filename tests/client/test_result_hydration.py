"""Hydration of ``Client.call_tool(...).data`` from the server's output schema.

The client rebuilds Python types from the output schema, so every ``format``
and positional item schema the server emits has to survive that round trip.
"""

from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict, Field

from fastmcp import Client, FastMCP
from fastmcp.client.mixins.tools import _hydration_covers_payload, _json_text_decodes_to
from fastmcp.tools import Tool, ToolResult


class Reading(BaseModel):
    ts: datetime
    day: date
    at: time
    dur: timedelta
    ident: UUID


class Counters(BaseModel):
    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, int] = Field(init=False)  # ty:ignore[invalid-mutable-override]
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


class Item(BaseModel):
    name: str
    when: date


class TreeNode(BaseModel):
    value: int
    children: list["TreeNode"] = []


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


class TestUnionHydrationFidelity:
    """``result.data`` is a convenience view of ``structured_content`` and must
    not disagree with what the server sent. A pydantic union can coerce across
    branches (0 → False) or drop keys, so hydration falls back to the payload
    itself when the typed form would change it (#6136)."""

    async def test_anyof_branch_coercion_keeps_server_value(self):
        mcp = FastMCP()

        # Only the first branch accepts {"a": 0}; the second requires a boolean.
        schema = {
            "type": "object",
            "properties": {
                "value": {
                    "anyOf": [
                        {"type": "object", "properties": {"x": {}}},
                        {"type": "object", "properties": {"a": {"type": "boolean"}}},
                    ]
                }
            },
        }

        def get_value() -> dict[str, Any]:
            return {"value": {"a": 0}}

        mcp.add_tool(Tool.from_function(get_value, output_schema=schema))

        async with Client(mcp) as client:
            result = await client.call_tool("get_value")

        assert result.structured_content == {"value": {"a": 0}}
        assert result.data == {"value": {"a": 0}}

    async def test_anyof_string_coercion_keeps_server_value(self):
        mcp = FastMCP()

        # Only the first branch accepts {"a": "5"}; the second coerces the
        # string to an integer. No ``format: json`` anywhere in the schema,
        # so the string the server sent must reach data unchanged (#6136).
        schema = {
            "type": "object",
            "properties": {
                "value": {
                    "anyOf": [
                        {"type": "object", "properties": {"x": {}}},
                        {"type": "object", "properties": {"a": {"type": "integer"}}},
                    ]
                }
            },
        }

        def get_value() -> dict[str, Any]:
            return {"value": {"a": "5"}}

        mcp.add_tool(Tool.from_function(get_value, output_schema=schema))

        async with Client(mcp) as client:
            result = await client.call_tool("get_value")

        assert result.structured_content == {"value": {"a": "5"}}
        assert result.data == {"value": {"a": "5"}}

    async def test_json_format_sibling_does_not_license_coercion(self):
        mcp = FastMCP()

        # A format: json field elsewhere in the schema must not open the
        # decode asymmetry for value: the anyOf coercion of "5" to 5 is
        # still a fidelity loss (#6136).
        schema = {
            "type": "object",
            "properties": {
                "value": {
                    "anyOf": [
                        {"type": "object", "properties": {"x": {}}},
                        {"type": "object", "properties": {"a": {"type": "integer"}}},
                    ]
                },
                "payload": {"type": "string", "format": "json"},
            },
        }

        def get_value() -> dict[str, Any]:
            return {"value": {"a": "5"}}

        mcp.add_tool(Tool.from_function(get_value, output_schema=schema))

        async with Client(mcp) as client:
            result = await client.call_tool("get_value")

        assert result.structured_content == {"value": {"a": "5"}}
        assert result.data == {"value": {"a": "5"}}

    async def test_json_format_field_decodes_beside_ordinary_fields(self):
        mcp = FastMCP()

        # Mixed payload: the format: json field still decodes (its own path
        # asks for it) while the anyOf field keeps the server's string.
        schema = {
            "type": "object",
            "properties": {
                "value": {
                    "anyOf": [
                        {"type": "object"},
                        {"type": "object", "properties": {"a": {"type": "integer"}}},
                    ]
                },
                "payload": {"type": "string", "format": "json"},
            },
        }

        def get_value() -> dict[str, Any]:
            return {"value": {"a": "5"}, "payload": '{"b": 1}'}

        mcp.add_tool(Tool.from_function(get_value, output_schema=schema))

        async with Client(mcp) as client:
            result = await client.call_tool("get_value")

        assert result.structured_content == {
            "value": {"a": "5"},
            "payload": '{"b": 1}',
        }
        assert result.data.value["a"] == "5"
        assert result.data.payload == {"b": 1}

    async def test_unambiguous_anyof_still_hydrates(self):
        mcp = FastMCP()

        schema = {
            "type": "object",
            "properties": {
                "value": {
                    "anyOf": [
                        {"type": "object", "properties": {"a": {"type": "boolean"}}},
                        {"type": "object", "properties": {"n": {"type": "integer"}}},
                    ]
                }
            },
        }

        def get_value() -> dict[str, Any]:
            return {"value": {"n": 3}}

        mcp.add_tool(Tool.from_function(get_value, output_schema=schema))

        async with Client(mcp) as client:
            result = await client.call_tool("get_value")

        assert result.structured_content == {"value": {"n": 3}}
        assert result.data.value.n == 3


class TestHydrationGuardNoFalsePositives:
    """The fidelity guard must not reject hydration the schema supports:
    omitted optional fields (defaults serialize into the dump) and
    equivalent date-time spellings (+00:00 vs Z) both keep their models."""

    async def test_omitted_optional_field_still_hydrates(self):
        mcp = FastMCP()

        schema = {
            "type": "object",
            "properties": {
                "a": {"type": "integer"},
                "note": {"type": "string"},
            },
            "required": ["a"],
        }

        def get_value() -> dict[str, Any]:
            return {"a": 1}

        mcp.add_tool(Tool.from_function(get_value, output_schema=schema))

        async with Client(mcp) as client:
            result = await client.call_tool("get_value")

        assert result.structured_content == {"a": 1}
        # Attribute access and typed values are preserved; the dump may
        # carry the optional field's default without being a mismatch.
        assert result.data.a == 1
        assert result.data.note is None

    async def test_utc_offset_datetime_still_hydrates(self):
        mcp = FastMCP()

        schema = {
            "type": "object",
            "properties": {"ts": {"type": "string", "format": "date-time"}},
            "required": ["ts"],
        }

        def get_value() -> dict[str, Any]:
            return {"ts": "2024-06-01T12:00:00+00:00"}

        mcp.add_tool(Tool.from_function(get_value, output_schema=schema))

        async with Client(mcp) as client:
            result = await client.call_tool("get_value")

        assert result.structured_content == {"ts": "2024-06-01T12:00:00+00:00"}
        # Same instant: pydantic serializes UTC as "Z"; the guard must treat
        # the spellings as equivalent rather than discarding the model.
        assert result.data.ts.year == 2024
        assert result.data.ts.hour == 12


class TestHydrationGuardIntentionalDecodes:
    """Decodes the schema itself calls for must not look like corruption:
    ``format: json`` hydrates JSON text into a value (pydantic Json), while a
    timestamp with sub-microsecond precision cannot survive the round trip
    and keeps the server's exact payload."""

    async def test_format_json_field_still_hydrates(self):
        mcp = FastMCP()

        schema = {
            "type": "object",
            "properties": {"payload": {"type": "string", "format": "json"}},
            "required": ["payload"],
        }

        def get_value() -> dict[str, Any]:
            return {"payload": '{"a": 1}'}

        mcp.add_tool(Tool.from_function(get_value, output_schema=schema))

        async with Client(mcp) as client:
            result = await client.call_tool("get_value")

        assert result.structured_content == {"payload": '{"a": 1}'}
        # format: json is documented to decode: the typed value is the
        # parsed object, not the raw text.
        assert result.data.payload == {"a": 1}

    async def test_format_json_scalar_and_bool_decode(self):
        mcp = FastMCP()

        schema = {
            "type": "object",
            "properties": {
                "count": {"type": "string", "format": "json"},
                "flag": {"type": "string", "format": "json"},
            },
            "required": ["count", "flag"],
        }

        def get_value() -> dict[str, Any]:
            # JSON booleans stay strict: the text "0" is a number, not False.
            return {"count": "3", "flag": "true"}

        mcp.add_tool(Tool.from_function(get_value, output_schema=schema))

        async with Client(mcp) as client:
            result = await client.call_tool("get_value")

        assert result.structured_content == {"count": "3", "flag": "true"}
        assert result.data.count == 3
        assert result.data.flag is True

    def test_decoded_boolean_comparison_is_type_strict(self):
        # 0 == False in Python: JSON text that decodes to a number must
        # never stand in for a boolean (the #6136 corruption shape).
        assert _json_text_decodes_to("0", False) is False
        assert _json_text_decodes_to("true", True) is True
        assert _json_text_decodes_to('{"a": 1}', {"a": 1}) is True
        assert _json_text_decodes_to("no json", {"a": 1}) is False
        assert _hydration_covers_payload(False, "0") is False
        assert _hydration_covers_payload(True, "true") is True

    async def test_sub_microsecond_timestamp_keeps_server_string(self):
        mcp = FastMCP()

        schema = {
            "type": "object",
            "properties": {"ts": {"type": "string", "format": "date-time"}},
            "required": ["ts"],
        }

        def get_value() -> dict[str, Any]:
            # Seven fractional digits: pydantic's datetime truncates to
            # microseconds, so hydration would lose what the server sent.
            return {"ts": "2024-06-01T12:00:00.1234567+00:00"}

        mcp.add_tool(Tool.from_function(get_value, output_schema=schema))

        async with Client(mcp) as client:
            result = await client.call_tool("get_value")

        assert result.structured_content == {"ts": "2024-06-01T12:00:00.1234567+00:00"}
        # The lossy datetime is rejected; the payload survives verbatim.
        assert result.data == {"ts": "2024-06-01T12:00:00.1234567+00:00"}

    async def test_microsecond_timestamp_still_hydrates(self):
        mcp = FastMCP()

        schema = {
            "type": "object",
            "properties": {"ts": {"type": "string", "format": "date-time"}},
            "required": ["ts"],
        }

        def get_value() -> dict[str, Any]:
            return {"ts": "2024-06-01T12:00:00.123456+00:00"}

        mcp.add_tool(Tool.from_function(get_value, output_schema=schema))

        async with Client(mcp) as client:
            result = await client.call_tool("get_value")

        assert result.structured_content == {"ts": "2024-06-01T12:00:00.123456+00:00"}
        # Microsecond precision survives the round trip intact.
        assert result.data.ts.year == 2024
        assert result.data.ts.microsecond == 123456
