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
        2026, 1, 1, 12, tzinfo=datetime.timezone.utc
    )
    assert type(result.data.observed_at) is datetime.datetime
