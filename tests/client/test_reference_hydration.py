"""Client hydration of JSON Schema URI-fragment references."""

import datetime

import pytest

from fastmcp import Client, FastMCP


@pytest.mark.parametrize("dereference_schemas", [False, True])
async def test_client_hydrates_percent_encoded_reference(dereference_schemas: bool):
    server = FastMCP(dereference_schemas=dereference_schemas)

    @server.tool(
        output_schema={
            "type": "object",
            "properties": {"observed_at": {"$ref": "#/$defs/Observation%20time"}},
            "required": ["observed_at"],
            "$defs": {"Observation time": {"type": "string", "format": "date-time"}},
        }
    )
    def reading() -> dict:
        return {"observed_at": "2026-01-01T12:00:00Z"}

    async with Client(server) as client:
        result = await client.call_tool("reading")

    assert result.structured_content == {"observed_at": "2026-01-01T12:00:00Z"}
    assert result.data.observed_at == datetime.datetime(
        2026, 1, 1, 12, tzinfo=datetime.UTC
    )
    assert type(result.data.observed_at) is datetime.datetime


async def test_client_hydrates_reference_with_empty_extra_schema() -> None:
    server = FastMCP(dereference_schemas=False)
    payload = {"details": {"name": "cache", "recorded_at": "2026-11-19"}}

    @server.tool(
        output_schema={
            "type": "object",
            "properties": {
                "details": {
                    "$ref": "#/$defs/Record",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                    "additionalProperties": {},
                }
            },
            "required": ["details"],
            "$defs": {
                "Record": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "recorded_at": {"type": "string", "format": "date"},
                    },
                    "required": ["name", "recorded_at"],
                }
            },
        }
    )
    def reading() -> dict:
        return payload

    async with Client(server) as client:
        tools = await client.list_tools()
        assert tools[0].output_schema is not None
        assert (
            tools[0].output_schema["properties"]["details"]["$ref"] == "#/$defs/Record"
        )
        result = await client.call_tool("reading")

    assert result.structured_content == payload
    assert result.data.details.recorded_at == datetime.date(2026, 11, 19)
    assert type(result.data.details.recorded_at) is datetime.date
