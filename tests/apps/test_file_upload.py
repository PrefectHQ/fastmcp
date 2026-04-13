"""Tests for the FileUpload provider."""

import base64

import pytest

from fastmcp import FastMCP
from fastmcp.apps.file_upload import FileUpload, _b64_decoded_size
from fastmcp.server.providers.addressing import hashed_backend_name


class TestB64DecodedSize:
    """Unit tests for the _b64_decoded_size helper."""

    @pytest.mark.parametrize("size", [0, 1, 2, 3, 4, 50, 99, 100, 101, 1000])
    def test_matches_actual_decode(self, size: int):
        data = b"x" * size
        b64 = base64.b64encode(data).decode()
        assert _b64_decoded_size(b64) == size

    def test_empty_string(self):
        assert _b64_decoded_size("") == 0

    def test_no_padding(self):
        # 3 bytes → 4 base64 chars, no padding
        assert _b64_decoded_size(base64.b64encode(b"abc").decode()) == 3

    def test_one_pad(self):
        # 2 bytes → 4 base64 chars with 1 '='
        assert _b64_decoded_size(base64.b64encode(b"ab").decode()) == 2

    def test_two_pads(self):
        # 1 byte → 4 base64 chars with 2 '='
        assert _b64_decoded_size(base64.b64encode(b"a").decode()) == 1


def _make_file(
    name: str = "test.txt",
    content: str = "hello world",
    mime_type: str = "text/plain",
) -> dict:
    data = base64.b64encode(content.encode()).decode()
    return {
        "name": name,
        "size": len(content),
        "type": mime_type,
        "data": data,
    }


class TestFileUploadProvider:
    async def test_basic_store_and_list(self):
        server = FastMCP("test", providers=[FileUpload()])
        files = [_make_file()]

        result = await server.call_tool(
            hashed_backend_name("Files", "store_files"), {"files": files}
        )
        text = result.content[0].text  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
        assert "test.txt" in text

        result = await server.call_tool("list_files", {})
        text = result.content[0].text  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
        assert "test.txt" in text

    async def test_read_text_file(self):
        server = FastMCP("test", providers=[FileUpload()])
        files = [_make_file(content="DON'T PANIC")]

        await server.call_tool(
            hashed_backend_name("Files", "store_files"), {"files": files}
        )

        result = await server.call_tool("read_file", {"name": "test.txt"})
        text = result.content[0].text  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
        assert "DON'T PANIC" in text

    async def test_read_binary_file(self):
        server = FastMCP("test", providers=[FileUpload()])
        data = base64.b64encode(b"\x00\x01\x02\xff").decode()
        files = [{"name": "image.png", "size": 4, "type": "image/png", "data": data}]

        await server.call_tool(
            hashed_backend_name("Files", "store_files"), {"files": files}
        )

        result = await server.call_tool("read_file", {"name": "image.png"})
        text = result.content[0].text  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
        assert "content_base64" in text

    async def test_read_missing_file_raises(self):
        server = FastMCP("test", providers=[FileUpload()])

        with pytest.raises(Exception, match="not found"):
            await server.call_tool("read_file", {"name": "nope.txt"})

    async def test_multiple_files(self):
        server = FastMCP("test", providers=[FileUpload()])
        files = [
            _make_file("a.txt", "aaa"),
            _make_file("b.txt", "bbb"),
        ]

        await server.call_tool(
            hashed_backend_name("Files", "store_files"), {"files": files}
        )

        result = await server.call_tool("list_files", {})
        text = result.content[0].text  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
        assert "a.txt" in text
        assert "b.txt" in text

    async def test_overwrite_file(self):
        server = FastMCP("test", providers=[FileUpload()])

        await server.call_tool(
            hashed_backend_name("Files", "store_files"),
            {"files": [_make_file(content="version 1")]},
        )
        await server.call_tool(
            hashed_backend_name("Files", "store_files"),
            {"files": [_make_file(content="version 2")]},
        )

        result = await server.call_tool("read_file", {"name": "test.txt"})
        text = result.content[0].text  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
        assert "version 2" in text

    async def test_custom_name(self):
        server = FastMCP("test", providers=[FileUpload(name="Uploads")])

        tools = await server.list_tools()
        tool_names = [t.name for t in tools]
        assert "file_manager" in tool_names

        # The hash uses the app's actual name ("Uploads"), not the default.
        files = [_make_file()]
        result = await server.call_tool(
            hashed_backend_name("Uploads", "store_files"), {"files": files}
        )
        text = result.content[0].text  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
        assert "test.txt" in text

    async def test_ui_tool_visible_backend_hidden(self):
        server = FastMCP("test", providers=[FileUpload()])

        tools = await server.list_tools()
        tool_names = [t.name for t in tools]

        assert "file_manager" in tool_names
        assert "list_files" in tool_names
        assert "read_file" in tool_names
        assert "store_files" not in tool_names

    async def test_max_file_size_enforced_server_side(self):
        server = FastMCP("test", providers=[FileUpload(max_file_size=100)])
        big_file = _make_file(content="x" * 200)

        with pytest.raises(Exception, match="exceeds max size"):
            await server.call_tool(
                hashed_backend_name("Files", "store_files"), {"files": [big_file]}
            )

    async def test_max_file_size_checks_actual_data_not_reported_size(self):
        """Size limit should be enforced on actual base64 payload, not the
        client-reported ``size`` field which can be spoofed."""
        server = FastMCP("test", providers=[FileUpload(max_file_size=100)])

        big_content = "x" * 200
        big_b64 = base64.b64encode(big_content.encode()).decode()
        spoofed_file = {
            "name": "spoofed.bin",
            "size": 1,  # lies about size
            "type": "application/octet-stream",
            "data": big_b64,
        }

        with pytest.raises(Exception, match="exceeds max size"):
            await server.call_tool(
                hashed_backend_name("Files", "store_files"), {"files": [spoofed_file]}
            )


class TestFileUploadSubclass:
    async def test_custom_storage(self):
        """Subclassing lets users provide their own persistence."""
        stored: dict[str, dict] = {}

        class MemoryUpload(FileUpload):
            def on_store(self, files: list[dict], ctx) -> list[dict]:
                for f in files:
                    stored[f["name"]] = f
                return [
                    {
                        "name": f["name"],
                        "type": f["type"],
                        "size": f["size"],
                        "size_display": "?",
                        "uploaded_at": "now",
                    }
                    for f in files
                ]

            def on_list(self, ctx) -> list[dict]:
                return [
                    {
                        "name": f["name"],
                        "type": f["type"],
                        "size": f["size"],
                        "size_display": "?",
                        "uploaded_at": "now",
                    }
                    for f in stored.values()
                ]

            def on_read(self, name: str, ctx) -> dict:
                if name not in stored:
                    raise ValueError(f"Not found: {name}")
                f = stored[name]
                return {"name": f["name"], "content": "custom read"}

        server = FastMCP("test", providers=[MemoryUpload()])
        files = [_make_file()]

        await server.call_tool(
            hashed_backend_name("Files", "store_files"), {"files": files}
        )

        assert "test.txt" in stored

        result = await server.call_tool("read_file", {"name": "test.txt"})
        text = result.content[0].text  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
        assert "custom read" in text
