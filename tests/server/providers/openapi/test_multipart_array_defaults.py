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


@pytest.mark.parametrize("tags", [["a", "b"], ["a"], [], ["中文", "a&b"]])
@pytest.mark.parametrize(
    "encoding", [None, {"explode": False}, {"contentType": "application/json"}]
)
async def test_multipart_string_array_defaults(
    tags: list[str], encoding: dict[str, Any] | None
):
    app = FastAPI()

    @app.post("/items")
    async def receive(request: Request):
        form = await request.form()
        return {"tags": form.getlist("tags"), "label": form["label"]}

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
    if encoding is not None:
        media["encoding"] = {"tags": encoding}
    spec = {
        "openapi": "3.1.0",
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
            result = await client.call_tool("submit", {"tags": tags, "label": "keep"})
    # Explicit encodings are not implemented here; preserve their existing wire format.
    assert result.structured_content == {
        "tags": tags if tags and encoding is None else [str(tags)],
        "label": "keep",
    }


def test_only_empty_array_preserves_multipart_content_type() -> None:
    route = HTTPRoute(
        path="/items",
        method="POST",
        request_body=RequestBodyInfo(
            content_schema={"multipart/form-data": {"type": "object"}}
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


@pytest.mark.parametrize("kind", ["tuple", "bytes", "stream", "object", "nested"])
def test_multipart_arrays_preserve_other_parts(kind: str) -> None:
    values = {
        "tuple": ("sample.txt", b"payload", "text/plain"),
        "bytes": b"payload",
        "stream": io.BytesIO(b"payload"),
        "object": {"name": "alice"},
        "nested": [["a"]],
    }
    route = HTTPRoute(
        path="/items",
        method="POST",
        request_body=RequestBodyInfo(
            content_schema={"multipart/form-data": {"type": "object"}}
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
            else str(values[kind]).encode()
        )
        assert parts[2].get_payload(decode=True) == expected
        if kind == "tuple":
            assert parts[2].get_filename() == "sample.txt"
    finally:
        values["stream"].close()
