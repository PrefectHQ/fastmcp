"""Multipart object properties are sent as JSON, per the OAS Encoding Object."""

import json
from typing import Any

import httpx2
from fastapi import FastAPI, Request

from fastmcp import Client, FastMCP


def _spec(media: dict[str, Any]) -> dict[str, Any]:
    return {
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


def _app() -> FastAPI:
    app = FastAPI()

    @app.post("/items")
    async def receive(request: Request):
        form = await request.form()
        return {"fields": {key: form[key] for key in form}}

    return app


async def _submit(media: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    async with httpx2.AsyncClient(
        base_url="http://test", transport=httpx2.ASGITransport(app=_app())
    ) as http:
        server = FastMCP.from_openapi(openapi_spec=_spec(media), client=http)
        async with Client(server) as client:
            result = await client.call_tool("submit", arguments)
    assert result.structured_content is not None
    return result.structured_content["fields"]


OBJECT_SCHEMA = {
    "type": "object",
    "properties": {
        "meta": {"type": "object"},
        "label": {"type": "string"},
    },
    "required": ["meta"],
}


async def test_object_property_is_sent_as_json():
    fields = await _submit(
        {"schema": OBJECT_SCHEMA},
        {"meta": {"title": "a", "note": None}, "label": "keep"},
    )

    assert json.loads(fields["meta"]) == {"title": "a", "note": None}
    assert fields["label"] == "keep"


async def test_nested_object_property_is_sent_as_json():
    fields = await _submit(
        {"schema": OBJECT_SCHEMA},
        {"meta": {"tags": ["a", "b"], "inner": {"ok": True}}},
    )

    assert json.loads(fields["meta"]) == {"tags": ["a", "b"], "inner": {"ok": True}}


async def test_referenced_object_property_is_sent_as_json():
    media = {
        "schema": {
            "type": "object",
            "properties": {"meta": {"$ref": "#/$defs/Meta"}},
            "required": ["meta"],
            "$defs": {
                "Meta": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                }
            },
        }
    }

    fields = await _submit(media, {"meta": {"title": "a"}})

    assert json.loads(fields["meta"]) == {"title": "a"}


async def test_scalar_properties_are_not_json_encoded():
    media = {
        "schema": {
            "type": "object",
            "properties": {
                "label": {"type": "string"},
                "count": {"type": "integer"},
                "flag": {"type": "boolean"},
            },
        }
    }

    fields = await _submit(media, {"label": "keep", "count": 3, "flag": True})

    assert fields == {"label": "keep", "count": "3", "flag": "true"}
