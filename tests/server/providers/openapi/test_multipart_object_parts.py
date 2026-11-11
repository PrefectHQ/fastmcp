"""Multipart object properties are sent as JSON parts, not Python reprs."""

import json
from email.parser import BytesParser
from email.policy import default
from typing import Any

import pytest
from jsonschema_path import SchemaPath

from fastmcp.utilities.openapi.director import RequestDirector
from fastmcp.utilities.openapi.models import HTTPRoute, RequestBodyInfo


def _build_parts(
    properties: dict[str, Any],
    arguments: dict[str, Any],
    encoding: dict[str, Any] | None = None,
) -> dict[str, tuple[str, bytes]]:
    """Build the request and return each part's (content type, payload) by name."""
    route = HTTPRoute(
        path="/items",
        method="POST",
        request_body=RequestBodyInfo(
            content_schema={
                "multipart/form-data": {"type": "object", "properties": properties}
            },
            encoding={"multipart/form-data": encoding} if encoding else {},
        ),
        parameter_map={
            name: {"location": "body", "openapi_name": name} for name in properties
        },
    )
    request = RequestDirector(SchemaPath.from_dict({})).build(
        route, arguments, "http://test"
    )
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
        + request.read()
    )
    parts: dict[str, tuple[str, bytes]] = {}
    for part in message.iter_parts():
        name = str(part.get_param("name", header="content-disposition"))
        payload = part.get_payload(decode=True)
        assert isinstance(payload, bytes)
        parts[name] = (part.get_content_type(), payload)
    return parts


def test_object_property_is_sent_as_a_json_part() -> None:
    value = {"title": "a", "n": 1, "x": None}
    parts = _build_parts({"meta": {"type": "object"}}, {"meta": value})

    assert parts["meta"][0] == "application/json"
    assert json.loads(parts["meta"][1]) == value


def test_array_of_objects_property_is_sent_as_a_json_part() -> None:
    value = [{"title": "a"}, {"title": "b"}]
    parts = _build_parts(
        {"items": {"type": "array", "items": {"type": "object"}}}, {"items": value}
    )

    assert parts["items"][0] == "application/json"
    assert json.loads(parts["items"][1]) == value


def test_explicit_json_encoding_content_type_is_used_for_the_part() -> None:
    parts = _build_parts(
        {"meta": {"type": "object"}},
        {"meta": {"title": "a"}},
        encoding={"meta": {"contentType": "application/vnd.api+json"}},
    )

    assert parts["meta"][0] == "application/vnd.api+json"
    assert json.loads(parts["meta"][1]) == {"title": "a"}


def test_object_with_non_json_encoding_keeps_its_serialization() -> None:
    parts = _build_parts(
        {"meta": {"type": "object"}},
        {"meta": {"title": "a"}},
        encoding={"meta": {"contentType": "text/plain"}},
    )

    assert parts["meta"][1] == b"{'title': 'a'}"


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_non_finite_numbers_in_an_object_are_rejected(value: float) -> None:
    with pytest.raises(ValueError):
        _build_parts({"meta": {"type": "object"}}, {"meta": {"n": value}})
