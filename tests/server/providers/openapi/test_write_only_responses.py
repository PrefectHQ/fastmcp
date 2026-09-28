"""writeOnly properties are request-only, so tool output schemas omit them."""

import json
from typing import Any

import httpx2
import pytest

from fastmcp import Client, FastMCP
from fastmcp.utilities.openapi.json_schema_converter import (
    convert_openapi_schema_to_json_schema,
)


def user_spec(openapi_version: str) -> dict[str, Any]:
    user_ref = {"$ref": "#/components/schemas/User"}
    return {
        "openapi": openapi_version,
        "info": {"title": "Users", "version": "1.0"},
        "paths": {
            "/users": {
                "post": {
                    "operationId": "create_user",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": user_ref}},
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {"application/json": {"schema": user_ref}},
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "User": {
                    "type": "object",
                    "required": ["id", "name", "password", "address"],
                    "properties": {
                        "id": {"type": "integer", "readOnly": True},
                        "name": {"type": "string", "writeOnly": False},
                        "password": {"type": "string", "writeOnly": True},
                        "address": {"$ref": "#/components/schemas/Address"},
                    },
                },
                "Address": {
                    "type": "object",
                    "required": ["street", "token"],
                    "properties": {
                        "street": {"type": "string"},
                        "token": {"type": "string", "writeOnly": True},
                    },
                },
            }
        },
    }


@pytest.mark.parametrize("openapi_version", ["3.0.3", "3.1.0"])
async def test_output_schema_omits_write_only_properties(openapi_version: str):
    server = FastMCP.from_openapi(
        user_spec(openapi_version), client=httpx2.AsyncClient(base_url="http://test")
    )
    async with Client(server) as client:
        tool = (await client.list_tools())[0]

    output = tool.output_schema
    assert output is not None
    assert list(output["properties"]) == ["id", "name", "address"]
    assert output["required"] == ["id", "name", "address"]
    address = output["properties"]["address"]
    assert list(address["properties"]) == ["street"]
    assert address["required"] == ["street"]

    # Requests still carry writeOnly properties.
    assert "password" in tool.input_schema["required"]
    assert "token" in tool.input_schema["properties"]["address"]["required"]


async def test_response_without_write_only_properties_is_accepted():
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            201, json={"id": 7, "name": "ada", "address": {"street": "1 Main"}}
        )

    api = httpx2.AsyncClient(
        base_url="http://test", transport=httpx2.MockTransport(handler)
    )
    server = FastMCP.from_openapi(user_spec("3.0.3"), client=api)
    async with Client(server) as client:
        result = await client.call_tool(
            "create_user",
            {
                "id": 7,
                "name": "ada",
                "password": "s3cret",
                "address": {"street": "1 Main", "token": "t0k"},
            },
        )

    assert json.loads(requests[0].content) == {
        "id": 7,
        "name": "ada",
        "password": "s3cret",
        "address": {"street": "1 Main", "token": "t0k"},
    }
    assert result.is_error is False
    assert result.structured_content == {
        "id": 7,
        "name": "ada",
        "address": {"street": "1 Main"},
    }


def test_required_is_cleared_when_every_property_is_write_only():
    schema = {
        "type": "object",
        "required": ["password"],
        "properties": {"password": {"type": "string", "writeOnly": True}},
    }

    result = convert_openapi_schema_to_json_schema(
        schema, "3.0.3", remove_write_only=True
    )

    assert result == {"type": "object", "properties": {}}
