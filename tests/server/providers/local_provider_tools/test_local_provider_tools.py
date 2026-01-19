"""Core tool return types and serialization tests."""

import base64
import datetime
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from mcp.types import (
    AudioContent,
    EmbeddedResource,
    ImageContent,
    TextContent,
)
from pydantic import BaseModel
from typing_extensions import TypedDict

from fastmcp import FastMCP
from fastmcp.utilities.types import Audio, File, Image


def _normalize_anyof_order(schema):
    """Normalize the order of items in anyOf arrays for consistent comparison."""
    if isinstance(schema, dict):
        if "anyOf" in schema:
            schema = schema.copy()
            schema["anyOf"] = sorted(schema["anyOf"], key=str)
        return {k: _normalize_anyof_order(v) for k, v in schema.items()}
    elif isinstance(schema, list):
        return [_normalize_anyof_order(item) for item in schema]
    return schema


class PersonTypedDict(TypedDict):
    name: str
    age: int


class PersonModel(BaseModel):
    name: str
    age: int


@dataclass
class PersonDataclass:
    name: str
    age: int


class TestToolReturnTypes:
    async def test_string(self):
        mcp = FastMCP()

        @mcp.tool
        def string_tool() -> str:
            return "Hello, world!"

        result = await mcp.call_tool("string_tool", {})
        assert result.structured_content == {"result": "Hello, world!"}

    async def test_bytes(self, tmp_path: Path):
        mcp = FastMCP()

        @mcp.tool
        def bytes_tool() -> bytes:
            return b"Hello, world!"

        result = await mcp.call_tool("bytes_tool", {})
        assert result.structured_content == {"result": "Hello, world!"}

    async def test_uuid(self):
        mcp = FastMCP()

        test_uuid = uuid.uuid4()

        @mcp.tool
        def uuid_tool() -> uuid.UUID:
            return test_uuid

        result = await mcp.call_tool("uuid_tool", {})
        assert result.structured_content == {"result": str(test_uuid)}

    async def test_path(self):
        mcp = FastMCP()

        test_path = Path("/tmp/test.txt")

        @mcp.tool
        def path_tool() -> Path:
            return test_path

        result = await mcp.call_tool("path_tool", {})
        assert result.structured_content == {"result": str(test_path)}

    async def test_datetime(self):
        mcp = FastMCP()

        dt = datetime.datetime(2025, 4, 25, 1, 2, 3)

        @mcp.tool
        def datetime_tool() -> datetime.datetime:
            return dt

        result = await mcp.call_tool("datetime_tool", {})
        assert result.structured_content == {"result": dt.isoformat()}

    async def test_image(self, tmp_path: Path):
        mcp = FastMCP()

        @mcp.tool
        def image_tool(path: str) -> Image:
            return Image(path)

        image_path = tmp_path / "test.png"
        image_path.write_bytes(b"fake png data")

        result = await mcp.call_tool("image_tool", {"path": str(image_path)})
        assert result.structured_content is None
        assert isinstance(result.content, list)
        content = result.content[0]
        assert isinstance(content, ImageContent)
        assert content.type == "image"
        assert content.mimeType == "image/png"
        decoded = base64.b64decode(content.data)
        assert decoded == b"fake png data"

    async def test_audio(self, tmp_path: Path):
        mcp = FastMCP()

        @mcp.tool
        def audio_tool(path: str) -> Audio:
            return Audio(path)

        audio_path = tmp_path / "test.wav"
        audio_path.write_bytes(b"fake wav data")

        result = await mcp.call_tool("audio_tool", {"path": str(audio_path)})
        assert isinstance(result.content, list)
        content = result.content[0]
        assert isinstance(content, AudioContent)
        assert content.type == "audio"
        assert content.mimeType == "audio/wav"
        decoded = base64.b64decode(content.data)
        assert decoded == b"fake wav data"

    async def test_file(self, tmp_path: Path):
        mcp = FastMCP()

        @mcp.tool
        def file_tool(path: str) -> File:
            return File(path)

        file_path = tmp_path / "test.bin"
        file_path.write_bytes(b"test file data")

        result = await mcp.call_tool("file_tool", {"path": str(file_path)})
        assert isinstance(result.content, list)
        content = result.content[0]
        assert isinstance(content, EmbeddedResource)
        assert content.type == "resource"
        resource = content.resource
        assert resource.mimeType == "application/octet-stream"
        assert hasattr(resource, "blob")
        blob_data = getattr(resource, "blob")
        decoded = base64.b64decode(blob_data)
        assert decoded == b"test file data"
        assert str(resource.uri) == file_path.resolve().as_uri()

    async def test_tool_mixed_content(self, tool_server: FastMCP):
        result = await tool_server.call_tool("mixed_content_tool", {})
        assert isinstance(result.content, list)
        assert len(result.content) == 3
        content1 = result.content[0]
        content2 = result.content[1]
        content3 = result.content[2]
        assert isinstance(content1, TextContent)
        assert content1.text == "Hello"
        assert isinstance(content2, ImageContent)
        assert content2.mimeType == "application/octet-stream"
        assert content2.data == "abc"
        assert isinstance(content3, EmbeddedResource)
        assert content3.type == "resource"
        resource = content3.resource
        assert resource.mimeType == "application/octet-stream"
        assert hasattr(resource, "blob")
        blob_data = getattr(resource, "blob")
        decoded = base64.b64decode(blob_data)
        assert decoded == b"abc"

    async def test_tool_mixed_list_with_image(
        self, tool_server: FastMCP, tmp_path: Path
    ):
        """Test that lists containing Image objects and other types are handled
        correctly. Items now preserve their original order."""
        image_path = tmp_path / "test.png"
        image_path.write_bytes(b"test image data")

        result = await tool_server.call_tool(
            "mixed_list_fn", {"image_path": str(image_path)}
        )
        assert isinstance(result.content, list)
        assert len(result.content) == 4
        content1 = result.content[0]
        assert isinstance(content1, TextContent)
        assert content1.text == "text message"
        content2 = result.content[1]
        assert isinstance(content2, ImageContent)
        assert content2.mimeType == "image/png"
        assert base64.b64decode(content2.data) == b"test image data"
        content3 = result.content[2]
        assert isinstance(content3, TextContent)
        assert json.loads(content3.text) == {"key": "value"}
        content4 = result.content[3]
        assert isinstance(content4, TextContent)
        assert content4.text == "direct content"

    async def test_tool_mixed_list_with_audio(
        self, tool_server: FastMCP, tmp_path: Path
    ):
        """Test that lists containing Audio objects and other types are handled
        correctly. Items now preserve their original order."""
        audio_path = tmp_path / "test.wav"
        audio_path.write_bytes(b"test audio data")

        result = await tool_server.call_tool(
            "mixed_audio_list_fn", {"audio_path": str(audio_path)}
        )
        assert isinstance(result.content, list)
        assert len(result.content) == 4
        content1 = result.content[0]
        assert isinstance(content1, TextContent)
        assert content1.text == "text message"
        content2 = result.content[1]
        assert isinstance(content2, AudioContent)
        assert content2.mimeType == "audio/wav"
        assert base64.b64decode(content2.data) == b"test audio data"
        content3 = result.content[2]
        assert isinstance(content3, TextContent)
        assert json.loads(content3.text) == {"key": "value"}
        content4 = result.content[3]
        assert isinstance(content4, TextContent)
        assert content4.text == "direct content"

    async def test_tool_mixed_list_with_file(
        self, tool_server: FastMCP, tmp_path: Path
    ):
        """Test that lists containing File objects and other types are handled
        correctly. Items now preserve their original order."""
        file_path = tmp_path / "test.bin"
        file_path.write_bytes(b"test file data")

        result = await tool_server.call_tool(
            "mixed_file_list_fn", {"file_path": str(file_path)}
        )
        assert isinstance(result.content, list)
        assert len(result.content) == 4
        content1 = result.content[0]
        assert isinstance(content1, TextContent)
        assert content1.text == "text message"
        content2 = result.content[1]
        assert isinstance(content2, EmbeddedResource)
        assert content2.type == "resource"
        resource = content2.resource
        assert resource.mimeType == "application/octet-stream"
        assert hasattr(resource, "blob")
        blob_data = getattr(resource, "blob")
        assert base64.b64decode(blob_data) == b"test file data"
        content3 = result.content[2]
        assert isinstance(content3, TextContent)
        assert json.loads(content3.text) == {"key": "value"}
        content4 = result.content[3]
        assert isinstance(content4, TextContent)
        assert content4.text == "direct content"
