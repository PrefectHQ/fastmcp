"""Request body schemas that combine a $ref with sibling properties."""

import json
from typing import Any

import httpx2

from fastmcp import Client, FastMCP


def build_spec(
    body_schema: dict[str, Any], components: dict[str, Any]
) -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "API", "version": "1"},
        "paths": {
            "/things": {
                "post": {
                    "operationId": "make_thing",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": body_schema}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        "components": {"schemas": components},
    }


async def call_make_thing(
    spec: dict[str, Any], arguments: dict[str, Any]
) -> tuple[dict[str, Any], Any]:
    seen: list[Any] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content or b"null"))
        return httpx2.Response(200, json={"ok": True})

    async with httpx2.AsyncClient(
        base_url="https://api.example.com",
        transport=httpx2.MockTransport(handler),
    ) as http:
        server = FastMCP.from_openapi(openapi_spec=spec, client=http)
        async with Client(server) as client:
            tool = (await client.list_tools())[0]
            await client.call_tool("make_thing", arguments)
    return tool.input_schema, seen[0]


BASE = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
}


async def test_ref_with_sibling_properties_advertises_both_fields():
    spec = build_spec(
        {
            "$ref": "#/components/schemas/Base",
            "properties": {"local": {"type": "string"}},
        },
        {"Base": BASE},
    )
    schema, _ = await call_make_thing(spec, {"name": "n", "local": "l"})
    assert set(schema["properties"]) == {"name", "local"}


async def test_ref_with_sibling_properties_sends_both_fields():
    spec = build_spec(
        {
            "$ref": "#/components/schemas/Base",
            "properties": {"local": {"type": "string"}},
        },
        {"Base": BASE},
    )
    _, body = await call_make_thing(spec, {"name": "n", "local": "l"})
    assert body == {"name": "n", "local": "l"}


async def test_ref_with_sibling_properties_merges_required():
    spec = build_spec(
        {
            "$ref": "#/components/schemas/Base",
            "properties": {"local": {"type": "string"}},
            "required": ["local"],
        },
        {"Base": BASE},
    )
    schema, _ = await call_make_thing(spec, {"name": "n", "local": "l"})
    assert sorted(schema["required"]) == ["local", "name"]


async def test_overlapping_property_applies_both_schemas():
    spec = build_spec(
        {
            "$ref": "#/components/schemas/Base",
            "properties": {"name": {"type": "string", "minLength": 2}},
        },
        {"Base": BASE},
    )
    schema, body = await call_make_thing(spec, {"name": "nn"})
    assert schema["properties"]["name"] == {
        "allOf": [{"type": "string"}, {"type": "string", "minLength": 2}]
    }
    assert body == {"name": "nn"}


async def test_identical_overlapping_property_stays_flat():
    spec = build_spec(
        {
            "$ref": "#/components/schemas/Base",
            "properties": {"name": {"type": "string"}},
        },
        {"Base": BASE},
    )
    schema, _ = await call_make_thing(spec, {"name": "n"})
    assert schema["properties"]["name"] == {"type": "string"}


async def test_reference_inside_sibling_property_is_resolved():
    spec = build_spec(
        {
            "$ref": "#/components/schemas/Base",
            "properties": {"child": {"$ref": "#/components/schemas/Child"}},
        },
        {
            "Base": BASE,
            "Child": {"type": "object", "properties": {"id": {"type": "integer"}}},
        },
    )
    schema, body = await call_make_thing(spec, {"name": "n", "child": {"id": 1}})
    assert schema["properties"]["child"]["properties"] == {"id": {"type": "integer"}}
    assert body == {"name": "n", "child": {"id": 1}}


async def test_ref_chain_with_sibling_properties_sends_all_fields():
    spec = build_spec(
        {
            "$ref": "#/components/schemas/Middle",
            "properties": {"local": {"type": "string"}},
        },
        {
            "Base": BASE,
            "Middle": {
                "$ref": "#/components/schemas/Base",
                "properties": {"middle": {"type": "string"}},
            },
        },
    )
    schema, body = await call_make_thing(
        spec, {"name": "n", "middle": "m", "local": "l"}
    )
    assert set(schema["properties"]) == {"name", "middle", "local"}
    assert body == {"name": "n", "middle": "m", "local": "l"}


async def test_nested_reference_in_referenced_property_is_resolved():
    spec = build_spec(
        {
            "$ref": "#/components/schemas/Base",
            "properties": {"local": {"type": "string"}},
        },
        {
            "Base": {
                "type": "object",
                "properties": {"child": {"$ref": "#/components/schemas/Child"}},
            },
            "Child": {"type": "object", "properties": {"id": {"type": "integer"}}},
        },
    )
    schema, body = await call_make_thing(spec, {"child": {"id": 1}, "local": "l"})
    assert schema["properties"]["child"]["properties"] == {"id": {"type": "integer"}}
    assert body == {"child": {"id": 1}, "local": "l"}


async def test_ref_without_siblings_exposes_referenced_fields():
    spec = build_spec({"$ref": "#/components/schemas/Base"}, {"Base": BASE})
    schema, body = await call_make_thing(spec, {"name": "n"})
    assert set(schema["properties"]) == {"name"}
    assert schema["required"] == ["name"]
    assert body == {"name": "n"}
