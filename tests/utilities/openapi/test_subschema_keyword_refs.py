"""Component references under single-schema keywords become $defs references."""

from typing import Any

import httpx2
import pytest

from fastmcp import Client, FastMCP
from fastmcp.utilities.openapi.schemas import _replace_ref_with_defs

REF = {"$ref": "#/components/schemas/Reserved"}
DEFS_REF = {"$ref": "#/$defs/Reserved"}


@pytest.mark.parametrize(
    "keyword",
    [
        "not",
        "if",
        "then",
        "else",
        "contains",
        "unevaluatedItems",
        "unevaluatedProperties",
        "propertyNames",
        "contentSchema",
    ],
)
def test_single_schema_keyword_refs_are_rewritten(keyword: str):
    schema = {"type": "string", keyword: REF}
    assert _replace_ref_with_defs(schema) == {"type": "string", keyword: DEFS_REF}


def test_dependent_schemas_refs_are_rewritten():
    schema = {"type": "object", "dependentSchemas": {"credit_card": REF}}
    assert _replace_ref_with_defs(schema) == {
        "type": "object",
        "dependentSchemas": {"credit_card": DEFS_REF},
    }


@pytest.mark.parametrize("version", ["3.0.3", "3.1.0"])
async def test_not_reference_resolves_in_tool_schemas(version: str):
    reserved = {"type": "string", "enum": ["root", "admin"]}
    username: dict[str, Any] = {"type": "string", "not": REF}
    spec = {
        "openapi": version,
        "info": {"title": "Users", "version": "1"},
        "paths": {
            "/users": {
                "post": {
                    "operationId": "create_user",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"username": username},
                                    "required": ["username"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"username": username},
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
        "components": {"schemas": {"Reserved": reserved}},
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"username": "alice"})

    http = httpx2.AsyncClient(
        base_url="http://test", transport=httpx2.MockTransport(handler)
    )
    async with Client(FastMCP.from_openapi(openapi_spec=spec, client=http)) as client:
        (tool,) = await client.list_tools()
        assert tool.input_schema["properties"]["username"]["not"] == reserved
        assert tool.output_schema is not None
        assert tool.output_schema["properties"]["username"]["not"] == reserved

        result = await client.call_tool("create_user", {"username": "alice"})
        assert result.structured_content == {"username": "alice"}
