"""Successful JSON responses must be accepted for every advertised status."""

from copy import deepcopy
from dataclasses import asdict, is_dataclass
from typing import Any

import httpx2
import pytest

from fastmcp import Client, FastMCP

READY_SCHEMA = {
    "type": "object",
    "properties": {"report_id": {"type": "integer"}},
    "required": ["report_id"],
}
QUEUED_SCHEMA = {
    "type": "object",
    "properties": {"job_id": {"type": "string"}},
    "required": ["job_id"],
}


def response(schema: dict[str, Any]) -> dict[str, Any]:
    return {"description": "OK", "content": {"application/json": {"schema": schema}}}


def spec_for(responses: dict[str, Any], version: str = "3.1.0") -> dict[str, Any]:
    return {
        "openapi": version,
        "info": {"title": "Report generation", "version": "1.0"},
        "paths": {
            "/reports": {
                "post": {
                    "operationId": "generate_report",
                    "responses": responses,
                }
            }
        },
    }


async def call_report(
    spec: dict[str, Any],
    status_code: int,
    payload: Any,
    *,
    validate_output: bool = True,
):
    calls = 0

    def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(status_code, json=payload)

    async with httpx2.AsyncClient(
        base_url="https://reports.example.test", transport=httpx2.MockTransport(respond)
    ) as upstream:
        server = FastMCP.from_openapi(
            openapi_spec=spec, client=upstream, validate_output=validate_output
        )
        async with Client(server) as client:
            (tool,) = await client.list_tools()
            try:
                result = await client.call_tool("generate_report", {})
            finally:
                assert calls == 1
    return tool, result


@pytest.mark.parametrize("version", ["3.0.3", "3.1.0"])
@pytest.mark.parametrize("status_code", [200, 202])
@pytest.mark.parametrize("references", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
async def test_alternate_json_success_reaches_client(
    version: str, status_code: int, references: bool, reverse: bool
) -> None:
    schemas = {"Ready": READY_SCHEMA, "Queued": QUEUED_SCHEMA}
    responses = {
        code: response(
            {"$ref": f"#/components/schemas/{name}"} if references else schemas[name]
        )
        for code, name in [("200", "Ready"), ("202", "Queued")]
    }
    if reverse:
        responses = dict(reversed(list(responses.items())))
    spec = spec_for(responses, version)
    spec["components"] = {"schemas": schemas}
    expected = {"report_id": 42} if status_code == 200 else {"job_id": "job-42"}
    tool, result = await call_report(spec, status_code, expected)

    assert tool.output_schema is not None
    assert tool.output_schema["type"] == "object"
    assert result.is_error is False
    assert result.structured_content == expected
    data = asdict(result.data) if is_dataclass(result.data) else result.data
    assert data == expected


@pytest.mark.parametrize(
    "schema,value",
    [
        ({"type": "string"}, "job-42"),
        ({"type": "integer"}, 42),
        ({"type": "array", "items": {"type": "integer"}}, [41, 42]),
    ],
)
@pytest.mark.parametrize("status_code", [200, 202])
@pytest.mark.parametrize("primary_object", [False, True])
async def test_mixed_object_and_non_object_successes(
    schema: dict[str, Any], value: Any, status_code: int, primary_object: bool
) -> None:
    schemas = (READY_SCHEMA, schema) if primary_object else (schema, READY_SCHEMA)
    spec = spec_for({code: response(s) for code, s in zip(("200", "202"), schemas)})
    is_object = (status_code == 200) == primary_object
    expected = {"report_id": 42} if is_object else value
    tool, result = await call_report(spec, status_code, expected)

    assert tool.output_schema is not None
    assert bool(tool.output_schema.get("x-fastmcp-wrap-result")) is not primary_object
    structured = expected if primary_object and is_object else {"result": expected}
    assert result.structured_content == structured
    data = asdict(result.data) if is_dataclass(result.data) else result.data
    assert data == (structured if primary_object else expected)


async def test_overlapping_json_success_schemas_accept_a_shared_result() -> None:
    spec = spec_for({"200": response(READY_SCHEMA), "202": response(QUEUED_SCHEMA)})
    expected = {"report_id": 42, "job_id": "job-42"}
    _, result = await call_report(spec, 202, expected)
    assert result.structured_content == expected


@pytest.mark.parametrize("other_status", ["400", "500", "default"])
async def test_error_response_schema_does_not_widen_success_output(
    other_status: str,
) -> None:
    spec = spec_for(
        {
            "200": response(READY_SCHEMA),
            "202": response(QUEUED_SCHEMA),
            other_status: response({"type": "object"}),
        }
    )
    with pytest.raises(RuntimeError, match="Invalid structured content"):
        await call_report(spec, 200, {"unexpected": True})


@pytest.mark.parametrize("status_code", [200, 202])
@pytest.mark.parametrize("field", ["value", "default"])
async def test_distinct_response_local_definitions_remain_distinct(
    status_code: int, field: str
) -> None:
    schemas = {}
    for code, kind, value_type in [
        ("200", "ready", "integer"),
        ("202", "queued", "string"),
    ]:
        schemas[code] = {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": [kind]},
                field: {"$ref": "#/$defs/Value"},
            },
            "required": ["kind", field],
            "$defs": {"Value": {"type": value_type}},
        }
    spec = spec_for({code: response(schema) for code, schema in schemas.items()})
    before = deepcopy(spec)
    expected = (
        {"kind": "ready", field: 42}
        if status_code == 200
        else {"kind": "queued", field: "job-42"}
    )
    _, result = await call_report(spec, status_code, expected)
    assert result.structured_content == expected
    assert spec == before


async def test_identical_json_successes_keep_single_output_schema() -> None:
    spec = spec_for({"200": response(READY_SCHEMA), "202": response(READY_SCHEMA)})
    tool, result = await call_report(spec, 202, {"report_id": 42})
    assert tool.output_schema == READY_SCHEMA
    assert result.structured_content == {"report_id": 42}


@pytest.mark.parametrize(
    "schema,value",
    [
        ({"type": "integer"}, 42),
        ({"type": "array", "items": {"type": "integer"}}, [41, 42]),
    ],
)
@pytest.mark.parametrize("status_code", [200, 202])
async def test_disabled_validation_preserves_primary_response_wrapping(
    schema: dict[str, Any], value: Any, status_code: int
) -> None:
    spec = spec_for({"200": response(READY_SCHEMA), "202": response(schema)})
    payload = {"report_id": 42} if status_code == 200 else value
    tool, result = await call_report(spec, status_code, payload, validate_output=False)

    assert tool.output_schema == {"type": "object", "additionalProperties": True}
    expected = payload if status_code == 200 else {"result": payload}
    assert result.structured_content == expected
    assert result.data == expected
