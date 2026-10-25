"""Multipart string arrays arrive as repeated form fields."""

import io
from email.parser import BytesParser
from email.policy import default
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI, Request
from jsonschema_path import SchemaPath

from fastmcp import Client, FastMCP
from fastmcp.utilities.openapi.director import RequestDirector
from fastmcp.utilities.openapi.models import HTTPRoute, RequestBodyInfo


@pytest.mark.parametrize("include_label", [False, True])
@pytest.mark.parametrize("openapi_version", ["3.0.3", "3.1.0"])
@pytest.mark.parametrize("tags", [["a", "b"], ["a"], [], ["中文", "a&b"]])
@pytest.mark.parametrize(
    "encoding,repeat",
    [
        (None, True),
        ({}, True),
        ({"explode": True}, True),
        ({"style": "form"}, True),
        ({"contentType": "text/plain"}, True),
        (
            {
                "style": "form",
                "explode": True,
                "contentType": "text/plain",
                "allowReserved": False,
                "headers": {},
            },
            True,
        ),
        ({"explode": False}, False),
        ({"contentType": "application/json"}, False),
        ({"contentType": "text/plain; charset=iso-8859-1"}, False),
        ({"headers": {"X-Part": {"schema": {"type": "string"}}}}, False),
    ],
)
async def test_multipart_string_array_defaults(
    include_label: bool,
    openapi_version: str,
    tags: list[str],
    encoding: dict[str, Any] | None,
    repeat: bool,
):
    app = FastAPI()

    @app.post("/items")
    async def receive(request: Request):
        form = await request.form()
        assert request.headers["content-type"].startswith(
            "multipart/form-data; boundary="
        )
        return {"tags": form.getlist("tags"), "label": form.get("label")}

    media: dict[str, Any] = {
        "schema": {
            "type": "object",
            "properties": {
                "tags": {"type": "array", "items": {"type": "string"}},
                "label": {"type": "string"},
            },
            "required": ["tags", "label"],
        }
    }
    if not include_label:
        del media["schema"]["properties"]["label"]
        media["schema"]["required"].remove("label")
    if encoding is not None:
        media["encoding"] = {"tags": encoding}
    spec = {
        "openapi": openapi_version,
        "info": {"title": "Multipart", "version": "1"},
        "paths": {
            "/items": {
                "post": {
                    "operationId": "submit",
                    "requestBody": {
                        "required": True,
                        "content": {"multipart/form-data": media},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    async with httpx2.AsyncClient(
        base_url="http://test", transport=httpx2.ASGITransport(app=app)
    ) as http:
        server = FastMCP.from_openapi(openapi_spec=spec, client=http)
        async with Client(server) as client:
            arguments: dict[str, Any] = {"tags": tags}
            if include_label:
                arguments["label"] = "keep"
            result = await client.call_tool("submit", arguments)
    # Custom encodings keep their existing wire format.
    assert result.structured_content == {
        "tags": tags if tags and repeat else [str(tags)],
        "label": "keep" if include_label else None,
    }


def test_only_empty_array_preserves_multipart_content_type() -> None:
    route = HTTPRoute(
        path="/items",
        method="POST",
        request_body=RequestBodyInfo(
            content_schema={
                "multipart/form-data": {
                    "type": "object",
                    "properties": {
                        "tags": {"type": "array", "items": {"type": "string"}}
                    },
                }
            }
        ),
        parameter_map={"tags": {"location": "body", "openapi_name": "tags"}},
    )
    request = RequestDirector(SchemaPath.from_dict({})).build(
        route, {"tags": []}, "http://test"
    )
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
        + request.read()
    )
    assert message.get_content_type() == "multipart/form-data"
    parts = list(message.iter_parts())
    assert len(parts) == 1
    assert parts[0].get_payload(decode=True) == b"[]"


@pytest.mark.parametrize(
    "kind",
    ["tuple", "bytes", "stream", "object", "nested", "integers", "mixed", "boolean"],
)
def test_multipart_arrays_preserve_other_parts(kind: str) -> None:
    values = {
        "tuple": ("sample.txt", b"payload", "text/plain"),
        "bytes": b"payload",
        "stream": io.BytesIO(b"payload"),
        "object": {"name": "alice"},
        "nested": [["a"]],
        "integers": [1, 2],
        "mixed": ["a", 2],
        "boolean": True,
    }
    route = HTTPRoute(
        path="/items",
        method="POST",
        request_body=RequestBodyInfo(
            content_schema={
                "multipart/form-data": {
                    "type": "object",
                    "properties": {
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "other": {},
                    },
                }
            }
        ),
        parameter_map={
            name: {"location": "body", "openapi_name": name}
            for name in ["tags", "other"]
        },
    )
    try:
        request = RequestDirector(SchemaPath.from_dict({})).build(
            route, {"tags": ["a", "b"], "other": values[kind]}, "http://test"
        )
        raw = request.read()
        message = BytesParser(policy=default).parsebytes(
            f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode() + raw
        )
        parts = list(message.iter_parts())
        assert [
            part.get_param("name", header="content-disposition") for part in parts
        ] == ["tags", "tags", "other"]
        assert [part.get_payload(decode=True) for part in parts[:2]] == [b"a", b"b"]
        expected = (
            b"payload"
            if kind in {"tuple", "bytes", "stream"}
            else ("true" if kind == "boolean" else str(values[kind])).encode()
        )
        assert parts[2].get_payload(decode=True) == expected
        if kind == "tuple":
            assert parts[2].get_filename() == "sample.txt"
    finally:
        values["stream"].close()
