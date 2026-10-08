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


def test_path_template_requires_every_parameter_value():
    director = RequestDirector(SchemaPath.from_dict({}))
    with pytest.raises(
        ValueError, match=r"Missing required path parameters: \{'org', 'repo'\}"
    ):
        director._build_url("/orgs/{org}/repos/{repo}/{org}", {}, "https://x.test")
    with pytest.raises(
        ValueError, match=r"Missing required path parameters: \{'repo'\}"
    ):
        director._build_url(
            "/orgs/{org}/repos/{repo}", {"org": "acme"}, "https://x.test"
        )


@pytest.mark.parametrize("value,segment", [("", ""), (0, "0")])
def test_path_template_accepts_falsy_values(value: str | int, segment: str):
    url = RequestDirector(SchemaPath.from_dict({}))._build_url(
        "/items/{id}", {"id": value}, "https://x.test"
    )
    assert url == f"https://x.test/items/{segment}"


@pytest.mark.parametrize(
    "arguments",
    [{}, {"user_id": None}, {"user_id__path": None, "user_id__query": "x"}],
)
async def test_missing_path_parameter_is_not_sent_as_placeholder(
    arguments: dict[str, str | None],
):
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "API", "version": "1"},
        "paths": {
            "/users/{user_id}": {
                "delete": {
                    "operationId": "delete_user",
                    "parameters": [
                        {
                            "name": "user_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        },
                        *(
                            [
                                {
                                    "name": "user_id",
                                    "in": "query",
                                    "schema": {"type": "string"},
                                }
                            ]
                            if "user_id__query" in arguments
                            else []
                        ),
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
        base_url="https://api.example.com", transport=httpx2.MockTransport(capture)
    ) as http:
        server = FastMCP.from_openapi(openapi_spec=spec, client=http)
        async with Client(server) as client:
            with pytest.raises(
                ToolError, match=r"Missing required path parameters: \{'user_id'\}"
            ):
                await client.call_tool("delete_user", arguments)

    assert requests == []
