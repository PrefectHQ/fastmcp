"""readOnly properties are response-only, so tool input schemas omit them."""

import json
from typing import Any

import httpx2
import pytest

from fastmcp import Client, FastMCP


def pet_spec(openapi_version: str) -> dict[str, Any]:
    pet_ref = {"$ref": "#/components/schemas/Pet"}
    return {
        "openapi": openapi_version,
        "info": {"title": "Pets", "version": "1.0"},
        "paths": {
            "/owners/{owner_id}/pets": {
                "post": {
                    "operationId": "create_pet",
                    "parameters": [
                        {
                            "name": "owner_id",
                            "in": "path",
                            "required": True,
                            "schema": {"$ref": "#/components/schemas/Id"},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": pet_ref}},
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {"application/json": {"schema": pet_ref}},
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "Id": {"type": "integer", "readOnly": True},
                "Pet": {
                    "type": "object",
                    "required": ["id", "name", "tag"],
                    "properties": {
                        "id": {"$ref": "#/components/schemas/Id"},
                        "name": {"type": "string", "readOnly": False},
                        "created_at": {"type": "string", "readOnly": True},
                        "tag": {"$ref": "#/components/schemas/Tag"},
                    },
                },
                "Tag": {
                    "type": "object",
                    "required": ["id", "label"],
                    "properties": {
                        "id": {"type": "integer", "readOnly": True},
                        "label": {"type": "string"},
                    },
                },
            }
        },
    }


@pytest.mark.parametrize("openapi_version", ["3.0.3", "3.1.0"])
async def test_input_schema_omits_read_only_properties(openapi_version: str):
    server = FastMCP.from_openapi(
        pet_spec(openapi_version), client=httpx2.AsyncClient(base_url="http://test")
    )
    async with Client(server) as client:
        tool = (await client.list_tools())[0]

    inputs = tool.input_schema
    assert list(inputs["properties"]) == ["owner_id", "name", "tag"]
    assert inputs["required"] == ["owner_id", "name", "tag"]
    tag = inputs["properties"]["tag"]
    assert list(tag["properties"]) == ["label"]
    assert tag["required"] == ["label"]
    assert "readOnly" not in json.dumps(inputs)

    # Responses still carry readOnly properties.
    output = tool.output_schema
    assert output is not None
    assert output["required"] == ["id", "name", "tag"]
    assert "created_at" in output["properties"]
    assert output["properties"]["tag"]["required"] == ["id", "label"]


async def test_request_without_read_only_properties_is_sent():
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            201,
            json={
                "id": 3,
                "name": "Rex",
                "created_at": "2026-10-15",
                "tag": {"id": 9, "label": "dog"},
            },
        )

    api = httpx2.AsyncClient(
        base_url="http://test", transport=httpx2.MockTransport(handler)
    )
    server = FastMCP.from_openapi(pet_spec("3.0.3"), client=api)
    async with Client(server) as client:
        result = await client.call_tool(
            "create_pet", {"owner_id": 1, "name": "Rex", "tag": {"label": "dog"}}
        )

    assert requests[0].url.path == "/owners/1/pets"
    assert json.loads(requests[0].content) == {"name": "Rex", "tag": {"label": "dog"}}
    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["id"] == 3


async def test_whole_body_argument_omits_read_only_properties():
    spec = pet_spec("3.1.0")
    # A bulk create: an array body is passed as one argument.
    spec["paths"]["/owners/{owner_id}/pets"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"] = {"type": "array", "items": {"$ref": "#/components/schemas/Pet"}}
    server = FastMCP.from_openapi(
        spec, client=httpx2.AsyncClient(base_url="http://test")
    )
    async with Client(server) as client:
        tool = (await client.list_tools())[0]

    inputs = tool.input_schema
    assert list(inputs["properties"]) == ["owner_id", "body"]
    pet = inputs["properties"]["body"]["items"]
    assert list(pet["properties"]) == ["name", "tag"]
    assert pet["required"] == ["name", "tag"]
    assert list(pet["properties"]["tag"]["properties"]) == ["label"]


async def test_body_typed_by_read_only_component_stays_an_argument():
    # readOnly applies to properties; a body that reuses a readOnly component
    # as its root schema is still the request payload.
    spec = pet_spec("3.0.3")
    spec["paths"]["/owners/{owner_id}/pets"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"] = {"$ref": "#/components/schemas/Id"}
    server = FastMCP.from_openapi(
        spec, client=httpx2.AsyncClient(base_url="http://test")
    )
    async with Client(server) as client:
        tool = (await client.list_tools())[0]

    inputs = tool.input_schema
    assert list(inputs["properties"]) == ["owner_id", "body"]
    assert inputs["properties"]["body"] == {"type": "integer"}
    assert inputs["required"] == ["owner_id", "body"]


async def test_object_parameter_omits_nested_read_only_properties():
    spec = pet_spec("3.0.3")
    filter_schema = {
        "type": "object",
        "required": ["id", "q"],
        "properties": {
            "id": {"type": "integer", "readOnly": True},
            "q": {"type": "string"},
        },
    }
    spec["components"]["schemas"]["Filter"] = filter_schema
    # The same object inline and as a component $ref.
    for name, schema in [
        ("filter", filter_schema),
        ("ref_filter", {"$ref": "#/components/schemas/Filter"}),
    ]:
        spec["paths"]["/owners/{owner_id}/pets"]["post"]["parameters"].append(
            {
                "name": name,
                "in": "query",
                "style": "deepObject",
                "explode": True,
                "schema": schema,
            }
        )
    server = FastMCP.from_openapi(
        spec, client=httpx2.AsyncClient(base_url="http://test")
    )
    async with Client(server) as client:
        tool = (await client.list_tools())[0]

    inputs = tool.input_schema
    query_filter = inputs["properties"]["filter"]
    assert list(query_filter["properties"]) == ["q"]
    assert query_filter["required"] == ["q"]
    assert inputs["properties"]["ref_filter"] == query_filter
