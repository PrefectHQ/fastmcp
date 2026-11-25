"""JSON Pointer references that traverse arrays in schemas."""

from datetime import date
from typing import Any

import pytest
from pydantic import TypeAdapter

from fastmcp import Client, FastMCP
from fastmcp.tools import ToolResult
from fastmcp.utilities.json_schema_type import json_schema_to_type


@pytest.mark.parametrize(
    ("template", "pointer"),
    [
        (
            {"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}]},
            "anyOf/0",
        ),
        (
            {"oneOf": [{"type": "null"}, {"type": "string", "format": "date"}]},
            "oneOf/1",
        ),
        (
            {"type": "array", "prefixItems": [{"type": "string", "format": "date"}]},
            "prefixItems/0",
        ),
        (
            {"anyOf": [{"anyOf": [{"type": "string", "format": "date"}]}]},
            "anyOf/0/anyOf/0",
        ),
    ],
)
def test_reference_traverses_array(template: dict[str, Any], pointer: str) -> None:
    schema = {
        "type": "object",
        "properties": {
            "template/name~": template,
            "day": {"$ref": f"#/properties/template~1name~0/{pointer}"},
        },
        "required": ["day"],
    }
    adapter = TypeAdapter(json_schema_to_type(schema))

    value = adapter.validate_python({"day": "2026-10-17"})

    assert value.day == date(2026, 10, 17)
    assert type(value.day) is date


@pytest.mark.parametrize("dereference_schemas", [False, True])
async def test_tool_result_hydrates_array_reference(dereference_schemas: bool) -> None:
    server = FastMCP(dereference_schemas=dereference_schemas)
    schema = {
        "type": "object",
        "properties": {
            "template": {
                "anyOf": [{"type": "string", "format": "date"}, {"type": "null"}]
            },
            "day": {"$ref": "#/properties/template/anyOf/0"},
        },
        "required": ["day"],
    }

    @server.tool(output_schema=schema)
    def get_day() -> ToolResult:
        return ToolResult(structured_content={"day": "2026-10-17"})

    async with Client(server) as client:
        result = await client.call_tool("get_day")

    assert not result.is_error
    assert result.structured_content == {"day": "2026-10-17"}
    assert result.data.day == date(2026, 10, 17)
    assert type(result.data.day) is date


@pytest.mark.parametrize("index", ["-1", "+0", "01", "٠", "-", "2", "not-an-index"])
def test_reference_rejects_invalid_array_index(index: str) -> None:
    schema = {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "$ref": f"#/anyOf/{index}",
    }

    with pytest.raises(ValueError, match="Invalid array index"):
        json_schema_to_type(schema)


@pytest.mark.parametrize("key", ["0", "01", "-1"])
def test_reference_keeps_numeric_object_keys(key: str) -> None:
    schema = {
        "$defs": {key: {"type": "string", "format": "date"}},
        "$ref": f"#/$defs/{key}",
    }
    adapter = TypeAdapter(json_schema_to_type(schema))

    assert adapter.validate_python("2026-10-17") == date(2026, 10, 17)
