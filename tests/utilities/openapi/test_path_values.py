"""Path values remain data within an operation's declared route."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import httpx2
import pytest
from jsonschema_path import SchemaPath

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.utilities.openapi.director import RequestDirector


@pytest.mark.parametrize(
    "value",
    [
        ".",
        "..",
        "../other",
        "other/../x",
        r"other\..\x",
        "%2e%2e",
        "%252e%252e",
        "..%2fother",
        "other%5c..%5cx",
    ],
)
def test_path_value_rejects_dot_segments(value: str):
    with pytest.raises(ValueError, match="dot segments"):
        RequestDirector(SchemaPath.from_dict({}))._build_url(
            "/items/{id}/record", {"id": value}, "https://api.example.com"
        )


@pytest.mark.parametrize(
    "value", ["item.1", ".hidden", "..name", "a/b", "a\\b", "100%", 42]
)
def test_path_value_preserves_ordinary_data(value: str | int):
    url = RequestDirector(SchemaPath.from_dict({}))._build_url(
        "/items/{id}/record", {"id": value}, "https://api.example.com"
    )
    assert url.startswith("https://api.example.com/items/")
    assert url.endswith("/record")


async def test_path_value_is_checked_before_backend_request(tmp_path: Path):
    (tmp_path / "api" / "users").mkdir(parents=True)
    (tmp_path / "api" / "admin.txt").write_text("private-record")
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    backend = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    worker = Thread(target=backend.serve_forever, daemon=True)
    worker.start()
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "API", "version": "1"},
        "paths": {
            "/api/users/{id}/admin.txt": {
                "get": {
                    "operationId": "get_record",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    try:
        async with httpx2.AsyncClient(
            base_url=f"http://127.0.0.1:{backend.server_port}"
        ) as http:
            server = FastMCP.from_openapi(spec, client=http)
            async with Client(server) as client:
                with pytest.raises(ToolError, match="dot segments"):
                    await client.call_tool("get_record", {"id": ".."})
    finally:
        backend.shutdown()
        backend.server_close()
        worker.join()


def test_path_value_rejects_excessive_encoding_layers():
    value = "%" + "25" * 40 + "2e"
    with pytest.raises(ValueError, match="too many encoding layers"):
        RequestDirector(SchemaPath.from_dict({}))._build_url(
            "/items/{id}", {"id": value}, "https://api.example.com"
        )


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.example.com",
        "https://api.example.com/",
        "https://api.example.com/v1",
        "https://api.example.com/v1/",
    ],
)
@pytest.mark.parametrize(
    "template,params,path",
    [
        ("/things:batchGet", {}, "/things:batchGet"),
        ("/{job}:cancel", {"job": "abc123"}, "/abc123:cancel"),
        ("/jobs/{job}:cancel", {"job": "abc123"}, "/jobs/abc123:cancel"),
        ("/v1/things:search", {}, "/v1/things:search"),
        ("things", {}, "/things"),
    ],
)
def test_path_keeps_leading_segment_with_colon(
    base_url: str, template: str, params: dict[str, str], path: str
):
    url = RequestDirector(SchemaPath.from_dict({}))._build_url(
        template, params, base_url
    )
    assert url == base_url.rstrip("/") + path


async def test_colon_path_segment_reaches_http_endpoint():
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "API", "version": "1"},
        "paths": {
            "/{job}:cancel": {
                "post": {
                    "operationId": "cancel_job",
                    "parameters": [
                        {
                            "name": "job",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    requests: list[httpx2.Request] = []

    def capture(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"ok": True})

    async with httpx2.AsyncClient(
        base_url="https://api.example.com/v1",
        transport=httpx2.MockTransport(capture),
    ) as http:
        server = FastMCP.from_openapi(openapi_spec=spec, client=http)
        async with Client(server) as client:
            await client.call_tool("cancel_job", {"job": "abc123"})

    assert [str(request.url) for request in requests] == [
        "https://api.example.com/v1/abc123:cancel"
    ]
