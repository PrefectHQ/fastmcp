"""Tests for the FileUpload provider."""

import base64

import pytest

from fastmcp import FastMCP
from fastmcp.apps.file_upload import FileUpload


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

        result = await server.call_tool("Files___store_files", {"files": files})
        text = result.content[0].text  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
        assert "test.txt" in text

        result = await server.call_tool("list_files", {})
        text = result.content[0].text  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
        assert "test.txt" in text

    async def test_read_text_file(self):
        server = FastMCP("test", providers=[FileUpload()])
        files = [_make_file(content="DON'T PANIC")]

        await server.call_tool("Files___store_files", {"files": files})

        result = await server.call_tool("read_file", {"name": "test.txt"})
        text = result.content[0].text  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
        assert "DON'T PANIC" in text

    async def test_read_binary_file(self):
        server = FastMCP("test", providers=[FileUpload()])
        data = base64.b64encode(b"\x00\x01\x02\xff").decode()
        files = [{"name": "image.png", "size": 4, "type": "image/png", "data": data}]

        await server.call_tool("Files___store_files", {"files": files})

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

        await server.call_tool("Files___store_files", {"files": files})

        result = await server.call_tool("list_files", {})
        text = result.content[0].text  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
        assert "a.txt" in text
        assert "b.txt" in text

    async def test_overwrite_file(self):
        server = FastMCP("test", providers=[FileUpload()])

        await server.call_tool(
            "Files___store_files",
            {"files": [_make_file(content="version 1")]},
        )
        await server.call_tool(
            "Files___store_files",
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

        # Routing uses the custom name
        files = [_make_file()]
        result = await server.call_tool("Uploads___store_files", {"files": files})
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


class TestFileUploadSubclass:
    async def test_custom_storage(self):
        """Subclassing lets users provide their own persistence."""
        stored: dict[str, dict] = {}

        class MemoryUpload(FileUpload):
            def on_store(self, files: list[dict]) -> list[dict]:
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

            def on_list(self) -> list[dict]:
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

            def on_read(self, name: str) -> dict:
                if name not in stored:
                    raise ValueError(f"Not found: {name}")
                f = stored[name]
                return {"name": f["name"], "content": "custom read"}

        server = FastMCP("test", providers=[MemoryUpload()])
        files = [_make_file()]

        await server.call_tool("Files___store_files", {"files": files})

        assert "test.txt" in stored

        result = await server.call_tool("read_file", {"name": "test.txt"})
        text = result.content[0].text  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
        assert "custom read" in text
