"""Explicit null body properties must survive JSON request construction."""

import json
from typing import Any

import pytest
from jsonschema_path import SchemaPath

from fastmcp.utilities.openapi.director import RequestDirector
from fastmcp.utilities.openapi.parser import parse_openapi_to_http_routes


@pytest.mark.parametrize(
    ("version", "property_schema"),
    [
        ("3.0.3", {"type": "string", "nullable": True}),
        ("3.1.0", {"type": ["string", "null"]}),
        ("3.1.0", {"anyOf": [{"type": "string"}, {"type": "null"}]}),
        ("3.1.0", {"oneOf": [{"type": "string"}, {"type": "null"}]}),
        ("3.1.0", {"$ref": "#/components/schemas/NullableString"}),
    ],
)
@pytest.mark.parametrize("with_name", [False, True])
@pytest.mark.parametrize(
    "media_type", ["application/json", "application/merge-patch+json"]
)
def test_explicit_json_null(
    version: str, property_schema: dict[str, Any], with_name: bool, media_type: str
) -> None:
    spec = _spec(version, property_schema, media_type)
    route = parse_openapi_to_http_routes(spec)[0]
    director = RequestDirector(SchemaPath.from_dict(spec))
    arguments: dict[str, Any] = {"folder_id": None, "filter": None}
    expected: dict[str, Any] = {"folder_id": None}
    if with_name:
        arguments["name"] = "report.pdf"
        expected["name"] = "report.pdf"
    request = director.build(route, arguments)
    assert json.loads(request.content) == expected
    assert request.headers["content-type"] == media_type
    assert "filter" not in request.url.params

    omitted = director.build(route, {"name": "report.pdf"})
    assert json.loads(omitted.content) == {"name": "report.pdf"}


@pytest.mark.parametrize(
    "media_type",
    ["multipart/form-data", "application/x-www-form-urlencoded", "application/json"],
)
def test_null_not_allowed_by_encoding_or_schema(media_type: str) -> None:
    property_schema = (
        {"type": "string"}
        if media_type == "application/json"
        else {"type": ["string", "null"]}
    )
    spec = _spec("3.1.0", property_schema, media_type)
    route = parse_openapi_to_http_routes(spec)[0]
    director = RequestDirector(SchemaPath.from_dict(spec))
    request = director.build(route, {"folder_id": None})
    assert request.content == b""


def test_null_uses_selected_media_type() -> None:
    spec = _spec("3.1.0", {"type": "string"}, "application/json")
    content = spec["paths"]["/files"]["patch"]["requestBody"]["content"]
    content["application/merge-patch+json"] = {
        "schema": {
            "type": "object",
            "properties": {"folder_id": {"type": ["string", "null"]}},
        }
    }
    route = parse_openapi_to_http_routes(spec)[0]
    request = RequestDirector(SchemaPath.from_dict(spec)).build(
        route, {"folder_id": None}
    )
    assert request.content == b""


def _spec(
    version: str, property_schema: dict[str, Any], media_type: str
) -> dict[str, Any]:
    return {
        "openapi": version,
        "info": {"title": "Files", "version": "1.0"},
        "components": {
            "schemas": {
                "NullableString": (
                    {"type": "string", "nullable": True}
                    if version.startswith("3.0")
                    else {"type": ["string", "null"]}
                )
            }
        },
        "paths": {
            "/files": {
                "patch": {
                    "operationId": "update_file",
                    "parameters": [
                        {"name": "filter", "in": "query", "schema": {"type": "string"}}
                    ],
                    "requestBody": {
                        "content": {
                            media_type: {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "folder_id": property_schema,
                                    },
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
