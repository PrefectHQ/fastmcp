"""Strict input validation enforces nested OpenAPI request-body schemas.

With ``strict_input_validation=True``, an OpenAPI-generated tool must reject
calls whose arguments violate the advertised schema *inside* nested objects
(wrong types, extra keys under ``additionalProperties: false``, missing
required properties), not just undeclared top-level arguments. Without this,
invalid values reach the HTTP transport verbatim.
"""

import json
from typing import Any

import httpx2
import pytest

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

BASE_URL = "https://api.example.com"

NESTED_SCHEMA = {
    "type": "object",
    "properties": {
        "options": {
            "type": "object",
            "properties": {"level": {"type": "integer"}},
            "required": ["level"],
            "additionalProperties": False,
        }
    },
    "required": ["options"],
}

REF_SCHEMA = {
    "type": "object",
    "properties": {"payload": {"$ref": "#/components/schemas/Item"}},
    "required": ["payload"],
}


def _spec(body_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Fixture", "version": "1"},
        "paths": {
            "/run": {
                "post": {
                    "operationId": "submit",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": body_schema}},
                    },
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }


def _spec_with_components(body_schema: dict[str, Any]) -> dict[str, Any]:
    spec = _spec(body_schema)
    spec["components"] = {
        "schemas": {
            "Item": {
                "type": "object",
                "properties": {"count": {"type": "integer"}},
                "required": ["count"],
                "additionalProperties": False,
            }
        }
    }
    return spec


def _upstream_client(requests: list[httpx2.Request]) -> httpx2.AsyncClient:
    def capture(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"ok": True})

    return httpx2.AsyncClient(
        base_url=BASE_URL, transport=httpx2.MockTransport(capture)
    )


async def _call_tool(
    spec: dict[str, Any], arguments: dict[str, Any], *, strict: bool
) -> list[httpx2.Request]:
    requests: list[httpx2.Request] = []
    async with _upstream_client(requests) as http_client:
        server = FastMCP.from_openapi(
            openapi_spec=spec, client=http_client, strict_input_validation=strict
        )
        async with Client(server) as client:
            await client.call_tool("submit", arguments)
    return requests


@pytest.mark.parametrize(
    ("arguments", "label"),
    [
        pytest.param({"options": {"level": "one", "extra": 1}}, "type", id="type"),
        pytest.param(
            {"options": {"level": 1, "extra": 1}},
            "additional-properties",
            id="additional-properties",
        ),
        pytest.param({"options": {}}, "missing-required", id="missing-required"),
        pytest.param({"options": None}, "null", id="null"),
    ],
)
async def test_strict_validation_rejects_nested_violations(
    arguments: dict[str, Any], label: str
) -> None:
    requests = []
    with pytest.raises(ToolError, match="does not match its schema"):
        async with _upstream_client(requests) as http_client:
            server = FastMCP.from_openapi(
                openapi_spec=_spec(NESTED_SCHEMA),
                client=http_client,
                strict_input_validation=True,
            )
            async with Client(server) as client:
                await client.call_tool("submit", arguments)
    assert requests == [], f"{label} violation was dispatched to the transport"


async def test_strict_validation_accepts_valid_nested_body() -> None:
    requests = await _call_tool(
        _spec(NESTED_SCHEMA), {"options": {"level": 3}}, strict=True
    )
    assert [json.loads(r.content) for r in requests] == [{"options": {"level": 3}}]


async def test_lax_validation_still_dispatches_invalid_nested_values() -> None:
    requests = await _call_tool(
        _spec(NESTED_SCHEMA),
        {"options": {"level": "one", "extra": 1}},
        strict=False,
    )
    assert [json.loads(r.content) for r in requests] == [
        {"options": {"level": "one", "extra": 1}}
    ]


async def test_strict_validation_rejects_nested_violation_behind_ref() -> None:
    requests = []
    with pytest.raises(ToolError, match="does not match its schema"):
        async with _upstream_client(requests) as http_client:
            server = FastMCP.from_openapi(
                openapi_spec=_spec_with_components(REF_SCHEMA),
                client=http_client,
                strict_input_validation=True,
            )
            async with Client(server) as client:
                await client.call_tool("submit", {"payload": {"count": "one"}})
    assert requests == []


async def test_strict_validation_accepts_valid_nested_body_behind_ref() -> None:
    requests = await _call_tool(
        _spec_with_components(REF_SCHEMA), {"payload": {"count": 1}}, strict=True
    )
    assert [json.loads(r.content) for r in requests] == [{"payload": {"count": 1}}]
