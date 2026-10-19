"""Whole-body arguments must not hide parameters in other HTTP locations."""

import json
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI, Query
from jsonschema import Draft202012Validator

from fastmcp import Client, FastMCP


@pytest.mark.parametrize("openapi_version", ["3.0.3", "3.1.0"])
@pytest.mark.parametrize("location", ["path", "query", "header", "cookie"])
@pytest.mark.parametrize(
    "schema,body_name,payload,use_ref",
    [
        pytest.param(
            {"type": "array", "items": {"type": "integer"}, "title": "Values"},
            "values",
            [1, 2],
            False,
            id="titled-array",
        ),
        pytest.param(
            {"type": "string"}, "body", "message", False, id="untitled-scalar"
        ),
        pytest.param(
            {
                "type": "object",
                "title": "Labels",
                "additionalProperties": {"type": "string"},
            },
            "labels",
            {"team": "infra"},
            True,
            id="referenced-dictionary",
        ),
    ],
)
async def test_whole_body_and_http_parameter_reach_their_locations(
    openapi_version: str,
    location: str,
    schema: dict[str, Any],
    body_name: str,
    payload: Any,
    use_ref: bool,
) -> None:
    path = f"/values/{{{body_name}}}" if location == "path" else "/values"
    spec = {
        "openapi": openapi_version,
        "info": {"title": "Body collisions", "version": "1.0"},
        "paths": {
            path: {
                "post": {
                    "operationId": "set_value",
                    "parameters": [
                        {
                            "name": body_name,
                            "in": location,
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Payload"}
                                if use_ref
                                else schema
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        "components": {"schemas": {"Payload": schema}},
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
            arguments = {body_name: payload, f"{body_name}__{location}": "active"}
            assert set(tool.input_schema["properties"]) == set(arguments)
            Draft202012Validator.check_schema(tool.input_schema)
            assert set(tool.input_schema["required"]) == set(arguments)
            await client.call_tool("set_value", arguments)

    assert len(requests) == 1
    request = requests[0]
    assert json.loads(request.content) == payload
    if location == "path":
        assert request.url.path == "/values/active"
    elif location == "query":
        assert dict(request.url.params) == {body_name: "active"}
    elif location == "header":
        assert request.headers[body_name] == "active"
    else:
        assert request.headers["cookie"] == f"{body_name}=active"


async def test_fastapi_body_title_does_not_hide_query_alias() -> None:
    app = FastAPI()

    @app.post("/values", operation_id="set_values")
    def set_values(
        values: list[int], group: str = Query(alias="values")
    ) -> dict[str, Any]:
        return {"group": group, "values": values}

    async with httpx2.AsyncClient(
        base_url="https://example.com", transport=httpx2.ASGITransport(app)
    ) as http_client:
        direct = await http_client.post(
            "/values", params={"values": "active"}, json=[1, 2]
        )
        assert direct.status_code == 200
        server = FastMCP.from_openapi(openapi_spec=app.openapi(), client=http_client)
        async with Client(server) as client:
            result = await client.call_tool(
                "set_values", {"values": [1, 2], "values__query": "active"}
            )

    assert (
        result.structured_content
        == direct.json()
        == {
            "group": "active",
            "values": [1, 2],
        }
    )
