"""Built-in resources carry public component metadata onto read contents."""

import base64
import copy
import json
from pathlib import Path

import pytest

from fastmcp import Client, FastMCP
from fastmcp.resources import DirectoryResource, FileResource, HttpResource
from tests.utilities.httpx2_mock import HTTPXMock


@pytest.mark.parametrize("kind", ["file_text", "file_binary", "directory", "http"])
@pytest.mark.parametrize("metadata", ["public", "private_only", "absent"])
async def test_builtin_read_preserves_public_metadata(
    tmp_path: Path, httpx_mock: HTTPXMock, kind: str, metadata: str
) -> None:
    meta = None
    expected = None
    if metadata == "public":
        meta = {
            "ui": {"csp": {"connectDomains": ["https://example.invalid"]}},
            "fastmcp": {"custom": "keep", "_internal": {"visibility": "private"}},
        }
        expected = {
            "ui": {"csp": {"connectDomains": ["https://example.invalid"]}},
            "fastmcp": {"custom": "keep"},
        }
    elif metadata == "private_only":
        meta = {"fastmcp": {"_internal": {"visibility": "private"}}}
    original_meta = copy.deepcopy(meta)
    file = tmp_path / "page.html"
    file.write_text("<p>hello</p>", encoding="utf-8")
    uri = f"test://{kind}"
    if kind.startswith("file"):
        resource = FileResource(
            uri=uri,
            name=kind,
            path=file,
            mime_type="text/html",
            is_binary=kind == "file_binary",
            meta=meta,
        )
    elif kind == "directory":
        resource = DirectoryResource(uri=uri, name=kind, path=tmp_path, meta=meta)
    else:
        url = "https://example.com/page"
        httpx_mock.add_response(url=url, text="<p>hello</p>")
        resource = HttpResource(
            uri=uri, name=kind, url=url, mime_type="text/html", meta=meta
        )

    server = FastMCP("built-in resource metadata")
    server.add_resource(resource)
    async with Client(server) as client:
        listed = await client.list_resources()
        assert listed[0].meta == resource.get_meta()
        result = await client.read_resource_mcp(uri)

    assert len(result.contents) == 1
    content = result.contents[0]
    if kind == "directory":
        assert json.loads(content.text) == {"files": ["page.html"]}
        assert content.mime_type == "application/json"
    elif kind == "file_binary":
        assert base64.b64decode(content.blob) == b"<p>hello</p>"
        assert content.mime_type == "text/html"
    else:
        assert content.text == "<p>hello</p>"
        assert content.mime_type == "text/html"
    assert content.meta == expected
    assert resource.meta == original_meta
