"""Query array serialization follows the declared OpenAPI style."""

from typing import Any

import httpx2
import pytest
from jsonschema_path import SchemaPath

from fastmcp import Client, FastMCP
from fastmcp.utilities.openapi.director import RequestDirector
from fastmcp.utilities.openapi.models import HTTPRoute, ParameterInfo


@pytest.mark.parametrize(
    "style,explode,expected",
    [
        (None, None, [("ids", "a"), ("ids", "b")]),
        ("form", None, [("ids", "a"), ("ids", "b")]),
        ("pipeDelimited", None, [("ids", "a|b")]),
        ("spaceDelimited", None, [("ids", "a b")]),
        ("form", False, [("ids", "a,b")]),
        ("pipeDelimited", False, [("ids", "a|b")]),
        ("spaceDelimited", False, [("ids", "a b")]),
        ("form", True, [("ids", "a"), ("ids", "b")]),
        ("pipeDelimited", True, [("ids", "a"), ("ids", "b")]),
        ("spaceDelimited", True, [("ids", "a"), ("ids", "b")]),
    ],
)
async def test_query_array_serialization(
    style: str | None,
    explode: bool | None,
    expected: list[tuple[str, str]],
) -> None:
    parameter: dict[str, Any] = {
        "name": "ids",
        "in": "query",
        "required": True,
        "schema": {"type": "array", "items": {"type": "string"}},
    }
    if style is not None:
        parameter["style"] = style
    if explode is not None:
        parameter["explode"] = explode
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Query arrays", "version": "1"},
        "paths": {
            "/items": {
                "get": {
                    "operationId": "list_items",
                    "parameters": [parameter],
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
        base_url="https://example.test", transport=httpx2.MockTransport(capture)
    ) as http_client:
        server = FastMCP.from_openapi(openapi_spec=spec, client=http_client)
        async with Client(server) as client:
            await client.call_tool("list_items", {"ids": ["a", "b"]})

    assert len(requests) == 1
    assert requests[0].url.params.multi_items() == expected


@pytest.mark.parametrize(
    "style,expected",
    [
        ("form", [("name", "alice")]),
        ("deepObject", [("filter[name]", "alice")]),
        ("pipeDelimited", [("name", "alice")]),
        ("spaceDelimited", [("name", "alice")]),
    ],
)
def test_object_query_defaults_preserve_existing_behavior(
    style: str, expected: list[tuple[str, str]]
) -> None:
    route = HTTPRoute(
        path="/items",
        method="GET",
        parameters=[
            ParameterInfo(
                name="filter",
                location="query",
                required=True,
                schema={"type": "object", "properties": {"name": {"type": "string"}}},
                style=style,
            )
        ],
        parameter_map={"filter": {"location": "query", "openapi_name": "filter"}},
    )
    request = RequestDirector(SchemaPath.from_dict({})).build(
        route, {"filter": {"name": "alice"}}, "https://example.test"
    )
    assert request.url.params.multi_items() == expected
