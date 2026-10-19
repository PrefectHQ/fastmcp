"""Object request bodies retain their shape through OpenAPI tool calls."""

import json
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI

from fastmcp import Client, FastMCP


@pytest.mark.parametrize("openapi_version", ["3.0.3", "3.1.0"])
@pytest.mark.parametrize("use_ref", [False, True], ids=["inline", "ref"])
@pytest.mark.parametrize(
    "schema,arguments,expected",
    [
        pytest.param(
            {"type": "object", "additionalProperties": {"type": "string"}},
            {"body": {"team": "infra"}},
            {"team": "infra"},
            id="typed-dictionary",
        ),
        pytest.param(
            {"type": "object", "additionalProperties": True},
            {"body": {"team": {"name": "infra"}}},
            {"team": {"name": "infra"}},
            id="free-form-dictionary",
        ),
        pytest.param(
            {
                "type": "object",
                "properties": {},
                "additionalProperties": {"type": "string"},
            },
            {"body": {"team": "infra"}},
            {"team": "infra"},
            id="empty-properties",
        ),
        pytest.param(
            {"type": "object", "additionalProperties": {"type": "string"}},
            {"body": {}},
            {},
            id="empty-dictionary",
        ),
        pytest.param(
            {
                "type": "object",
                "title": "Labels",
                "additionalProperties": {"type": "string"},
            },
            {"labels": {"team": "infra"}},
            {"team": "infra"},
            id="titled-dictionary",
        ),
        pytest.param(
            {"type": "object", "properties": {"body": {"type": "string"}}},
            {"body": "message"},
            {"body": "message"},
            id="property-named-body",
        ),
        pytest.param(
            {
                "type": "object",
                "title": "Labels",
                "properties": {
                    "labels": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    }
                },
            },
            {"labels": {"team": "infra"}},
            {"labels": {"team": "infra"}},
            id="property-matching-title",
        ),
        pytest.param(
            {
                "allOf": [
                    {
                        "type": "object",
                        "properties": {"body": {"type": "string"}},
                    }
                ]
            },
            {"body": "message"},
            {"body": "message"},
            id="allof-property",
        ),
    ],
)
async def test_object_json_body_reaches_http_endpoint(
    openapi_version: str,
    use_ref: bool,
    schema: dict[str, Any],
    arguments: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    body_schema = {"$ref": "#/components/schemas/Labels"} if use_ref else schema
    spec = {
        "openapi": openapi_version,
        "info": {"title": "Labels API", "version": "1.0"},
        "paths": {
            "/accounts/{account_id}/labels": {
                "post": {
                    "operationId": "set_labels",
                    "parameters": [
                        {
                            "name": "account_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "dry_run",
                            "in": "query",
                            "schema": {"type": "boolean"},
                        },
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": body_schema}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        "components": {"schemas": {"Labels": schema}},
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
            tool = (await client.list_tools())[0]
            assert set(tool.input_schema["properties"]) == {
                "account_id",
                "dry_run",
                *arguments,
            }
            await client.call_tool(
                "set_labels", {"account_id": "acme", "dry_run": True, **arguments}
            )

    assert len(requests) == 1
    assert requests[0].url.path == "/accounts/acme/labels"
    assert dict(requests[0].url.params) == {"dry_run": "true"}
    assert json.loads(requests[0].content) == expected


@pytest.mark.parametrize("labels", [{"team": "infra"}, {}])
async def test_fastapi_dictionary_body(labels: dict[str, str]) -> None:
    app = FastAPI()

    @app.post("/labels", operation_id="set_labels")
    def set_labels(labels: dict[str, str]) -> dict[str, str]:
        return labels

    async with httpx2.AsyncClient(
        base_url="https://example.com", transport=httpx2.ASGITransport(app)
    ) as http_client:
        server = FastMCP.from_openapi(openapi_spec=app.openapi(), client=http_client)
        async with Client(server) as client:
            result = await client.call_tool("set_labels", {"labels": labels})

    assert result.structured_content == labels
