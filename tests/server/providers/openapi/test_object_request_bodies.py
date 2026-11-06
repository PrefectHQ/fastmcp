"""Object request bodies retain their shape through OpenAPI tool calls."""

import json
from typing import Any
from urllib.parse import parse_qs

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


def _form_urlencoded_spec(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
    encoding: dict[str, Any] | None = None,
    schema_ref: str | None = None,
) -> dict[str, Any]:
    """Build a spec whose only operation takes a form-urlencoded body."""
    body_schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        body_schema["required"] = required
    media_type: dict[str, Any] = {"schema": body_schema}
    if encoding is not None:
        media_type["encoding"] = encoding
    spec: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {"title": "Form API", "version": "1.0"},
        "paths": {
            "/submit": {
                "post": {
                    "operationId": "submit",
                    "requestBody": {
                        "required": True,
                        "content": {"application/x-www-form-urlencoded": media_type},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    if schema_ref is not None:
        body_schema["properties"]["filter"] = {"$ref": schema_ref}
        spec["components"] = {
            "schemas": {
                "Filter": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "age": {"type": "integer"},
                    },
                }
            }
        }
    return spec


async def _submit_and_capture(
    spec: dict[str, Any], arguments: dict[str, Any]
) -> httpx2.Request:
    requests: list[httpx2.Request] = []

    def capture(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"ok": True})

    async with httpx2.AsyncClient(
        base_url="https://example.com", transport=httpx2.MockTransport(capture)
    ) as http_client:
        server = FastMCP.from_openapi(openapi_spec=spec, client=http_client)
        async with Client(server) as client:
            await client.call_tool("submit", arguments)

    assert len(requests) == 1
    return requests[0]


def _form_fields(request: httpx2.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode("utf-8"), keep_blank_values=True)


def _form_body_schema_spec(
    body_schema: dict[str, Any],
    components: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a form-urlencoded spec around an arbitrary body schema."""
    spec: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {"title": "Form API", "version": "1.0"},
        "paths": {
            "/submit": {
                "post": {
                    "operationId": "submit",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/x-www-form-urlencoded": {"schema": body_schema}
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    if components is not None:
        spec["components"] = {"schemas": components}
    return spec


@pytest.mark.parametrize("use_ref", [False, True], ids=["inline", "ref"])
async def test_form_urlencoded_object_property_is_json_encoded(
    use_ref: bool,
) -> None:
    """Object properties default to contentType application/json in OAS."""
    spec = _form_urlencoded_spec(
        {
            "filter": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "integer"},
                },
            },
            "tags": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer"},
        },
        required=["filter"],
        schema_ref="#/components/schemas/Filter" if use_ref else None,
    )

    request = await _submit_and_capture(
        spec,
        {
            "filter": {"name": "alice", "age": 30},
            "tags": ["a", "b"],
            "limit": 5,
        },
    )

    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    fields = _form_fields(request)
    payload = fields["filter"][0]
    assert "'name'" not in payload
    assert json.loads(payload) == {"name": "alice", "age": 30}
    # Arrays of primitives keep httpx's repeated-key form encoding.
    assert fields["tags"] == ["a", "b"]
    assert fields["limit"] == ["5"]


async def test_form_urlencoded_object_property_encodes_nested_null() -> None:
    """Nested None inside a JSON-encoded object becomes JSON null."""
    spec = _form_urlencoded_spec(
        {
            "filter": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "metadata": {"type": ["string", "null"]},
                },
            }
        },
        required=["filter"],
    )

    request = await _submit_and_capture(
        spec, {"filter": {"name": "alice", "metadata": None}}
    )

    payload = _form_fields(request)["filter"][0]
    assert "None" not in payload
    assert "null" in payload
    assert json.loads(payload) == {"name": "alice", "metadata": None}


async def test_form_urlencoded_array_of_objects_is_json_encoded() -> None:
    spec = _form_urlencoded_spec(
        {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "note": {"type": ["string", "null"]},
                    },
                },
            }
        },
        required=["rows"],
    )

    request = await _submit_and_capture(
        spec, {"rows": [{"id": 1}, {"id": 2, "note": None}]}
    )

    payload = _form_fields(request)["rows"][0]
    assert "None" not in payload
    assert "'id'" not in payload
    assert json.loads(payload) == [{"id": 1}, {"id": 2, "note": None}]


async def test_form_urlencoded_explicit_json_encoding_is_honored() -> None:
    """An explicit encoding contentType overrides the default serialization."""
    spec = _form_urlencoded_spec(
        {"tags": {"type": "array", "items": {"type": "string"}}},
        required=["tags"],
        encoding={"tags": {"contentType": "application/json"}},
    )

    request = await _submit_and_capture(spec, {"tags": ["a", "b"]})

    tags = _form_fields(request)["tags"]
    assert len(tags) == 1
    assert json.loads(tags[0]) == ["a", "b"]


async def test_json_object_body_serialization_is_unchanged() -> None:
    """JSON request bodies keep using a JSON document, not form encoding."""
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "JSON API", "version": "1.0"},
        "paths": {
            "/submit": {
                "post": {
                    "operationId": "submit",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "filter": {
                                            "type": "object",
                                            "properties": {
                                                "name": {"type": "string"},
                                                "metadata": {
                                                    "type": ["string", "null"]
                                                },
                                            },
                                        },
                                        "tags": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }

    request = await _submit_and_capture(
        spec, {"filter": {"name": "alice", "metadata": None}, "tags": ["a", "b"]}
    )

    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {
        "filter": {"name": "alice", "metadata": None},
        "tags": ["a", "b"],
    }


_ROW_OBJECT_SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "integer"}},
}


@pytest.mark.parametrize(
    "rows_schema",
    [
        pytest.param(
            {
                "anyOf": [
                    {"type": "array", "items": _ROW_OBJECT_SCHEMA},
                    {"type": "null"},
                ]
            },
            id="anyOf",
        ),
        pytest.param(
            {
                "oneOf": [
                    {"type": "array", "items": _ROW_OBJECT_SCHEMA},
                    {"type": "null"},
                ]
            },
            id="oneOf",
        ),
        pytest.param(
            {"type": ["array", "null"], "items": _ROW_OBJECT_SCHEMA},
            id="type-list",
        ),
        pytest.param(
            {"allOf": [{"type": "array", "items": _ROW_OBJECT_SCHEMA}]},
            id="allOf",
        ),
    ],
)
async def test_form_urlencoded_nullable_array_of_objects_is_json_encoded(
    rows_schema: dict[str, Any],
) -> None:
    """Nullable/composed array schemas still identify arrays of objects."""
    spec = _form_urlencoded_spec({"rows": rows_schema}, required=["rows"])

    request = await _submit_and_capture(spec, {"rows": [{"id": 1}]})

    payload = _form_fields(request)["rows"][0]
    assert "'id'" not in payload
    assert json.loads(payload) == [{"id": 1}]


async def test_form_urlencoded_ref_to_nullable_array_of_objects_is_json_encoded() -> (
    None
):
    """A `$ref` resolving to a nullable array of objects is JSON-encoded."""
    spec = _form_urlencoded_spec(
        {"rows": {"$ref": "#/components/schemas/NullableRows"}},
        required=["rows"],
    )
    spec["components"] = {
        "schemas": {
            "Row": _ROW_OBJECT_SCHEMA,
            "NullableRows": {
                "anyOf": [
                    {"type": "array", "items": {"$ref": "#/components/schemas/Row"}},
                    {"type": "null"},
                ]
            },
        }
    }

    request = await _submit_and_capture(spec, {"rows": [{"id": 1}]})

    payload = _form_fields(request)["rows"][0]
    assert "'id'" not in payload
    assert json.loads(payload) == [{"id": 1}]


@pytest.mark.parametrize("composition", ["oneOf", "anyOf"])
async def test_form_urlencoded_union_array_uses_matching_branch(
    composition: str,
) -> None:
    """Runtime items choose the union branch instead of any alternative."""
    rows_schema = {
        composition: [
            {"type": "array", "items": _ROW_OBJECT_SCHEMA},
            {"type": "array", "items": {"type": "string"}},
        ]
    }
    spec = _form_urlencoded_spec({"rows": rows_schema}, required=["rows"])

    scalar_request = await _submit_and_capture(spec, {"rows": ["a", "b"]})
    # The unused array-of-objects branch must not force JSON into one value.
    assert _form_fields(scalar_request)["rows"] == ["a", "b"]

    object_request = await _submit_and_capture(spec, {"rows": [{"id": 1}]})
    payload = _form_fields(object_request)["rows"][0]
    assert json.loads(payload) == [{"id": 1}]


async def test_form_urlencoded_cyclic_component_graph_is_serialized() -> None:
    """Mutually recursive composition refs terminate instead of recursing."""
    components = {
        "A": {
            "allOf": [
                {"$ref": "#/components/schemas/B"},
                {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                },
            ]
        },
        "B": {"$ref": "#/components/schemas/A"},
    }
    spec = _form_urlencoded_spec(
        {"filter": {"$ref": "#/components/schemas/A"}}, required=["filter"]
    )
    spec["components"] = {"schemas": components}

    request = await _submit_and_capture(spec, {"filter": {"name": "alice"}})

    payload = _form_fields(request)["filter"][0]
    assert json.loads(payload) == {"name": "alice"}


@pytest.mark.parametrize("use_ref", [False, True], ids=["inline", "ref"])
async def test_form_urlencoded_additional_properties_objects_are_json_encoded(
    use_ref: bool,
) -> None:
    """Object map values from additionalProperties are JSON-encoded."""
    labels_schema = {
        "type": "object",
        "additionalProperties": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        },
    }
    if use_ref:
        spec = _form_body_schema_spec(
            {"$ref": "#/components/schemas/Labels"}, {"Labels": labels_schema}
        )
    else:
        spec = _form_body_schema_spec(labels_schema)

    request = await _submit_and_capture(spec, {"body": {"team": {"name": "infra"}}})

    payload = _form_fields(request)["team"][0]
    assert "'name'" not in payload
    assert json.loads(payload) == {"name": "infra"}
