"""OpenAPI 3.0 boolean exclusive bounds become JSON Schema numeric bounds."""

from typing import Any

import httpx2
import pytest

from fastmcp import Client, FastMCP
from fastmcp.utilities.openapi.json_schema_converter import (
    convert_openapi_schema_to_json_schema,
)


@pytest.mark.parametrize(
    "schema,expected",
    [
        (
            {"type": "integer", "minimum": 0, "exclusiveMinimum": True},
            {"type": "integer", "exclusiveMinimum": 0},
        ),
        (
            {"type": "integer", "maximum": 10, "exclusiveMaximum": True},
            {"type": "integer", "exclusiveMaximum": 10},
        ),
        (
            {"type": "number", "minimum": 0, "exclusiveMinimum": False},
            {"type": "number", "minimum": 0},
        ),
        (
            {"type": "number", "maximum": 1, "exclusiveMaximum": False},
            {"type": "number", "maximum": 1},
        ),
        ({"type": "number", "exclusiveMinimum": True}, {"type": "number"}),
        (
            {
                "type": "integer",
                "minimum": 1,
                "exclusiveMinimum": True,
                "maximum": 5,
                "exclusiveMaximum": False,
            },
            {"type": "integer", "exclusiveMinimum": 1, "maximum": 5},
        ),
    ],
)
def test_boolean_exclusive_bounds_are_converted(
    schema: dict[str, Any], expected: dict[str, Any]
):
    assert convert_openapi_schema_to_json_schema(schema, "3.0.3") == expected


def test_nested_boolean_exclusive_bounds_are_converted():
    schema = {
        "type": "object",
        "properties": {
            "tags": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0, "exclusiveMinimum": True},
            }
        },
        "$defs": {"Ratio": {"type": "number", "maximum": 1, "exclusiveMaximum": True}},
    }
    result = convert_openapi_schema_to_json_schema(schema, "3.0.3")
    assert result["properties"]["tags"]["items"] == {
        "type": "integer",
        "exclusiveMinimum": 0,
    }
    assert result["$defs"]["Ratio"] == {"type": "number", "exclusiveMaximum": 1}


def test_numeric_exclusive_bounds_are_unchanged():
    schema = {"type": "integer", "exclusiveMinimum": 0, "exclusiveMaximum": 10}
    assert convert_openapi_schema_to_json_schema(schema, "3.1.0") == schema


async def test_tool_with_boolean_exclusive_bound_is_callable():
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Bounds", "version": "1"},
        "paths": {
            "/items": {
                "get": {
                    "operationId": "list_items",
                    "parameters": [
                        {
                            "name": "limit",
                            "in": "query",
                            "schema": {
                                "type": "integer",
                                "minimum": 0,
                                "exclusiveMinimum": True,
                                "maximum": 10,
                                "exclusiveMaximum": True,
                            },
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "limit": {
                                                "type": "integer",
                                                "minimum": 0,
                                                "exclusiveMinimum": True,
                                            }
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"limit": int(request.url.params["limit"])})

    http = httpx2.AsyncClient(
        base_url="http://test", transport=httpx2.MockTransport(handler)
    )
    async with Client(FastMCP.from_openapi(openapi_spec=spec, client=http)) as client:
        (tool,) = await client.list_tools()
        limit = tool.input_schema["properties"]["limit"]
        assert limit["exclusiveMinimum"] == 0
        assert limit["exclusiveMaximum"] == 10
        assert "minimum" not in limit
        assert "maximum" not in limit
        assert tool.output_schema is not None
        assert tool.output_schema["properties"]["limit"] == {
            "type": "integer",
            "exclusiveMinimum": 0,
        }

        result = await client.call_tool("list_items", {"limit": 5})
        assert result.structured_content == {"limit": 5}
