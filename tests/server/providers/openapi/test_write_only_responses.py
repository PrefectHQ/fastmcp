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


PASSWORD = {"type": "string", "writeOnly": True}
ID = {"type": "integer"}


def spec_for(schemas: dict[str, Any], openapi_version: str = "3.0.3") -> dict[str, Any]:
    """A POST /things whose request and response both use the `Thing` schema."""
    thing = {"$ref": "#/components/schemas/Thing"}
    return {
        "openapi": openapi_version,
        "info": {"title": "Things", "version": "1.0"},
        "paths": {
            "/things": {
                "post": {
                    "operationId": "create_thing",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": thing}},
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {"application/json": {"schema": thing}},
                        }
                    },
                }
            }
        },
        "components": {"schemas": schemas},
    }


class Harness:
    """A tool generated from a spec, backed by a mock API that returns `reply`."""

    def __init__(self, schemas: dict[str, Any], reply: dict[str, Any]) -> None:
        self.requests: list[httpx2.Request] = []
        self.reply = reply
        self.server = FastMCP.from_openapi(
            spec_for(schemas),
            client=httpx2.AsyncClient(
                base_url="http://test", transport=httpx2.MockTransport(self.handler)
            ),
        )

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(201, json=self.reply)

    async def schemas(self) -> tuple[dict[str, Any], dict[str, Any]]:
        async with Client(self.server) as client:
            tool = (await client.list_tools())[0]
        assert tool.output_schema is not None
        return tool.input_schema, tool.output_schema

    async def call(self, arguments: dict[str, Any]) -> Any:
        async with Client(self.server) as client:
            return await client.call_tool("create_thing", arguments)


async def check_public_response_is_valid(
    schemas: dict[str, Any], arguments: dict[str, Any], public_response: dict[str, Any]
) -> None:
    """The public response validates, an empty one is rejected, and the request
    still carries and requires `password`."""
    harness = Harness(schemas, public_response)
    input_schema, output_schema = await harness.schemas()
    assert "password" in input_schema["required"]
    assert "password" in input_schema["properties"]
    assert "password" not in json.dumps(output_schema)

    result = await harness.call(arguments)
    assert result.is_error is False
    assert json.loads(harness.requests[0].content)["password"] == "s3cret"

    harness.reply = {}
    with pytest.raises(RuntimeError, match="required property"):
        await harness.call(arguments)


async def test_required_names_defined_by_all_of_stay_required():
    schemas = {
        "Base": {"type": "object", "properties": {"id": ID}},
        "Thing": {
            "type": "object",
            "allOf": [{"$ref": "#/components/schemas/Base"}],
            "required": ["id", "password"],
            "properties": {"password": PASSWORD},
        },
    }
    _, output = await Harness(schemas, {"id": 1}).schemas()
    assert output["required"] == ["id"]
    assert output["properties"] == {}
    await check_public_response_is_valid(
        schemas, {"id": 1, "password": "s3cret"}, {"id": 1}
    )


async def test_required_names_covered_by_additional_properties_stay_required():
    schemas = {
        "Thing": {
            "type": "object",
            "required": ["id", "password"],
            "properties": {"password": PASSWORD},
            "additionalProperties": ID,
        }
    }
    _, output = await Harness(schemas, {"id": 1}).schemas()
    assert output["required"] == ["id"]
    await check_public_response_is_valid(
        schemas, {"id": 1, "password": "s3cret"}, {"id": 1}
    )


@pytest.mark.parametrize("via_ref", [False, True], ids=["inline", "ref"])
async def test_write_only_property_in_all_of_member_is_not_required(via_ref: bool):
    credentials = {"type": "object", "properties": {"password": PASSWORD}}
    schemas: dict[str, Any] = {
        "Thing": {
            "type": "object",
            "required": ["id", "password"],
            "properties": {"id": ID},
            "allOf": [
                {"$ref": "#/components/schemas/Credentials"} if via_ref else credentials
            ],
        }
    }
    if via_ref:
        schemas["Credentials"] = credentials
    _, output = await Harness(schemas, {"id": 1}).schemas()
    assert output["required"] == ["id"]
    assert list(output["properties"]) == ["id"]
    await check_public_response_is_valid(
        schemas, {"id": 1, "password": "s3cret"}, {"id": 1}
    )


async def test_required_in_sibling_all_of_member_loses_write_only_name():
    schemas = {
        "Credentials": {"type": "object", "properties": {"password": PASSWORD}},
        "Thing": {
            "type": "object",
            "properties": {"id": ID},
            "allOf": [
                {"$ref": "#/components/schemas/Credentials"},
                {"required": ["id", "password"]},
            ],
        },
    }
    _, output = await Harness(schemas, {"id": 1}).schemas()
    assert output["allOf"][1] == {"required": ["id"]}
    await check_public_response_is_valid(
        schemas, {"id": 1, "password": "s3cret"}, {"id": 1}
    )


@pytest.mark.parametrize(
    "password",
    [
        {"$ref": "#/components/schemas/Secret"},
        {"$ref": "#/components/schemas/SecretAlias"},
        {"allOf": [{"$ref": "#/components/schemas/Secret"}]},
    ],
    ids=["ref", "ref-chain", "all-of-ref"],
)
async def test_property_referencing_write_only_schema_is_excluded(
    password: dict[str, Any],
):
    schemas = {
        "Secret": PASSWORD,
        "SecretAlias": {"$ref": "#/components/schemas/Secret"},
        "Thing": {
            "type": "object",
            "required": ["id", "password"],
            "properties": {"id": ID, "password": password},
        },
    }
    _, output = await Harness(schemas, {"id": 1}).schemas()
    assert output["required"] == ["id"]
    assert list(output["properties"]) == ["id"]
    await check_public_response_is_valid(
        schemas, {"id": 1, "password": "s3cret"}, {"id": 1}
    )


def test_cyclic_references_do_not_hang_projection():
    schema = {
        "type": "object",
        "required": ["a", "b"],
        "properties": {"a": {"$ref": "#/$defs/A"}, "b": {"$ref": "#/$defs/B"}},
        "$defs": {"A": {"$ref": "#/$defs/B"}, "B": {"$ref": "#/$defs/A"}},
    }
    result = convert_openapi_schema_to_json_schema(
        schema, "3.0.3", remove_write_only=True
    )
    assert result["required"] == ["a", "b"]


async def test_nested_object_and_array_item_scopes_stay_separate():
    token = {"type": "string"}
    private = {
        "type": "object",
        "required": ["token"],
        "properties": {"token": {**token, "writeOnly": True}},
    }
    public = {"type": "object", "required": ["token"], "properties": {"token": token}}
    schemas = {
        "Thing": {
            "type": "object",
            "required": ["token", "credentials", "audit", "sessions"],
            "properties": {
                "token": token,
                "credentials": private,
                "audit": public,
                "sessions": {"type": "array", "items": private},
            },
        }
    }
    reply: dict[str, Any] = {
        "token": "t",
        "credentials": {},
        "audit": {"token": "a"},
        "sessions": [{}],
    }
    harness = Harness(schemas, reply)
    input_schema, output = await harness.schemas()

    assert output["required"] == ["token", "credentials", "audit", "sessions"]
    scope = {"type": "object", "properties": {}}
    assert output["properties"]["credentials"] == scope
    assert output["properties"]["sessions"]["items"] == scope
    assert output["properties"]["audit"] == public

    # Requests keep every writeOnly field
    assert input_schema["properties"]["credentials"]["required"] == ["token"]
    assert input_schema["properties"]["sessions"]["items"]["required"] == ["token"]

    arguments = {
        "token": "t",
        "credentials": {"token": "c"},
        "audit": {"token": "a"},
        "sessions": [{"token": "s"}],
    }
    assert (await harness.call(arguments)).is_error is False

    # The public token of the parent and of the sibling is still required
    for invalid in (
        {k: v for k, v in reply.items() if k != "token"},
        {**reply, "audit": {}},
    ):
        harness.reply = invalid
        with pytest.raises(RuntimeError, match="'token' is a required property"):
            await harness.call(arguments)


def test_nullable_object_with_all_of_is_projected_before_conversion():
    schema = {
        "nullable": True,
        "allOf": [{"$ref": "#/$defs/Base"}],
        "required": ["id", "password"],
        "properties": {"password": PASSWORD},
        "$defs": {"Base": {"type": "object", "properties": {"id": ID}}},
    }
    result = convert_openapi_schema_to_json_schema(
        schema, "3.0.3", remove_write_only=True
    )
    assert result["required"] == ["id"]
    assert result["properties"] == {}
    assert {"type": "null"} in result["anyOf"]


@pytest.mark.parametrize("openapi_version", ["3.0.3", "3.1.0"])
async def test_schema_without_write_only_is_unchanged(openapi_version: str):
    schemas = {
        "Base": {"type": "object", "properties": {"id": {**ID, "readOnly": True}}},
        "Thing": {
            "type": "object",
            "required": ["id", "name"],
            "allOf": [{"$ref": "#/components/schemas/Base"}],
            "properties": {
                "name": {"type": "string", "nullable": True},
                "kind": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": {"type": "integer"},
        },
    }
    server = FastMCP.from_openapi(
        spec_for(schemas, openapi_version),
        client=httpx2.AsyncClient(base_url="http://test"),
    )
    async with Client(server) as client:
        tool = (await client.list_tools())[0]
    name_type: Any = ["string", "null"] if openapi_version == "3.0.3" else "string"
    assert tool.output_schema == {
        "type": "object",
        "required": ["id", "name"],
        "allOf": [{"type": "object", "properties": {"id": {"type": "integer"}}}],
        "properties": {
            "name": {"type": name_type},
            "kind": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": {"type": "integer"},
        "x-fastmcp-top-level-schema": "Thing",
    }
