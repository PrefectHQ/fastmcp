"""OpenAPI tools send only the arguments their operation declares."""

import json
from typing import Any

import httpx2
import pytest
from jsonschema_path import SchemaPath

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.utilities.openapi import director, parser
from fastmcp.utilities.openapi.director import RequestDirector
from fastmcp.utilities.openapi.models import (
    HTTPRoute,
    ParameterInfo,
    RequestBodyInfo,
)

BASE_URL = "https://api.example.com"
CONFIGURED_HEADERS = {"Authorization": "Bearer configured", "X-Roles": "readonly"}
CONFIGURED_COOKIES = {"session": "configured-session"}

UNDECLARED_ARGUMENTS = [
    pytest.param({"Authorization__header": "Bearer other"}, id="authorization"),
    pytest.param({"X-Roles__header": "admin"}, id="custom-header"),
    pytest.param({"Host__header": "other.invalid"}, id="host"),
    pytest.param({"session__cookie": "other-session"}, id="cookie"),
    pytest.param({"include_private__query": "true"}, id="query"),
    pytest.param({"extra": {"nested": "value"}}, id="body"),
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


async def _call_tool(
    spec: dict[str, Any],
    arguments: dict[str, Any],
    requests: list[httpx2.Request],
) -> None:
    def capture(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"ok": True})

    async with httpx2.AsyncClient(
        base_url=BASE_URL,
        headers=CONFIGURED_HEADERS,
        cookies=CONFIGURED_COOKIES,
        transport=httpx2.MockTransport(capture),
    ) as http_client:
        server = FastMCP.from_openapi(openapi_spec=spec, client=http_client)
        async with Client(server) as client:
            await client.call_tool("operation", arguments)


def _assert_configured_request(request: httpx2.Request) -> None:
    assert request.headers["Authorization"] == "Bearer configured"
    assert request.headers["X-Roles"] == "readonly"
    assert request.headers["Host"] == "api.example.com"
    assert request.headers["Cookie"] == "session=configured-session"


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("arguments", UNDECLARED_ARGUMENTS)
async def test_operation_without_parameters_drops_undeclared_arguments(
    method: str, arguments: dict[str, Any]
) -> None:
    spec = _spec("/health", method, {})

    requests: list[httpx2.Request] = []
    await _call_tool(spec, arguments, requests)

    [request] = requests

    _assert_configured_request(request)
    assert str(request.url) == f"{BASE_URL}/health"
    assert request.content == b""


async def test_failed_schema_precalculation_drops_undeclared_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("schema pre-calculation failed")

    monkeypatch.setattr(parser, "_combine_schemas_and_map_params", fail)
    limit = {"name": "limit", "in": "query", "schema": {"type": "integer"}}
    spec = _spec("/items", "get", {"parameters": [limit]})

    requests: list[httpx2.Request] = []
    await _call_tool(
        spec,
        {"limit": 5, "Authorization__header": "Bearer other", "extra": "value"},
        requests,
    )

    [request] = requests

    _assert_configured_request(request)
    assert dict(request.url.params) == {"limit": "5"}
    assert request.content == b""


async def test_unbuildable_parameter_map_sends_no_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("schema pre-calculation failed")

    monkeypatch.setattr(parser, "_combine_schemas_and_map_params", fail)
    monkeypatch.setattr(director, "_combine_schemas_and_map_params", fail)
    limit = {"name": "limit", "in": "query", "schema": {"type": "integer"}}
    spec = _spec("/items", "get", {"parameters": [limit]})

    requests: list[httpx2.Request] = []
    with pytest.raises(ToolError, match="schema pre-calculation failed"):
        await _call_tool(
            spec, {"limit": 5, "Authorization__header": "Bearer other"}, requests
        )

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
async def test_whole_body_keeps_its_contents_and_drops_undeclared_arguments(
    schema: dict[str, Any],
) -> None:
    body_schema = {"$ref": "#/components/schemas/Thing"}
    request_body = {
        "required": True,
        "content": {"application/json": {"schema": body_schema}},
    }
    spec = _spec("/things", "post", {"requestBody": request_body})
    spec["components"] = {"schemas": {"Thing": schema}}
    body = {"Authorization__header": "body data", "extra": "body data"}

    requests: list[httpx2.Request] = []
    await _call_tool(
        spec, {"body": body, "Authorization__header": "Bearer other"}, requests
    )

    [request] = requests

    _assert_configured_request(request)
    assert json.loads(request.content) == body


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
            "Authorization__header": "Bearer other",
            "extra": "value",
        },
        BASE_URL,
    )

    assert request.url.path == "/things/path-value"
    assert dict(request.url.params) == {"id": "query-value"}
    assert request.headers["id"] == "header-value"
    assert "Authorization" not in request.headers
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
        route, {"name": "thing", "Authorization__header": "Bearer other"}, BASE_URL
    )

    assert json.loads(request.content) == {"name": "thing"}
    assert "Authorization" not in request.headers
    assert route.request_body is not None
    assert route.request_body.content_schema == {
        "application/json": {"allOf": [{"$ref": "#/components/schemas/Thing"}]}
    }
