"""A media-type schema's own ``$defs`` must survive beside component schemas.

OpenAPI 3.1 schema objects are JSON Schema 2020-12 resources, so a request or
response media-type schema may declare its own ``$defs`` and reference them
with ``#/$defs/...``. When ``components/schemas`` is non-empty, those inline
definitions used to be replaced by the component-derived ones, leaving the
media-type schema's ``#/$defs/...`` references dangling.
"""

import httpx2
from jsonschema import validate as validate_json_schema

from fastmcp import Client, FastMCP
from fastmcp.utilities.openapi.models import (
    HTTPRoute,
    RequestBodyInfo,
    ResponseInfo,
)
from fastmcp.utilities.openapi.parser import parse_openapi_to_http_routes
from fastmcp.utilities.openapi.schemas import (
    _combine_schemas_and_map_params,
    extract_output_schema_from_responses,
)

ITEM_SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}}}
BODY = {"item": {"name": "n"}, "extra": "hello"}


def _inline_defs_schema() -> dict:
    """A media-type schema that declares its own ``$defs``."""
    return {
        "type": "object",
        "properties": {
            "item": {"$ref": "#/components/schemas/Item"},
            "extra": {"$ref": "#/$defs/Extra"},
        },
        "$defs": {"Extra": {"type": "string"}},
    }


SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "t", "version": "1.0.0"},
    "components": {"schemas": {"Item": ITEM_SCHEMA}},
    "paths": {
        "/items": {
            "post": {
                "operationId": "createItem",
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": _inline_defs_schema()}},
                },
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {
                            "application/json": {"schema": _inline_defs_schema()}
                        },
                    }
                },
            }
        }
    },
}


def test_output_schema_keeps_media_type_defs():
    responses = {
        "200": ResponseInfo(
            description="ok",
            content_schema={"application/json": _inline_defs_schema()},
        )
    }

    result = extract_output_schema_from_responses(
        responses, {"Item": ITEM_SCHEMA}, "3.1.0"
    )

    assert result is not None
    assert result["properties"]["extra"] == {"$ref": "#/$defs/Extra"}
    assert result["$defs"]["Extra"] == {"type": "string"}
    assert "Item" in result["$defs"]

    # The advertised schema must resolve every reference it carries.
    validate_json_schema(BODY, result)


def test_input_schema_keeps_media_type_defs():
    route = HTTPRoute(
        path="/items",
        method="POST",
        operation_id="createItem",
        request_body=RequestBodyInfo(
            required=True,
            content_schema={"application/json": _inline_defs_schema()},
        ),
        request_schemas={"Item": ITEM_SCHEMA},
    )

    combined, _ = _combine_schemas_and_map_params(route)

    assert combined["properties"]["extra"] == {"$ref": "#/$defs/Extra"}
    assert combined["$defs"]["Extra"] == {"type": "string"}
    assert "Item" in combined["$defs"]

    validate_json_schema(BODY, combined)


def test_output_schema_rewrites_refs_inside_media_type_defs():
    schema = {
        "type": "object",
        "properties": {"extra": {"$ref": "#/$defs/Extra"}},
        "$defs": {
            "Extra": {
                "type": "object",
                "properties": {"inner": {"$ref": "#/components/schemas/Item"}},
            }
        },
    }
    responses = {
        "200": ResponseInfo(
            description="ok",
            content_schema={"application/json": schema},
        )
    }

    result = extract_output_schema_from_responses(
        responses, {"Item": ITEM_SCHEMA}, "3.1.0"
    )

    assert result is not None
    assert result["$defs"]["Extra"]["properties"]["inner"] == {"$ref": "#/$defs/Item"}
    assert "Item" in result["$defs"]

    validate_json_schema({"extra": {"inner": {"name": "n"}}}, result)


def test_output_schema_keeps_media_type_defs_after_wrap_without_components():
    schema = {
        "type": "array",
        "items": {"$ref": "#/$defs/Extra"},
        "$defs": {"Extra": {"type": "string"}},
    }
    responses = {
        "200": ResponseInfo(
            description="ok",
            content_schema={"application/json": schema},
        )
    }

    result = extract_output_schema_from_responses(responses, {}, "3.1.0")

    assert result is not None
    assert result["x-fastmcp-wrap-result"] is True
    assert result["properties"]["result"]["items"] == {"$ref": "#/$defs/Extra"}
    assert result["$defs"]["Extra"] == {"type": "string"}

    # The advertised schema must resolve every reference it carries.
    validate_json_schema({"result": ["hello"]}, result)


def test_parsed_route_flat_schema_keeps_media_type_defs():
    route = parse_openapi_to_http_routes(SPEC)[0]

    flat = route.flat_param_schema

    assert flat["properties"]["extra"] == {"$ref": "#/$defs/Extra"}
    assert flat["$defs"]["Extra"] == {"type": "string"}
    assert "Item" in flat["$defs"]


def test_parsed_route_input_rewrites_refs_inside_media_type_defs():
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "t", "version": "1.0.0"},
        "components": {"schemas": {"Item": ITEM_SCHEMA}},
        "paths": {
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "extra": {"$ref": "#/$defs/Extra"},
                                    },
                                    "$defs": {
                                        "Extra": {
                                            "type": "object",
                                            "properties": {
                                                "inner": {
                                                    "$ref": "#/components/schemas/Item"
                                                }
                                            },
                                        }
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }

    route = parse_openapi_to_http_routes(spec)[0]

    flat = route.flat_param_schema

    assert flat["properties"]["extra"] == {"$ref": "#/$defs/Extra"}
    assert flat["$defs"]["Extra"]["properties"]["inner"] == {"$ref": "#/$defs/Item"}
    assert "Item" in flat["$defs"]

    validate_json_schema({"extra": {"inner": {"name": "n"}}}, flat)


async def test_tool_call_with_media_type_defs():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=BODY)

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    mcp = FastMCP.from_openapi(SPEC, client=http)

    async with Client(mcp) as client:
        result = await client.call_tool("createItem", BODY)

    assert result.structured_content == BODY
