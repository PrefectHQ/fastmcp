"""Header and path parameter values follow the OpenAPI "simple" style."""

from typing import Any

import httpx2
import pytest
from jsonschema_path import SchemaPath

from fastmcp import Client, FastMCP
from fastmcp.utilities.openapi.director import RequestDirector


def make_spec(parameters: list[dict[str, Any]], path: str = "/items") -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Header/path scalars", "version": "1"},
        "paths": {
            path: {
                "get": {
                    "operationId": "get_items",
                    "parameters": parameters,
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }


async def call_and_capture(
    spec: dict[str, Any], args: dict[str, Any]
) -> list[httpx2.Request]:
    requests: list[httpx2.Request] = []

    def capture(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"ok": True})

    async with httpx2.AsyncClient(
        base_url="https://example.test", transport=httpx2.MockTransport(capture)
    ) as http_client:
        server = FastMCP.from_openapi(openapi_spec=spec, client=http_client)
        async with Client(server) as client:
            await client.call_tool("get_items", args)

    assert len(requests) == 1
    return requests


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, "true"),
        (False, "false"),
        (5, "5"),
        (1.5, "1.5"),
        ("plain", "plain"),
    ],
)
async def test_header_scalar_serialization(value: Any, expected: str) -> None:
    spec = make_spec(
        [
            {
                "name": "X-Flag",
                "in": "header",
                "required": True,
                "schema": {"type": "string"},
            }
        ]
    )
    requests = await call_and_capture(spec, {"X-Flag": value})
    assert requests[0].headers["x-flag"] == expected


async def test_header_array_serialization() -> None:
    spec = make_spec(
        [
            {
                "name": "X-Tags",
                "in": "header",
                "required": True,
                "schema": {"type": "array", "items": {"type": "string"}},
            }
        ]
    )
    requests = await call_and_capture(spec, {"X-Tags": ["a", "b"]})
    assert requests[0].headers["x-tags"] == "a,b"


@pytest.mark.parametrize(
    "explode,expected",
    [
        (None, "R,100,G,200"),
        (False, "R,100,G,200"),
        (True, "R=100,G=200"),
    ],
)
async def test_header_object_serialization(explode: bool | None, expected: str) -> None:
    parameter: dict[str, Any] = {
        "name": "X-Color",
        "in": "header",
        "required": True,
        "schema": {"type": "object"},
    }
    if explode is not None:
        parameter["explode"] = explode
    spec = make_spec([parameter])
    requests = await call_and_capture(spec, {"X-Color": {"R": 100, "G": 200}})
    assert requests[0].headers["x-color"] == expected


async def test_path_array_serialization() -> None:
    spec = make_spec(
        [
            {
                "name": "ids",
                "in": "path",
                "required": True,
                "schema": {"type": "array", "items": {"type": "string"}},
            }
        ],
        path="/items/{ids}",
    )
    requests = await call_and_capture(spec, {"ids": ["a", "b"]})
    assert requests[0].url.path == "/items/a,b"


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, "/items/true"),
        (5, "/items/5"),
        ("a b", "/items/a b"),
    ],
)
async def test_path_scalar_serialization(value: Any, expected: str) -> None:
    spec = make_spec(
        [
            {
                "name": "id",
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }
        ],
        path="/items/{id}",
    )
    requests = await call_and_capture(spec, {"id": value})
    assert requests[0].url.path == expected


@pytest.mark.parametrize(
    "explode,expected",
    [
        (None, "/items/R,100,G,200"),
        (True, "/items/R=100,G=200"),
    ],
)
async def test_path_object_serialization(explode: bool | None, expected: str) -> None:
    parameter: dict[str, Any] = {
        "name": "filter",
        "in": "path",
        "required": True,
        "schema": {"type": "object"},
    }
    if explode is not None:
        parameter["explode"] = explode
    spec = make_spec([parameter], path="/items/{filter}")
    requests = await call_and_capture(spec, {"filter": {"R": 100, "G": 200}})
    assert requests[0].url.path == expected


async def test_path_array_items_encode_separately_on_the_wire() -> None:
    """url.path decodes %2C, so the two forms only differ on the raw wire path."""
    spec = make_spec(
        [
            {
                "name": "ids",
                "in": "path",
                "required": True,
                "schema": {"type": "array", "items": {"type": "string"}},
            }
        ],
        path="/items/{ids}",
    )
    requests = await call_and_capture(spec, {"ids": ["a", "b"]})
    assert requests[0].url.raw_path == b"/items/a,b"

    requests = await call_and_capture(spec, {"ids": ["a,b"]})
    assert requests[0].url.raw_path == b"/items/a%2Cb"


async def test_path_object_pairs_encode_separately_on_the_wire() -> None:
    spec = make_spec(
        [
            {
                "name": "filter",
                "in": "path",
                "required": True,
                "schema": {"type": "object"},
            }
        ],
        path="/items/{filter}",
    )
    requests = await call_and_capture(spec, {"filter": {"R": "1,0", "G": 200}})
    assert requests[0].url.raw_path == b"/items/R,1%2C0,G,200"


async def test_header_nested_array_flattens_one_level() -> None:
    spec = make_spec(
        [
            {
                "name": "X-Tags",
                "in": "header",
                "required": True,
                "schema": {"type": "array", "items": {"type": "string"}},
            }
        ]
    )
    requests = await call_and_capture(spec, {"X-Tags": [["a", "b"], "c"]})
    assert requests[0].headers["x-tags"] == "a,b,c"


@pytest.mark.parametrize(
    "value",
    [
        [".."],
        ["a", ".."],
        ["%2e%2e"],
        {"lat": ".."},
        "..",
    ],
)
def test_path_serialized_items_reject_dot_segments(value: Any) -> None:
    with pytest.raises(ValueError, match="dot segments"):
        RequestDirector(SchemaPath.from_dict({}))._build_url(
            "/items/{ids}", {"ids": value}, "https://api.example.com"
        )


def test_path_missing_parameter_raises() -> None:
    with pytest.raises(ValueError, match="Missing required path parameters"):
        RequestDirector(SchemaPath.from_dict({}))._build_url(
            "/items/{ids}", {}, "https://api.example.com"
        )


async def test_path_nested_array_flattens_one_level() -> None:
    spec = make_spec(
        [
            {
                "name": "ids",
                "in": "path",
                "required": True,
                "schema": {"type": "array", "items": {"type": "string"}},
            }
        ],
        path="/items/{ids}",
    )
    requests = await call_and_capture(spec, {"ids": [["a", "b"], "c"]})
    assert requests[0].url.raw_path == b"/items/a,b,c"
