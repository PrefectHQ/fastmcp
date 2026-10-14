"""References that point into a component schema rather than at a whole one."""

from typing import Any

import httpx2
import pytest

from fastmcp import Client, FastMCP
from fastmcp.utilities.openapi.schemas import _replace_ref_with_defs

STATUS_REF = {"$ref": "#/components/schemas/Pet/properties/status"}
STATUS: dict[str, Any] = {"type": "string", "enum": ["available", "sold"]}
PET = {"type": "object", "properties": {"status": STATUS}}


def build_spec(
    components: dict[str, Any],
    body: dict[str, Any] | None = None,
    response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if body is None:
        body = {
            "type": "object",
            "properties": {"status": STATUS_REF},
            "required": ["status"],
        }
    if response is None:
        response = body
    return {
        "openapi": "3.1.0",
        "info": {"title": "API", "version": "1"},
        "paths": {
            "/pets": {
                "post": {
                    "operationId": "create_pet",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": body}},
                    },
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": response}},
                        }
                    },
                }
            }
        },
        "components": {"schemas": components},
    }


def test_replace_ref_with_defs_keeps_the_pointer_into_the_component():
    assert _replace_ref_with_defs(STATUS_REF) == {
        "$ref": "#/$defs/Pet/properties/status"
    }


async def list_only_tool(spec: dict[str, Any]) -> Any:
    async with httpx2.AsyncClient(base_url="https://api.example.com") as http:
        server = FastMCP.from_openapi(openapi_spec=spec, client=http)
        async with Client(server) as client:
            (tool,) = await client.list_tools()
    return tool


@pytest.mark.parametrize(
    "components",
    [
        pytest.param({"Pet": PET}, id="no other component"),
        pytest.param(
            {"Pet": PET, "status": {"type": "integer"}},
            id="component named like the last segment",
        ),
    ],
)
async def test_tool_schemas_use_the_referenced_property(components: dict[str, Any]):
    """The pointer used to be cut to its last segment, so it named a `status`
    definition: missing, or an unrelated component of that name."""
    tool = await list_only_tool(build_spec(components))

    assert tool.input_schema["properties"]["status"] == STATUS
    assert tool.output_schema is not None
    assert tool.output_schema["properties"]["status"] == STATUS


async def test_response_drops_a_referenced_write_only_property():
    """Responses never contain writeOnly properties, including one reached
    through a pointer into a component."""
    components = {
        "Pet": {
            "type": "object",
            "properties": {"secret": {"type": "string", "writeOnly": True}},
        }
    }
    response = {
        "type": "object",
        "properties": {
            "secret": {"$ref": "#/components/schemas/Pet/properties/secret"},
            "id": {"type": "integer"},
        },
        "required": ["secret", "id"],
    }

    tool = await list_only_tool(build_spec(components, response=response))

    assert tool.output_schema is not None
    assert tool.output_schema["properties"] == {"id": {"type": "integer"}}
    assert tool.output_schema["required"] == ["id"]


async def test_allof_body_member_pointing_into_a_component_keeps_its_fields():
    components = {
        "Pet": {
            "type": "object",
            "properties": {
                "details": {
                    "type": "object",
                    "properties": {"color": {"type": "string"}},
                    "required": ["color"],
                }
            },
        }
    }
    body = {"allOf": [{"$ref": "#/components/schemas/Pet/properties/details"}]}

    tool = await list_only_tool(build_spec(components, body=body))

    assert tool.input_schema["properties"] == {"color": {"type": "string"}}
    assert tool.input_schema["required"] == ["color"]
