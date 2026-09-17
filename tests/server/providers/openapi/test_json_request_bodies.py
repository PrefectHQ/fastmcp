"""JSON scalar request bodies retain their type through OpenAPI tool calls."""

import json

import httpx2
import pytest

from fastmcp import Client, FastMCP


@pytest.mark.parametrize(
    "media_type",
    [
        "application/json",
        "application/json; charset=utf-8",
        'application/json; profile="https://example.com/schema"',
        "application/vnd.example+json",
        "application/merge-patch+json; charset=utf-8",
    ],
)
@pytest.mark.parametrize(
    "schema_type,value",
    [
        ("string", "hello"),
        ("string", "true"),
        ("string", ""),
        ("string", 'café "quoted"\ntext'),
        ("integer", 42),
        ("integer", 0),
        ("number", 1.25),
        ("boolean", True),
        ("boolean", False),
    ],
)
async def test_scalar_json_body_reaches_http_endpoint(
    media_type: str, schema_type: str, value: str | int | float | bool
) -> None:
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Scalar API", "version": "1.0"},
        "paths": {
            "/value": {
                "post": {
                    "operationId": "set_value",
                    "requestBody": {
                        "required": True,
                        "content": {media_type: {"schema": {"type": schema_type}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    requests: list[httpx2.Request] = []

    def capture(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"ok": True})

    async with httpx2.AsyncClient(
        base_url="https://example.com", transport=httpx2.MockTransport(capture)
    ) as http_client:
        server = FastMCP.from_openapi(openapi_spec=spec, client=http_client)
        async with Client(server) as client:
            await client.call_tool("set_value", {"body": value})

    assert len(requests) == 1
    assert requests[0].headers["content-type"] == media_type
    decoded = json.loads(requests[0].content)
    assert decoded == value
    assert type(decoded) is type(value)
