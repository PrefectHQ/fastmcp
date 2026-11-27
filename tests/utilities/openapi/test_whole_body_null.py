"""Explicit whole-body JSON null is distinct from an omitted request body."""

from typing import Any

import httpx2
import pytest
from jsonschema_path import SchemaPath

from fastmcp import Client, FastMCP
from fastmcp.utilities.openapi.director import RequestDirector
from fastmcp.utilities.openapi.parser import parse_openapi_to_http_routes


def _spec(
    schema: dict[str, Any],
    *,
    required: bool = True,
    media_type: str = "application/json",
    version: str = "3.1.0",
) -> dict[str, Any]:
    return {
        "openapi": version,
        "info": {"title": "Nullable body", "version": "1.0"},
        "paths": {
            "/value": {
                "post": {
                    "operationId": "set_value",
                    "requestBody": {
                        "required": required,
                        "content": {media_type: {"schema": schema}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }


@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize(
    ("arguments", "expected"),
    [({"body": None}, b"null"), ({"body": "hello"}, b'"hello"')],
)
async def test_whole_body_null_through_client(
    mode: str, required: bool, arguments: dict[str, Any], expected: bytes
) -> None:
    requests: list[httpx2.Request] = []

    def capture(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"ok": True})

    spec = _spec({"type": ["string", "null"]}, required=required)
    async with httpx2.AsyncClient(
        base_url="https://example.test", transport=httpx2.MockTransport(capture)
    ) as http_client:
        server = FastMCP.from_openapi(openapi_spec=spec, client=http_client)
        async with Client(server, mode=mode) as client:
            await client.call_tool("set_value", arguments)
            if not required:
                await client.call_tool("set_value", {})

    assert requests[0].content == expected
    assert requests[0].headers["content-type"] == "application/json"
    if not required:
        assert requests[1].content == b""
        assert "content-type" not in requests[1].headers


@pytest.mark.parametrize(
    ("version", "schema"),
    [
        ("3.0.3", {"type": "string", "nullable": True}),
        ("3.1.0", {"type": ["object", "null"]}),
        ("3.1.0", {"anyOf": [{"type": "string"}, {"type": "null"}]}),
        ("3.1.0", {"oneOf": [{"type": "string"}, {"type": "null"}]}),
    ],
)
@pytest.mark.parametrize(
    "media_type",
    [
        "application/json",
        "application/merge-patch+json",
        "application/json; charset=utf-8",
    ],
)
def test_whole_body_null_uses_declared_json_encoding(
    version: str, schema: dict[str, Any], media_type: str
) -> None:
    spec = _spec(schema, version=version, media_type=media_type)
    route = parse_openapi_to_http_routes(spec)[0]
    request = RequestDirector(SchemaPath.from_dict(spec)).build(route, {"body": None})
    assert request.content == b"null"
    assert request.headers["content-type"] == media_type


@pytest.mark.parametrize(
    ("schema", "value", "expected"),
    [
        ({"type": "boolean"}, False, b"false"),
        ({"type": "integer"}, 0, b"0"),
        ({"type": "string"}, "", b'""'),
        ({"type": "object"}, {}, b"{}"),
        ({"type": "array", "items": {"type": "string"}}, [], b"[]"),
        ({"type": "string"}, None, b""),
    ],
)
def test_other_whole_body_values(
    schema: dict[str, Any], value: Any, expected: bytes
) -> None:
    spec = _spec(schema)
    route = parse_openapi_to_http_routes(spec)[0]
    request = RequestDirector(SchemaPath.from_dict(spec)).build(route, {"body": value})
    assert request.content == expected
