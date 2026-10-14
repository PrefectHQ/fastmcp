"""References that point into a component schema rather than at a whole one."""

from typing import Any

import httpx2
import pytest

from fastmcp import Client, FastMCP
from fastmcp.utilities.openapi.schemas import _replace_ref_with_defs

STATUS_REF = {"$ref": "#/components/schemas/Pet/properties/status"}
STATUS: dict[str, Any] = {"type": "string", "enum": ["available", "sold"]}
PET = {"type": "object", "properties": {"status": STATUS}}


def build_spec(components: dict[str, Any]) -> dict[str, Any]:
    body = {
        "type": "object",
        "properties": {"status": STATUS_REF},
        "required": ["status"],
    }
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
                            "content": {"application/json": {"schema": body}},
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
    async with httpx2.AsyncClient(base_url="https://api.example.com") as http:
        server = FastMCP.from_openapi(openapi_spec=build_spec(components), client=http)
        async with Client(server) as client:
            (tool,) = await client.list_tools()

    assert tool.input_schema["properties"]["status"] == STATUS
    assert tool.output_schema is not None
    assert tool.output_schema["properties"]["status"] == STATUS
