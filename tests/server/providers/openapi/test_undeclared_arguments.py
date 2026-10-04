"""OpenAPI components ignore undeclared arguments and keep configured headers."""

import json
from typing import Any

import httpx2
import pytest
from jsonschema_path import SchemaPath

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.providers.openapi.routing import MCPType, RouteMap
from fastmcp.utilities.openapi import director, parser
from fastmcp.utilities.openapi.director import RequestDirector
from fastmcp.utilities.openapi.models import (
    HTTPRoute,
    ParameterInfo,
    RequestBodyInfo,
)
from fastmcp.utilities.tests import run_server_async

BASE_URL = "https://api.example.com"
CONFIGURED_HEADERS = {"X-Api-Key": "configured-key"}
CONFIGURED_COOKIES = {"session": "configured-session"}

UNDECLARED_ARGUMENTS = [
    pytest.param({"note__header": "hello"}, id="header"),
    pytest.param({"X-Api-Key__header": "other-key"}, id="configured-header"),
    pytest.param({"theme__cookie": "dark"}, id="cookie"),
    pytest.param({"extra__query": "true"}, id="query"),
    pytest.param({"debug": True}, id="body"),
]


def _spec(path: str, method: str, operation: dict[str, Any]) -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Test API", "version": "1.0"},
        "paths": {
            path: {
                method: {
                    "operationId": "operation",
                    "responses": {"200": {"description": "OK"}},
                    **operation,
                }
            }
        },
    }


def _upstream_client(requests: list[httpx2.Request]) -> httpx2.AsyncClient:
    def capture(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"ok": True})

    return httpx2.AsyncClient(
        base_url=BASE_URL,
        headers=CONFIGURED_HEADERS,
        cookies=CONFIGURED_COOKIES,
        transport=httpx2.MockTransport(capture),
    )


async def _call_tool(
    spec: dict[str, Any], arguments: dict[str, Any]
) -> list[httpx2.Request]:
    requests: list[httpx2.Request] = []
    async with _upstream_client(requests) as http_client:
        server = FastMCP.from_openapi(openapi_spec=spec, client=http_client)
        async with Client(server) as client:
            await client.call_tool("operation", arguments)
    return requests


def _assert_sent_as_configured(request: httpx2.Request) -> None:
    assert request.headers["X-Api-Key"] == "configured-key"
    assert request.headers["Cookie"] == "session=configured-session"
    assert "note" not in request.headers


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("arguments", UNDECLARED_ARGUMENTS)
async def test_undeclared_arguments_are_ignored(
    method: str, arguments: dict[str, Any]
) -> None:
    spec = _spec("/health", method, {})

    [request] = await _call_tool(spec, arguments)

    _assert_sent_as_configured(request)
    assert str(request.url) == f"{BASE_URL}/health"
    assert request.content == b""


async def test_undeclared_arguments_are_ignored_after_schema_precalculation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("schema pre-calculation failed")

    monkeypatch.setattr(parser, "_combine_schemas_and_map_params", fail)
    limit = {"name": "limit", "in": "query", "schema": {"type": "integer"}}
    spec = _spec("/items", "get", {"parameters": [limit]})

    [request] = await _call_tool(
        spec,
        {"limit": 5, "note__header": "hello", "debug": True},
    )

    _assert_sent_as_configured(request)
    assert dict(request.url.params) == {"limit": "5"}
    assert request.content == b""


async def test_tool_call_fails_when_parameter_map_cannot_be_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("schema pre-calculation failed")

    monkeypatch.setattr(parser, "_combine_schemas_and_map_params", fail)
    monkeypatch.setattr(director, "_combine_schemas_and_map_params", fail)
    limit = {"name": "limit", "in": "query", "schema": {"type": "integer"}}
    spec = _spec("/items", "get", {"parameters": [limit]})

    requests: list[httpx2.Request] = []
    async with _upstream_client(requests) as http_client:
        server = FastMCP.from_openapi(openapi_spec=spec, client=http_client)
        async with Client(server) as client:
            with pytest.raises(ToolError, match="schema pre-calculation failed"):
                await client.call_tool("operation", {"limit": 5})

    assert requests == []


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param({"type": "object"}, id="default-additional-properties"),
        pytest.param(
            {"type": "object", "additionalProperties": True},
            id="additional-properties",
        ),
    ],
)
async def test_whole_body_is_sent_unchanged(
    schema: dict[str, Any],
) -> None:
    body_schema = {"$ref": "#/components/schemas/Thing"}
    request_body = {
        "required": True,
        "content": {"application/json": {"schema": body_schema}},
    }
    spec = _spec("/things", "post", {"requestBody": request_body})
    spec["components"] = {"schemas": {"Thing": schema}}
    body = {"note__header": "body data", "debug": True}

    [request] = await _call_tool(spec, {"body": body, "note__header": "hello"})

    _assert_sent_as_configured(request)
    assert json.loads(request.content) == body


@pytest.mark.parametrize(
    "component",
    ["tool", "resource", "resource_template"],
)
async def test_configured_headers_keep_their_values(
    component: str,
) -> None:
    ok = {"200": {"description": "OK"}}
    item_id = {"name": "item_id", "in": "path", "required": True, "schema": {}}
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Test API", "version": "1.0"},
        "paths": {
            "/actions": {"post": {"operationId": "act", "responses": ok}},
            "/status": {"get": {"operationId": "status", "responses": ok}},
            "/items/{item_id}": {
                "get": {"operationId": "item", "parameters": [item_id], "responses": ok}
            },
        },
    }
    route_maps = [
        RouteMap(pattern=r"\{", mcp_type=MCPType.RESOURCE_TEMPLATE),
        RouteMap(methods=["GET"], mcp_type=MCPType.RESOURCE),
    ]
    requests: list[httpx2.Request] = []

    async with _upstream_client(requests) as http_client:
        server = FastMCP.from_openapi(
            openapi_spec=spec, client=http_client, route_maps=route_maps
        )
        async with run_server_async(server) as url:
            transport = StreamableHttpTransport(
                url, headers={"X-Api-Key": "client-key", "X-Client-Tag": "forwarded"}
            )
            async with Client(transport) as client:
                if component == "tool":
                    await client.call_tool("act", {})
                elif component == "resource":
                    await client.read_resource("resource://status")
                else:
                    await client.read_resource("resource://item/7")

    [request] = requests
    _assert_sent_as_configured(request)
    assert request.headers["X-Client-Tag"] == "forwarded"


def test_manual_route_uses_canonical_parameter_names() -> None:
    route = HTTPRoute(
        path="/things/{id}",
        method="GET",
        parameters=[
            ParameterInfo(
                name="id", location="path", required=True, schema={"type": "string"}
            ),
            ParameterInfo(name="id", location="query", schema={"type": "string"}),
            ParameterInfo(name="id", location="header", schema={"type": "string"}),
        ],
    )
    request_director = RequestDirector(SchemaPath.from_dict({}))

    request = request_director.build(
        route,
        {
            "id__path": "path-value",
            "id__query": "query-value",
            "id__header": "header-value",
            "note__header": "hello",
            "debug": True,
        },
        BASE_URL,
    )

    assert request.url.path == "/things/path-value"
    assert dict(request.url.params) == {"id": "query-value"}
    assert request.headers["id"] == "header-value"
    assert "note" not in request.headers
    assert request.content == b""


def test_manual_route_accepts_location_suffix_for_declared_parameter() -> None:
    route = HTTPRoute(
        path="/users/{id}",
        method="GET",
        parameters=[
            ParameterInfo(
                name="id", location="path", required=True, schema={"type": "integer"}
            )
        ],
    )
    request_director = RequestDirector(SchemaPath.from_dict({}))

    request = request_director.build(
        route,
        {
            "id__path": 123,
            "id__header": "header-value",
            "id__query": "query-value",
        },
        BASE_URL,
    )

    assert str(request.url) == f"{BASE_URL}/users/123"
    assert "id" not in request.headers
    assert request.content == b""


def test_manual_route_with_allof_body_sends_declared_properties() -> None:
    content_schema = {
        "application/json": {"allOf": [{"$ref": "#/components/schemas/Thing"}]}
    }
    route = HTTPRoute(
        path="/things",
        method="POST",
        request_body=RequestBodyInfo(content_schema=content_schema),
        request_schemas={
            "Thing": {"type": "object", "properties": {"name": {"type": "string"}}}
        },
    )
    request_director = RequestDirector(SchemaPath.from_dict({}))

    request = request_director.build(
        route, {"name": "thing", "note__header": "hello"}, BASE_URL
    )

    assert json.loads(request.content) == {"name": "thing"}
    assert "note" not in request.headers
    assert route.request_body is not None
    assert route.request_body.content_schema == {
        "application/json": {"allOf": [{"$ref": "#/components/schemas/Thing"}]}
    }
