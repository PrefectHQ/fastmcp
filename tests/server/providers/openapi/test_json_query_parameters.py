"""Content-based query parameters retain their JSON representation on the wire."""

import json
from typing import Any

import httpx2
import pytest

from fastmcp import Client, FastMCP


def make_spec(version: str, content_type: str = "application/json") -> dict[str, Any]:
    return {
        "openapi": version,
        "info": {"title": "JSON query parameters", "version": "1"},
        "paths": {
            "/items": {
                "get": {
                    "operationId": "list_items",
                    "parameters": [
                        {
                            "name": "filter",
                            "in": "query",
                            "content": {content_type: {"schema": {}}},
                        },
                        {"name": "page", "in": "query", "schema": {"type": "integer"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }


async def capture_queries(
    spec: dict[str, Any], arguments: list[dict[str, Any]]
) -> list[httpx2.QueryParams]:
    queries: list[httpx2.QueryParams] = []

    def capture(request: httpx2.Request) -> httpx2.Response:
        queries.append(request.url.params)
        return httpx2.Response(200, json={"ok": True})

    async with httpx2.AsyncClient(
        base_url="https://example.test",
        params={"api_version": "1", "page": "1"},
        transport=httpx2.MockTransport(capture),
    ) as http:
        async with Client(
            FastMCP.from_openapi(openapi_spec=spec, client=http)
        ) as client:
            for args in arguments:
                await client.call_tool("list_items", args)
        assert dict(http.params) == {"api_version": "1", "page": "1"}
    return queries


@pytest.mark.parametrize("version", ["3.0.3", "3.1.0"])
@pytest.mark.parametrize(
    "value",
    [
        {"status": "active", "tags": ["a&b", "é"]},
        [1, False],
        "a&b",
        {},
        [],
        False,
        0,
        None,
    ],
)
async def test_json_query_is_one_encoded_value(version: str, value: Any) -> None:
    queries = await capture_queries(make_spec(version), [{"filter": value, "page": 2}])
    assert set(queries[0]) == {"api_version", "page", "filter"}
    assert len(queries[0].get_list("filter")) == 1
    decoded = json.loads(queries[0]["filter"])
    assert decoded == value
    assert type(decoded) is type(value)
    assert queries[0]["page"] == "2"
    assert queries[0]["api_version"] == "1"


async def test_omitted_json_query_does_not_send_a_value() -> None:
    queries = await capture_queries(make_spec("3.1.0"), [{"filter": {"q": 1}}, {}])
    assert "filter" not in queries[1]
    assert dict(queries[1]) == {"api_version": "1", "page": "1"}


@pytest.mark.parametrize(
    "content_type", ["application/problem+json", "application/json; charset=utf-8"]
)
async def test_json_media_types(content_type: str) -> None:
    queries = await capture_queries(
        make_spec("3.1.0", content_type), [{"filter": {"q": 1}}]
    )
    assert json.loads(queries[0]["filter"]) == {"q": 1}


async def test_plain_text_content_is_unchanged() -> None:
    queries = await capture_queries(
        make_spec("3.1.0", "text/plain"), [{"filter": "a&b"}]
    )
    assert queries[0]["filter"] == "a&b"


@pytest.mark.parametrize("version", ["3.0.3", "3.1.0"])
@pytest.mark.parametrize("nullable", [True, False])
async def test_referenced_schema_controls_explicit_null(
    version: str, nullable: bool
) -> None:
    spec = make_spec(version)
    schema: dict[str, Any] = {"type": "object"}
    if nullable:
        if version.startswith("3.0"):
            schema["nullable"] = True
        else:
            schema["type"] = ["object", "null"]
    spec["components"] = {"schemas": {"Filter": schema}}
    parameter = spec["paths"]["/items"]["get"]["parameters"][0]
    parameter["content"]["application/json"]["schema"] = {
        "$ref": "#/components/schemas/Filter"
    }
    queries = await capture_queries(spec, [{"filter": None}])
    if nullable:
        assert queries[0]["filter"] == "null"
    else:
        assert "filter" not in queries[0]


async def test_referenced_parameter_preserves_json_encoding() -> None:
    spec = make_spec("3.1.0")
    parameters = spec["paths"]["/items"]["get"]["parameters"]
    spec["components"] = {"parameters": {"Filter": parameters[0]}}
    parameters[0] = {"$ref": "#/components/parameters/Filter"}
    queries = await capture_queries(spec, [{"filter": {"q": [1, 2]}}])
    assert json.loads(queries[0]["filter"]) == {"q": [1, 2]}
