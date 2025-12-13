import base64
import datetime
import functools
import json
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal

import pytest
from inline_snapshot import snapshot
from mcp import McpError
from mcp.types import (
    AudioContent,
    BlobResourceContents,
    EmbeddedResource,
    ImageContent,
    TextContent,
    TextResourceContents,
)
from pydantic import AnyUrl, BaseModel, Field, TypeAdapter
from typing_extensions import TypedDict

from fastmcp import Client, Context, FastMCP
from fastmcp.client.client import CallToolResult
from fastmcp.client.transports import FastMCPTransport
from fastmcp.exceptions import ToolError
from fastmcp.prompts.prompt import Prompt, PromptMessage
from fastmcp.resources import FileResource, ResourceTemplate
from fastmcp.resources.resource import FunctionResource
from fastmcp.tools.tool import Tool, ToolResult
from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.tests import temporary_settings
from fastmcp.utilities.types import Audio, File, Image


def _normalize_anyof_order(schema):
    """Normalize the order of items in anyOf arrays for consistent comparison."""
    if isinstance(schema, dict):
        if "anyOf" in schema:
            # Sort anyOf items by their string representation for consistent ordering
            schema = schema.copy()
            schema["anyOf"] = sorted(schema["anyOf"], key=str)
        # Recursively normalize nested objects
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


@pytest.fixture
def tool_server():
    mcp = FastMCP()

    @mcp.tool
    def add(x: int, y: int) -> int:
        return x + y

    @mcp.tool
    def list_tool() -> list[str | int]:
        return ["x", 2]

    @mcp.tool
    def error_tool() -> None:
        raise ValueError("Test error")

    @mcp.tool
    def image_tool(path: str) -> Image:
        return Image(path)

    @mcp.tool
    def audio_tool(path: str) -> Audio:
        return Audio(path)

    @mcp.tool
    def file_tool(path: str) -> File:
        return File(path)

    @mcp.tool
    def mixed_content_tool() -> list[TextContent | ImageContent | EmbeddedResource]:
        return [
            TextContent(type="text", text="Hello"),
            ImageContent(type="image", data="abc", mimeType="application/octet-stream"),
            EmbeddedResource(
                type="resource",
                resource=BlobResourceContents(
                    blob=base64.b64encode(b"abc").decode(),
                    mimeType="application/octet-stream",
                    uri=AnyUrl("file:///test.bin"),
                ),
            ),
        ]

    @mcp.tool(output_schema=None)
    def mixed_list_fn(image_path: str) -> list:
        return [
            "text message",
            Image(image_path),
            {"key": "value"},
            TextContent(type="text", text="direct content"),
        ]

    @mcp.tool(output_schema=None)
    def mixed_audio_list_fn(audio_path: str) -> list:
        return [
            "text message",
            Audio(audio_path),
            {"key": "value"},
            TextContent(type="text", text="direct content"),
        ]

    @mcp.tool(output_schema=None)
    def mixed_file_list_fn(file_path: str) -> list:
        return [
            "text message",
            File(file_path),
            {"key": "value"},
            TextContent(type="text", text="direct content"),
        ]

    @mcp.tool
    def file_text_tool() -> File:
        # Return a File with text data and text/plain format
        return File(data=b"hello world", format="plain")

    return mcp


class TestTools:
    async def test_add_tool_exists(self, tool_server: FastMCP):
        async with Client(tool_server) as client:
            tools = await client.list_tools()
            assert "add" in [t.name for t in tools]

    async def test_list_tools(self, tool_server: FastMCP):
        async with Client(tool_server) as client:
            assert len(await client.list_tools()) == 11

    async def test_call_tool_mcp(self, tool_server: FastMCP):
        async with Client(tool_server) as client:
            result = await client.call_tool_mcp("add", {"x": 1, "y": 2})
            assert result.content[0].text == "3"  # type: ignore[attr-defined]
            assert result.structuredContent == {"result": 3}

    async def test_call_tool(self, tool_server: FastMCP):
        async with Client(tool_server) as client:
            result = await client.call_tool("add", {"x": 1, "y": 2})
            assert result.content[0].text == "3"  # type: ignore[attr-defined]
            assert result.structured_content == {"result": 3}
            assert result.data == 3

    async def test_call_tool_error(self, tool_server: FastMCP):
        async with Client(tool_server) as client:
            with pytest.raises(Exception):
                await client.call_tool("error_tool", {})

    async def test_call_tool_error_as_client_raw(self):
        """Test raising and catching errors from a tool."""
        mcp = FastMCP()
        client = Client(transport=FastMCPTransport(mcp))

        @mcp.tool
        def error_tool():
            raise ValueError("Test error")

        async with client:
            with pytest.raises(Exception) as excinfo:
                await client.call_tool("error_tool", {})
            assert "Error calling tool 'error_tool'" in str(excinfo.value)

    async def test_tool_returns_list(self, tool_server: FastMCP):
        async with Client(tool_server) as client:
            result = await client.call_tool("list_tool", {})
            # Adjacent non-MCP list items are combined into single content block
            assert len(result.content) == 1
            assert result.content[0].text == '["x",2]'  # type: ignore[attr-defined]
            assert result.data == ["x", 2]

    async def test_file_text_tool(self, tool_server: FastMCP):
        async with Client(tool_server) as client:
            result = await client.call_tool("file_text_tool", {})
            assert len(result.content) == 1
            embedded = result.content[0]
            assert isinstance(embedded, EmbeddedResource)
            resource = embedded.resource
            assert isinstance(resource, TextResourceContents)
            assert resource.mimeType == "text/plain"
            assert resource.text == "hello world"


class TestToolTags:
    def create_server(self, include_tags=None, exclude_tags=None):
        mcp = FastMCP(include_tags=include_tags, exclude_tags=exclude_tags)

        @mcp.tool(tags={"a", "b"})
        def tool_1() -> int:
            return 1

        @mcp.tool(tags={"b", "c"})
        def tool_2() -> int:
            return 2

        return mcp

    async def test_include_tags_all_tools(self):
        mcp = self.create_server(include_tags={"a", "b"})

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert {t.name for t in tools} == {"tool_1", "tool_2"}

    async def test_include_tags_some_tools(self):
        mcp = self.create_server(include_tags={"a", "z"})

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert {t.name for t in tools} == {"tool_1"}

    async def test_exclude_tags_all_tools(self):
        mcp = self.create_server(exclude_tags={"a", "b"})

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert {t.name for t in tools} == set()

    async def test_exclude_tags_some_tools(self):
        mcp = self.create_server(exclude_tags={"a", "z"})

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert {t.name for t in tools} == {"tool_2"}

    async def test_exclude_precedence(self):
        mcp = self.create_server(exclude_tags={"a"}, include_tags={"b"})

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert {t.name for t in tools} == {"tool_2"}

    async def test_call_included_tool(self):
        mcp = self.create_server(include_tags={"a"})

        async with Client(mcp) as client:
            result_1 = await client.call_tool("tool_1", {})
            assert result_1.data == 1

            with pytest.raises(ToolError, match="Unknown tool"):
                await client.call_tool("tool_2", {})

    async def test_call_excluded_tool(self):
        mcp = self.create_server(exclude_tags={"a"})

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="Unknown tool"):
                await client.call_tool("tool_1", {})

            result_2 = await client.call_tool("tool_2", {})
            assert result_2.data == 2


class TestToolReturnTypes:
    async def test_string(self):
        mcp = FastMCP()

        @mcp.tool
        def string_tool() -> str:
            return "Hello, world!"

        async with Client(mcp) as client:
            result = await client.call_tool("string_tool", {})
            assert result.data == "Hello, world!"

    async def test_bytes(self, tmp_path: Path):
        mcp = FastMCP()

        @mcp.tool
        def bytes_tool() -> bytes:
            return b"Hello, world!"

        async with Client(mcp) as client:
            result = await client.call_tool("bytes_tool", {})
            assert result.data == "Hello, world!"

    async def test_uuid(self):
        mcp = FastMCP()

        test_uuid = uuid.uuid4()

        @mcp.tool
        def uuid_tool() -> uuid.UUID:
            return test_uuid

        async with Client(mcp) as client:
            result = await client.call_tool("uuid_tool", {})
            assert result.data == str(test_uuid)

    async def test_path(self):
        mcp = FastMCP()

        test_path = Path("/tmp/test.txt")

        @mcp.tool
        def path_tool() -> Path:
            return test_path

        async with Client(mcp) as client:
            result = await client.call_tool("path_tool", {})
            assert result.data == str(test_path)

    async def test_datetime(self):
        mcp = FastMCP()

        dt = datetime.datetime(2025, 4, 25, 1, 2, 3)

        @mcp.tool
        def datetime_tool() -> datetime.datetime:
            return dt

        async with Client(mcp) as client:
            result = await client.call_tool("datetime_tool", {})
            assert result.data == dt

    async def test_image(self, tmp_path: Path):
        mcp = FastMCP()

        @mcp.tool
        def image_tool(path: str) -> Image:
            return Image(path)

        # Create a test image
        image_path = tmp_path / "test.png"
        image_path.write_bytes(b"fake png data")

        async with Client(mcp) as client:
            result = await client.call_tool("image_tool", {"path": str(image_path)})
            assert result.structured_content is None
            content = result.content[0]
            assert isinstance(content, ImageContent)
            assert content.type == "image"
            assert content.mimeType == "image/png"
            # Verify base64 encoding
            decoded = base64.b64decode(content.data)
            assert decoded == b"fake png data"

    async def test_audio(self, tmp_path: Path):
        mcp = FastMCP()

        @mcp.tool
        def audio_tool(path: str) -> Audio:
            return Audio(path)

        # Create a test audio file
        audio_path = tmp_path / "test.wav"
        audio_path.write_bytes(b"fake wav data")

        async with Client(mcp) as client:
            result = await client.call_tool("audio_tool", {"path": str(audio_path)})
            content = result.content[0]
            assert isinstance(content, AudioContent)
            assert content.type == "audio"
            assert content.mimeType == "audio/wav"
            # Verify base64 encoding
            decoded = base64.b64decode(content.data)
            assert decoded == b"fake wav data"

    async def test_file(self, tmp_path: Path):
        mcp = FastMCP()

        @mcp.tool
        def file_tool(path: str) -> File:
            return File(path)

        # Create a test file
        file_path = tmp_path / "test.bin"
        file_path.write_bytes(b"test file data")

        async with Client(mcp) as client:
            result = await client.call_tool("file_tool", {"path": str(file_path)})
            content = result.content[0]
            assert isinstance(content, EmbeddedResource)
            assert content.type == "resource"
            resource = content.resource
            assert resource.mimeType == "application/octet-stream"
            # Verify base64 encoding
            assert hasattr(resource, "blob")
            blob_data = getattr(resource, "blob")
            decoded = base64.b64decode(blob_data)
            assert decoded == b"test file data"
            # Verify URI points to the file
            assert str(resource.uri) == file_path.resolve().as_uri()

    async def test_tool_mixed_content(self, tool_server: FastMCP):
        async with Client(tool_server) as client:
            result = await client.call_tool("mixed_content_tool", {})
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
        # Create a test image
        image_path = tmp_path / "test.png"
        image_path.write_bytes(b"test image data")

        async with Client(tool_server) as client:
            result = await client.call_tool(
                "mixed_list_fn", {"image_path": str(image_path)}
            )
            assert len(result.content) == 4  # Now each item is separate
            # Check text message (first item)
            content1 = result.content[0]
            assert isinstance(content1, TextContent)
            assert content1.text == "text message"
            # Check image conversion (second item)
            content2 = result.content[1]
            assert isinstance(content2, ImageContent)
            assert content2.mimeType == "image/png"
            assert base64.b64decode(content2.data) == b"test image data"
            # Check dict content (third item)
            content3 = result.content[2]
            assert isinstance(content3, TextContent)
            assert json.loads(content3.text) == {"key": "value"}
            # Check direct TextContent (fourth item)
            content4 = result.content[3]
            assert isinstance(content4, TextContent)
            assert content4.text == "direct content"

    async def test_tool_mixed_list_with_audio(
        self, tool_server: FastMCP, tmp_path: Path
    ):
        """Test that lists containing Audio objects and other types are handled
        correctly. Items now preserve their original order."""
        # Create a test audio file
        audio_path = tmp_path / "test.wav"
        audio_path.write_bytes(b"test audio data")

        async with Client(tool_server) as client:
            result = await client.call_tool(
                "mixed_audio_list_fn", {"audio_path": str(audio_path)}
            )
            assert len(result.content) == 4  # Now each item is separate
            # Check text message (first item)
            content1 = result.content[0]
            assert isinstance(content1, TextContent)
            assert content1.text == "text message"
            # Check audio conversion (second item)
            content2 = result.content[1]
            assert isinstance(content2, AudioContent)
            assert content2.mimeType == "audio/wav"
            assert base64.b64decode(content2.data) == b"test audio data"
            # Check dict content (third item)
            content3 = result.content[2]
            assert isinstance(content3, TextContent)
            assert json.loads(content3.text) == {"key": "value"}
            # Check direct TextContent (fourth item)
            content4 = result.content[3]
            assert isinstance(content4, TextContent)
            assert content4.text == "direct content"

    async def test_tool_mixed_list_with_file(
        self, tool_server: FastMCP, tmp_path: Path
    ):
        """Test that lists containing File objects and other types are handled
        correctly. Items now preserve their original order."""
        # Create a test file
        file_path = tmp_path / "test.bin"
        file_path.write_bytes(b"test file data")

        async with Client(tool_server) as client:
            result = await client.call_tool(
                "mixed_file_list_fn", {"file_path": str(file_path)}
            )
            assert len(result.content) == 4  # Now each item is separate
            # Check text message (first item)
            content1 = result.content[0]
            assert isinstance(content1, TextContent)
            assert content1.text == "text message"
            # Check file conversion (second item)
            content2 = result.content[1]
            assert isinstance(content2, EmbeddedResource)
            assert content2.type == "resource"
            resource = content2.resource
            assert resource.mimeType == "application/octet-stream"
            assert hasattr(resource, "blob")
            blob_data = getattr(resource, "blob")
            assert base64.b64decode(blob_data) == b"test file data"
            # Check dict content (third item)
            content3 = result.content[2]
            assert isinstance(content3, TextContent)
            assert json.loads(content3.text) == {"key": "value"}
            # Check direct TextContent (fourth item)
            content4 = result.content[3]
            assert isinstance(content4, TextContent)
            assert content4.text == "direct content"


class TestToolParameters:
    async def test_parameter_descriptions_with_field_annotations(self):
        mcp = FastMCP("Test Server")

        @mcp.tool
        def greet(
            name: Annotated[str, Field(description="The name to greet")],
            title: Annotated[str, Field(description="Optional title", default="")],
        ) -> str:
            """A greeting tool"""
            return f"Hello {title} {name}"

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert len(tools) == 1
            tool = tools[0]

            # Check that parameter descriptions are present in the schema
            properties = tool.inputSchema["properties"]
            assert "name" in properties
            assert properties["name"]["description"] == "The name to greet"
            assert "title" in properties
            assert properties["title"]["description"] == "Optional title"
            assert properties["title"]["default"] == ""
            assert tool.inputSchema["required"] == ["name"]

    async def test_parameter_descriptions_with_field_defaults(self):
        mcp = FastMCP("Test Server")

        @mcp.tool
        def greet(
            name: str = Field(description="The name to greet"),
            title: str = Field(description="Optional title", default=""),
        ) -> str:
            """A greeting tool"""
            return f"Hello {title} {name}"

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert len(tools) == 1
            tool = tools[0]

            # Check that parameter descriptions are present in the schema
            properties = tool.inputSchema["properties"]
            assert "name" in properties
            assert properties["name"]["description"] == "The name to greet"
            assert "title" in properties
            assert properties["title"]["description"] == "Optional title"
            assert properties["title"]["default"] == ""
            assert tool.inputSchema["required"] == ["name"]

    async def test_tool_with_bytes_input(self):
        mcp = FastMCP()

        @mcp.tool
        def process_image(image: bytes) -> Image:
            return Image(data=image)

        async with Client(mcp) as client:
            result = await client.call_tool(
                "process_image", {"image": b"fake png data"}
            )
            assert result.structured_content is None
            assert isinstance(result.content[0], ImageContent)
            assert result.content[0].mimeType == "image/png"
            assert result.content[0].data == base64.b64encode(b"fake png data").decode()

    async def test_tool_with_invalid_input(self):
        mcp = FastMCP()

        @mcp.tool
        def my_tool(x: int) -> int:
            return x + 1

        async with Client(mcp) as client:
            with pytest.raises(
                ToolError,
                match="Input should be a valid integer",
            ):
                await client.call_tool("my_tool", {"x": "not an int"})

    async def test_tool_int_coercion(self):
        """Test that string ints are coerced by default."""
        mcp = FastMCP()

        @mcp.tool
        def add_one(x: int) -> int:
            return x + 1

        async with Client(mcp) as client:
            # String input should be coerced with default settings
            result = await client.call_tool("add_one", {"x": "42"})
            assert result.data == 43

    async def test_tool_bool_coercion(self):
        """Test that string bools are coerced by default."""
        mcp = FastMCP()

        @mcp.tool
        def toggle(flag: bool) -> bool:
            return not flag

        async with Client(mcp) as client:
            # String input should be coerced with default settings
            result = await client.call_tool("toggle", {"flag": "true"})
            assert result.data is False

            result = await client.call_tool("toggle", {"flag": "false"})
            assert result.data is True

    async def test_annotated_field_validation(self):
        mcp = FastMCP()

        @mcp.tool
        def analyze(x: Annotated[int, Field(ge=1)]) -> None:
            pass

        async with Client(mcp) as client:
            with pytest.raises(
                ToolError,
                match="Input should be greater than or equal to 1",
            ):
                await client.call_tool("analyze", {"x": 0})

    async def test_default_field_validation(self):
        mcp = FastMCP()

        @mcp.tool
        def analyze(x: int = Field(ge=1)) -> None:
            pass

        async with Client(mcp) as client:
            with pytest.raises(
                ToolError,
                match="Input should be greater than or equal to 1",
            ):
                await client.call_tool("analyze", {"x": 0})

    async def test_default_field_is_still_required_if_no_default_specified(self):
        mcp = FastMCP()

        @mcp.tool
        def analyze(x: int = Field()) -> None:
            pass

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="Missing required argument"):
                await client.call_tool("analyze", {})

    async def test_literal_type_validation_error(self):
        mcp = FastMCP()

        @mcp.tool
        def analyze(x: Literal["a", "b"]) -> None:
            pass

        async with Client(mcp) as client:
            with pytest.raises(
                ToolError,
                match="Input should be 'a' or 'b'",
            ):
                await client.call_tool("analyze", {"x": "c"})

    async def test_literal_type_validation_success(self):
        mcp = FastMCP()

        @mcp.tool
        def analyze(x: Literal["a", "b"]) -> str:
            return x

        async with Client(mcp) as client:
            result = await client.call_tool("analyze", {"x": "a"})
            assert result.data == "a"

    async def test_enum_type_validation_error(self):
        mcp = FastMCP()

        class MyEnum(Enum):
            RED = "red"
            GREEN = "green"
            BLUE = "blue"

        @mcp.tool
        def analyze(x: MyEnum) -> str:
            return x.value

        async with Client(mcp) as client:
            with pytest.raises(
                ToolError,
                match="Input should be 'red', 'green' or 'blue'",
            ):
                await client.call_tool("analyze", {"x": "some-color"})

    async def test_enum_type_validation_success(self):
        mcp = FastMCP()

        class MyEnum(Enum):
            RED = "red"
            GREEN = "green"
            BLUE = "blue"

        @mcp.tool
        def analyze(x: MyEnum) -> str:
            return x.value

        async with Client(mcp) as client:
            result = await client.call_tool("analyze", {"x": "red"})
            assert result.data == "red"

    async def test_union_type_validation(self):
        mcp = FastMCP()

        @mcp.tool
        def analyze(x: int | float) -> str:
            return str(x)

        async with Client(mcp) as client:
            result = await client.call_tool("analyze", {"x": 1})
            assert result.data == "1"

            result = await client.call_tool("analyze", {"x": 1.0})
            assert result.data == "1.0"

            with pytest.raises(
                ToolError,
                match="Input should be a valid",
            ):
                await client.call_tool("analyze", {"x": "not a number"})

    async def test_path_type(self):
        mcp = FastMCP()

        @mcp.tool
        def send_path(path: Path) -> str:
            assert isinstance(path, Path)
            return str(path)

        # Use a platform-independent path
        test_path = Path("tmp") / "test.txt"

        async with Client(mcp) as client:
            result = await client.call_tool("send_path", {"path": str(test_path)})
            assert result.data == str(test_path)

    async def test_path_type_error(self):
        mcp = FastMCP()

        @mcp.tool
        def send_path(path: Path) -> str:
            return str(path)

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="Input is not a valid path"):
                await client.call_tool("send_path", {"path": 1})

    async def test_uuid_type(self):
        mcp = FastMCP()

        @mcp.tool
        def send_uuid(x: uuid.UUID) -> str:
            assert isinstance(x, uuid.UUID)
            return str(x)

        test_uuid = uuid.uuid4()

        async with Client(mcp) as client:
            result = await client.call_tool("send_uuid", {"x": test_uuid})
            assert result.data == str(test_uuid)

    async def test_uuid_type_error(self):
        mcp = FastMCP()

        @mcp.tool
        def send_uuid(x: uuid.UUID) -> str:
            return str(x)

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="Input should be a valid UUID"):
                await client.call_tool("send_uuid", {"x": "not a uuid"})

    async def test_datetime_type(self):
        mcp = FastMCP()

        @mcp.tool
        def send_datetime(x: datetime.datetime) -> str:
            return x.isoformat()

        dt = datetime.datetime(2025, 4, 25, 1, 2, 3)

        async with Client(mcp) as client:
            result = await client.call_tool("send_datetime", {"x": dt})
            assert result.data == dt.isoformat()

    async def test_datetime_type_parse_string(self):
        mcp = FastMCP()

        @mcp.tool
        def send_datetime(x: datetime.datetime) -> str:
            return x.isoformat()

        async with Client(mcp) as client:
            result = await client.call_tool(
                "send_datetime", {"x": "2021-01-01T00:00:00"}
            )
            assert result.data == "2021-01-01T00:00:00"

    async def test_datetime_type_error(self):
        mcp = FastMCP()

        @mcp.tool
        def send_datetime(x: datetime.datetime) -> str:
            return x.isoformat()

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="Input should be a valid datetime"):
                await client.call_tool("send_datetime", {"x": "not a datetime"})

    async def test_date_type(self):
        mcp = FastMCP()

        @mcp.tool
        def send_date(x: datetime.date) -> str:
            return x.isoformat()

        async with Client(mcp) as client:
            result = await client.call_tool("send_date", {"x": datetime.date.today()})
            assert result.data == datetime.date.today().isoformat()

    async def test_date_type_parse_string(self):
        mcp = FastMCP()

        @mcp.tool
        def send_date(x: datetime.date) -> str:
            return x.isoformat()

        async with Client(mcp) as client:
            result = await client.call_tool("send_date", {"x": "2021-01-01"})
            assert result.data == "2021-01-01"

    async def test_timedelta_type(self):
        mcp = FastMCP()

        @mcp.tool
        def send_timedelta(x: datetime.timedelta) -> str:
            return str(x)

        async with Client(mcp) as client:
            result = await client.call_tool(
                "send_timedelta", {"x": datetime.timedelta(days=1)}
            )
            assert result.data == "1 day, 0:00:00"

    async def test_timedelta_type_parse_int(self):
        """Test that int input is coerced to timedelta (seconds)."""
        mcp = FastMCP()

        @mcp.tool
        def send_timedelta(x: datetime.timedelta) -> str:
            return str(x)

        async with Client(mcp) as client:
            # Int input should be coerced to timedelta (seconds)
            result = await client.call_tool("send_timedelta", {"x": 1000})
            assert (
                "0:16:40" in result.data or "16:40" in result.data
            )  # 1000 seconds = 16 minutes 40 seconds

    async def test_annotated_string_description(self):
        mcp = FastMCP()

        @mcp.tool
        def f(x: Annotated[int, "A number"]):
            return x

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert len(tools) == 1
            assert tools[0].inputSchema["properties"]["x"]["description"] == "A number"


class TestToolOutputSchema:
    @pytest.mark.parametrize("annotation", [str, int, float, bool, list, AnyUrl])
    async def test_simple_output_schema(self, annotation):
        mcp = FastMCP()

        @mcp.tool
        def f() -> annotation:  # type: ignore
            return "hello"

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert len(tools) == 1

            type_schema = TypeAdapter(annotation).json_schema()
            # Remove title fields from the schema for comparison (title pruning is enabled)
            type_schema = compress_schema(type_schema, prune_titles=True)
            # this line will fail until MCP adds output schemas!!
            assert tools[0].outputSchema == {
                "type": "object",
                "properties": {"result": type_schema},
                "required": ["result"],
                "x-fastmcp-wrap-result": True,
            }

    @pytest.mark.parametrize(
        "annotation",
        [dict[str, int | str], PersonTypedDict, PersonModel, PersonDataclass],
    )
    async def test_structured_output_schema(self, annotation):
        mcp = FastMCP()

        @mcp.tool
        def f() -> annotation:  # type: ignore[valid-type]
            return {"name": "John", "age": 30}

        async with Client(mcp) as client:
            tools = await client.list_tools()

            type_schema = compress_schema(
                TypeAdapter(annotation).json_schema(), prune_titles=True
            )
            assert len(tools) == 1

            # Normalize anyOf ordering for comparison since union type order
            # can vary between environments when using annotation resolution
            actual_schema = _normalize_anyof_order(tools[0].outputSchema)
            expected_schema = _normalize_anyof_order(type_schema)
            assert actual_schema == expected_schema

    async def test_disabled_output_schema_no_structured_content(self):
        mcp = FastMCP()

        @mcp.tool(output_schema=None)
        def f() -> int:
            return 42

        async with Client(mcp) as client:
            result = await client.call_tool("f", {})
            assert result.content[0].text == "42"  # type: ignore[attr-defined]
            assert result.structured_content is None
            assert result.data is None

    async def test_manual_structured_content(self):
        mcp = FastMCP()

        @mcp.tool
        def f() -> ToolResult:
            return ToolResult(
                content="Hello, world!", structured_content={"message": "Hello, world!"}
            )

        assert f.output_schema is None

        async with Client(mcp) as client:
            result = await client.call_tool("f", {})
            assert result.content[0].text == "Hello, world!"  # type: ignore[attr-defined]
            assert result.structured_content == {"message": "Hello, world!"}
            assert result.data == {"message": "Hello, world!"}

    async def test_output_schema_none_full_handshake(self):
        """Test that output_schema=None works through full client/server
        handshake. We test this by returning a scalar, which requires an output
        schema to serialize."""
        mcp = FastMCP()

        @mcp.tool(output_schema=None)
        def simple_tool() -> int:
            return 42

        async with Client(mcp) as client:
            # List tools and verify output schema is None
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "simple_tool")
            assert tool.outputSchema is None

            # Call tool and verify no structured content
            result = await client.call_tool("simple_tool", {})
            assert result.structured_content is None
            assert result.data is None
            assert result.content[0].text == "42"  # type: ignore[attr-defined]

    async def test_output_schema_explicit_object_full_handshake(self):
        """Test explicit object output schema through full client/server handshake."""
        mcp = FastMCP()

        @mcp.tool(
            output_schema={
                "type": "object",
                "properties": {
                    "greeting": {"type": "string"},
                    "count": {"type": "integer"},
                },
                "required": ["greeting"],
            }
        )
        def explicit_tool() -> dict[str, Any]:
            return {"greeting": "Hello", "count": 42}

        async with Client(mcp) as client:
            # List tools and verify exact schema is preserved
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "explicit_tool")
            expected_schema = {
                "type": "object",
                "properties": {
                    "greeting": {"type": "string"},
                    "count": {"type": "integer"},
                },
                "required": ["greeting"],
            }
            assert tool.outputSchema == expected_schema

            # Call tool and verify structured content matches return value directly
            result = await client.call_tool("explicit_tool", {})
            assert result.structured_content == {"greeting": "Hello", "count": 42}
            # Client deserializes according to schema, so check fields
            assert result.data.greeting == "Hello"  # type: ignore[attr-defined]
            assert result.data.count == 42  # type: ignore[attr-defined]

    async def test_output_schema_wrapped_primitive_full_handshake(self):
        """Test wrapped primitive output schema through full client/server handshake."""
        mcp = FastMCP()

        @mcp.tool
        def primitive_tool() -> str:
            return "Hello, primitives!"

        async with Client(mcp) as client:
            # List tools and verify schema shows wrapped structure
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "primitive_tool")
            expected_schema = {
                "type": "object",
                "properties": {"result": {"type": "string"}},
                "required": ["result"],
                "x-fastmcp-wrap-result": True,
            }
            assert tool.outputSchema == expected_schema

            # Call tool and verify structured content is wrapped
            result = await client.call_tool("primitive_tool", {})
            assert result.structured_content == {"result": "Hello, primitives!"}
            assert result.data == "Hello, primitives!"  # Client unwraps for convenience

    async def test_output_schema_complex_type_full_handshake(self):
        """Test complex type output schema through full client/server handshake."""
        mcp = FastMCP()

        @mcp.tool
        def complex_tool() -> list[dict[str, int]]:
            return [{"a": 1, "b": 2}, {"c": 3, "d": 4}]

        async with Client(mcp) as client:
            # List tools and verify schema shows wrapped array
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "complex_tool")
            expected_inner_schema = compress_schema(
                TypeAdapter(list[dict[str, int]]).json_schema(), prune_titles=True
            )
            expected_schema = {
                "type": "object",
                "properties": {"result": expected_inner_schema},
                "required": ["result"],
                "x-fastmcp-wrap-result": True,
            }
            assert tool.outputSchema == expected_schema

            # Call tool and verify structured content is wrapped
            result = await client.call_tool("complex_tool", {})
            expected_data = [{"a": 1, "b": 2}, {"c": 3, "d": 4}]
            assert result.structured_content == {"result": expected_data}
            # Client deserializes - just verify we got data back
            assert result.data is not None

    async def test_output_schema_dataclass_full_handshake(self):
        """Test dataclass output schema through full client/server handshake."""
        mcp = FastMCP()

        @dataclass
        class User:
            name: str
            age: int

        @mcp.tool
        def dataclass_tool() -> User:
            return User(name="Alice", age=30)

        async with Client(mcp) as client:
            # List tools and verify schema is object type (not wrapped)
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "dataclass_tool")
            expected_schema = compress_schema(
                TypeAdapter(User).json_schema(), prune_titles=True
            )
            assert tool.outputSchema == expected_schema
            assert (
                tool.outputSchema and "x-fastmcp-wrap-result" not in tool.outputSchema
            )

            # Call tool and verify structured content is direct
            result = await client.call_tool("dataclass_tool", {})
            assert result.structured_content == {"name": "Alice", "age": 30}
            # Client deserializes according to schema
            assert result.data.name == "Alice"  # type: ignore[attr-defined]
            assert result.data.age == 30  # type: ignore[attr-defined]

    async def test_output_schema_mixed_content_types(self):
        """Test tools with mixed content and output schemas."""
        mcp = FastMCP()

        @mcp.tool
        def mixed_output() -> list[Any]:
            # Return mixed content that includes MCP types and regular data
            return [
                "text message",
                {"structured": "data"},
                TextContent(type="text", text="direct MCP content"),
            ]

        async with Client(mcp) as client:
            result = await client.call_tool("mixed_output", {})

            # Should have multiple content blocks
            assert result == snapshot(
                CallToolResult(
                    content=[
                        TextContent(type="text", text="text message"),
                        TextContent(type="text", text='{"structured":"data"}'),
                        TextContent(type="text", text="direct MCP content"),
                    ],
                    structured_content={
                        "result": [
                            "text message",
                            {"structured": "data"},
                            {
                                "type": "text",
                                "text": "direct MCP content",
                                "annotations": None,
                                "_meta": None,
                            },
                        ]
                    },
                    data=[
                        "text message",
                        {"structured": "data"},
                        {
                            "type": "text",
                            "text": "direct MCP content",
                            "annotations": None,
                            "_meta": None,
                        },
                    ],
                    meta=None,
                )
            )

    async def test_output_schema_serialization_edge_cases(self):
        """Test edge cases in output schema serialization."""
        mcp = FastMCP()

        @mcp.tool
        def edge_case_tool() -> tuple[int, str]:
            return (42, "hello")

        async with Client(mcp) as client:
            # Verify tuple gets proper schema
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "edge_case_tool")

            # Tuples should be wrapped since they're not object type
            assert tool.outputSchema and "x-fastmcp-wrap-result" in tool.outputSchema

            result = await client.call_tool("edge_case_tool", {})
            # Should be wrapped with result key
            assert result.structured_content == {"result": [42, "hello"]}
            assert result.data == [42, "hello"]


class TestToolContextInjection:
    """Test context injection in tools."""

    async def test_context_detection(self):
        """Test that context parameters are properly detected."""
        mcp = FastMCP()

        @mcp.tool
        def tool_with_context(x: int, ctx: Context) -> str:
            return f"Request {ctx.request_id}: {x}"

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert len(tools) == 1
            assert tools[0].name == "tool_with_context"

    async def test_context_injection(self):
        """Test that context is properly injected into tool calls."""
        mcp = FastMCP()

        @mcp.tool
        def tool_with_context(x: int, ctx: Context) -> str:
            assert isinstance(ctx, Context)
            assert ctx.request_id is not None
            return ctx.request_id

        async with Client(mcp) as client:
            result = await client.call_tool("tool_with_context", {"x": 42})
            assert result.data == "1"

    async def test_async_context(self):
        """Test that context works in async functions."""
        mcp = FastMCP()

        @mcp.tool
        async def async_tool(x: int, ctx: Context) -> str:
            assert ctx.request_id is not None
            return f"Async request {ctx.request_id}: {x}"

        async with Client(mcp) as client:
            result = await client.call_tool("async_tool", {"x": 42})
            assert result.data == "Async request 1: 42"

    async def test_optional_context(self):
        """Test that context is optional."""
        mcp = FastMCP()

        @mcp.tool
        def no_context(x: int) -> int:
            return x * 2

        async with Client(mcp) as client:
            result = await client.call_tool("no_context", {"x": 21})
            assert result.data == 42

    async def test_context_resource_access(self):
        """Test that context can access resources."""
        mcp = FastMCP()

        @mcp.resource("test://data")
        def test_resource() -> str:
            return "resource data"

        @mcp.tool
        async def tool_with_resource(ctx: Context) -> str:
            r_iter = await ctx.read_resource("test://data")
            r_list = list(r_iter)
            assert len(r_list) == 1
            r = r_list[0]
            return f"Read resource: {r.content} with mime type {r.mime_type}"

        async with Client(mcp) as client:
            result = await client.call_tool("tool_with_resource", {})
            assert (
                result.data == "Read resource: resource data with mime type text/plain"
            )

    async def test_tool_decorator_with_tags(self):
        """Test that the tool decorator properly sets tags."""
        mcp = FastMCP()

        @mcp.tool(tags={"example", "test-tag"})
        def sample_tool(x: int) -> int:
            return x * 2

        # Verify the tool exists
        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert len(tools) == 1
            # Note: MCPTool from the client API doesn't expose tags

    async def test_callable_object_with_context(self):
        """Test that a callable object can be used as a tool with context."""
        mcp = FastMCP()

        class MyTool:
            async def __call__(self, x: int, ctx: Context) -> int:
                return x + int(ctx.request_id)

        mcp.add_tool(Tool.from_function(MyTool(), name="MyTool"))

        async with Client(mcp) as client:
            result = await client.call_tool("MyTool", {"x": 2})
            assert result.data == 3

    async def test_decorated_tool_with_functools_wraps(self):
        """Regression test for #2524: @mcp.tool with functools.wraps decorator."""

        def custom_decorator(func):
            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                return await func(*args, **kwargs)

            return wrapper

        mcp = FastMCP()

        @mcp.tool
        @custom_decorator
        async def decorated_tool(ctx: Context, query: str) -> str:
            assert isinstance(ctx, Context)
            return f"query: {query}"

        async with Client(mcp) as client:
            # Verify ctx is not in the schema
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "decorated_tool")
            assert "ctx" not in tool.inputSchema.get("properties", {})

            # Verify the tool works
            result = await client.call_tool("decorated_tool", {"query": "test"})
            assert result.data == "query: test"


class TestToolEnabled:
    async def test_toggle_enabled(self):
        mcp = FastMCP()

        @mcp.tool
        def sample_tool(x: int) -> int:
            return x * 2

        assert sample_tool.enabled

        tool = await mcp.get_tool("sample_tool")
        assert tool.enabled

        tool.disable()

        assert not tool.enabled
        assert not sample_tool.enabled

        tool.enable()
        assert tool.enabled
        assert sample_tool.enabled

    async def test_tool_disabled_in_decorator(self):
        mcp = FastMCP()

        @mcp.tool(enabled=False)
        def sample_tool(x: int) -> int:
            return x * 2

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert len(tools) == 0

            with pytest.raises(ToolError, match="Unknown tool"):
                await client.call_tool("sample_tool", {"x": 5})

    async def test_tool_toggle_enabled(self):
        mcp = FastMCP()

        @mcp.tool(enabled=False)
        def sample_tool(x: int) -> int:
            return x * 2

        sample_tool.enable()

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert len(tools) == 1

    async def test_tool_toggle_disabled(self):
        mcp = FastMCP()

        @mcp.tool
        def sample_tool(x: int) -> int:
            return x * 2

        sample_tool.disable()

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert len(tools) == 0

            with pytest.raises(ToolError, match="Unknown tool"):
                await client.call_tool("sample_tool", {"x": 5})

    async def test_get_tool_and_disable(self):
        mcp = FastMCP()

        @mcp.tool
        def sample_tool(x: int) -> int:
            return x * 2

        tool = await mcp.get_tool("sample_tool")
        assert tool.enabled

        sample_tool.disable()

        async with Client(mcp) as client:
            result = await client.list_tools()
            assert len(result) == 0

            with pytest.raises(ToolError, match="Unknown tool"):
                await client.call_tool("sample_tool", {"x": 5})

    async def test_cant_call_disabled_tool(self):
        mcp = FastMCP()

        @mcp.tool(enabled=False)
        def sample_tool(x: int) -> int:
            return x * 2

        with pytest.raises(Exception, match="Unknown tool"):
            async with Client(mcp) as client:
                await client.call_tool("sample_tool", {"x": 5})


class TestResource:
    async def test_text_resource(self):
        mcp = FastMCP()

        def get_text():
            return "Hello, world!"

        resource = FunctionResource(
            uri=AnyUrl("resource://test"), name="test", fn=get_text
        )
        mcp.add_resource(resource)

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://test"))
            assert result[0].text == "Hello, world!"  # type: ignore[attr-defined]

    async def test_binary_resource(self):
        mcp = FastMCP()

        def get_binary():
            return b"Binary data"

        resource = FunctionResource(
            uri=AnyUrl("resource://binary"),
            name="binary",
            fn=get_binary,
            mime_type="application/octet-stream",
        )
        mcp.add_resource(resource)

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://binary"))
            assert result[0].blob == base64.b64encode(b"Binary data").decode()  # type: ignore[attr-defined]

    async def test_file_resource_text(self, tmp_path: Path):
        mcp = FastMCP()

        # Create a text file
        text_file = tmp_path / "test.txt"
        text_file.write_text("Hello from file!")

        resource = FileResource(
            uri=AnyUrl("file://test.txt"), name="test.txt", path=text_file
        )
        mcp.add_resource(resource)

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("file://test.txt"))
            assert result[0].text == "Hello from file!"  # type: ignore[attr-defined]

    async def test_file_resource_binary(self, tmp_path: Path):
        mcp = FastMCP()

        # Create a binary file
        binary_file = tmp_path / "test.bin"
        binary_file.write_bytes(b"Binary file data")

        resource = FileResource(
            uri=AnyUrl("file://test.bin"),
            name="test.bin",
            path=binary_file,
            mime_type="application/octet-stream",
        )
        mcp.add_resource(resource)

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("file://test.bin"))
            assert result[0].blob == base64.b64encode(b"Binary file data").decode()  # type: ignore[attr-defined]

    async def test_resource_with_annotations(self):
        mcp = FastMCP()

        @mcp.resource(
            "http://example.com/data",
            name="test",
            annotations={
                "httpMethod": "GET",
                "Cache-Control": "max-age=3600",
            },
        )
        def get_data() -> str:
            return "Hello, world!"

        async with Client(mcp) as client:
            resources = await client.list_resources()
            assert len(resources) == 1

            resource = resources[0]
            assert str(resource.uri) == "http://example.com/data"

            assert resource.annotations is not None
            assert hasattr(resource.annotations, "httpMethod")
            assert getattr(resource.annotations, "httpMethod") == "GET"
            assert hasattr(resource.annotations, "Cache-Control")
            assert getattr(resource.annotations, "Cache-Control") == "max-age=3600"


class TestResourceTags:
    def create_server(self, include_tags=None, exclude_tags=None):
        mcp = FastMCP(include_tags=include_tags, exclude_tags=exclude_tags)

        @mcp.resource("resource://1", tags={"a", "b"})
        def resource_1() -> str:
            return "1"

        @mcp.resource("resource://2", tags={"b", "c"})
        def resource_2() -> str:
            return "2"

        return mcp

    async def test_include_tags_all_resources(self):
        mcp = self.create_server(include_tags={"a", "b"})

        async with Client(mcp) as client:
            resources = await client.list_resources()
            assert {r.name for r in resources} == {"resource_1", "resource_2"}

    async def test_include_tags_some_resources(self):
        mcp = self.create_server(include_tags={"a", "z"})

        async with Client(mcp) as client:
            resources = await client.list_resources()
            assert {r.name for r in resources} == {"resource_1"}

    async def test_exclude_tags_all_resources(self):
        mcp = self.create_server(exclude_tags={"a", "b"})

        async with Client(mcp) as client:
            resources = await client.list_resources()
            assert {r.name for r in resources} == set()

    async def test_exclude_tags_some_resources(self):
        mcp = self.create_server(exclude_tags={"a"})

        async with Client(mcp) as client:
            resources = await client.list_resources()
            assert {r.name for r in resources} == {"resource_2"}

    async def test_exclude_precedence(self):
        mcp = self.create_server(exclude_tags={"a"}, include_tags={"b"})

        async with Client(mcp) as client:
            resources = await client.list_resources()
            assert {r.name for r in resources} == {"resource_2"}

    async def test_read_included_resource(self):
        mcp = self.create_server(include_tags={"a"})

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://1"))
            assert result[0].text == "1"  # type: ignore[attr-defined]

            with pytest.raises(McpError, match="Unknown resource"):
                await client.read_resource(AnyUrl("resource://2"))

    async def test_read_excluded_resource(self):
        mcp = self.create_server(exclude_tags={"a"})

        async with Client(mcp) as client:
            with pytest.raises(McpError, match="Unknown resource"):
                await client.read_resource(AnyUrl("resource://1"))


class TestResourceContext:
    async def test_resource_with_context_annotation_gets_context(self):
        mcp = FastMCP()

        @mcp.resource("resource://test")
        def resource_with_context(ctx: Context) -> str:
            assert isinstance(ctx, Context)
            return ctx.request_id

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://test"))
            assert result[0].text == "1"  # type: ignore[attr-defined]


class TestResourceEnabled:
    async def test_toggle_enabled(self):
        mcp = FastMCP()

        @mcp.resource("resource://data")
        def sample_resource() -> str:
            return "Hello, world!"

        assert sample_resource.enabled

        resource = await mcp.get_resource("resource://data")
        assert resource.enabled

        resource.disable()

        assert not resource.enabled
        assert not sample_resource.enabled

        resource.enable()
        assert resource.enabled
        assert sample_resource.enabled

    async def test_resource_disabled_in_decorator(self):
        mcp = FastMCP()

        @mcp.resource("resource://data", enabled=False)
        def sample_resource() -> str:
            return "Hello, world!"

        async with Client(mcp) as client:
            resources = await client.list_resources()
            assert len(resources) == 0

            with pytest.raises(McpError, match="Unknown resource"):
                await client.read_resource(AnyUrl("resource://data"))

    async def test_resource_toggle_enabled(self):
        mcp = FastMCP()

        @mcp.resource("resource://data", enabled=False)
        def sample_resource() -> str:
            return "Hello, world!"

        sample_resource.enable()

        async with Client(mcp) as client:
            resources = await client.list_resources()
            assert len(resources) == 1

    async def test_resource_toggle_disabled(self):
        mcp = FastMCP()

        @mcp.resource("resource://data")
        def sample_resource() -> str:
            return "Hello, world!"

        sample_resource.disable()

        async with Client(mcp) as client:
            resources = await client.list_resources()
            assert len(resources) == 0

            with pytest.raises(McpError, match="Unknown resource"):
                await client.read_resource(AnyUrl("resource://data"))

    async def test_get_resource_and_disable(self):
        mcp = FastMCP()

        @mcp.resource("resource://data")
        def sample_resource() -> str:
            return "Hello, world!"

        resource = await mcp.get_resource("resource://data")
        assert resource.enabled

        sample_resource.disable()

        async with Client(mcp) as client:
            result = await client.list_resources()
            assert len(result) == 0

            with pytest.raises(McpError, match="Unknown resource"):
                await client.read_resource(AnyUrl("resource://data"))

    async def test_cant_read_disabled_resource(self):
        mcp = FastMCP()

        @mcp.resource("resource://data", enabled=False)
        def sample_resource() -> str:
            return "Hello, world!"

        with pytest.raises(McpError, match="Unknown resource"):
            async with Client(mcp) as client:
                await client.read_resource(AnyUrl("resource://data"))


class TestResourceTemplates:
    async def test_resource_with_params_not_in_uri(self):
        """Test that a resource with function parameters raises an error if the URI
        parameters don't match"""
        mcp = FastMCP()

        with pytest.raises(
            ValueError,
            match="URI template must contain at least one parameter",
        ):

            @mcp.resource("resource://data")
            def get_data_fn(param: str) -> str:
                return f"Data: {param}"

    async def test_resource_with_uri_params_without_args(self):
        """Test that a resource with URI parameters is automatically a template"""
        mcp = FastMCP()

        with pytest.raises(
            ValueError,
            match="URI parameters .* must be a subset of the function arguments",
        ):

            @mcp.resource("resource://{param}")
            def get_data() -> str:
                return "Data"

    async def test_resource_with_untyped_params(self):
        """Test that a resource with untyped parameters raises an error"""
        mcp = FastMCP()

        @mcp.resource("resource://{param}")
        def get_data(param) -> str:
            return "Data"

    async def test_resource_matching_params(self):
        """Test that a resource with matching URI and function parameters works"""
        mcp = FastMCP()

        @mcp.resource("resource://{name}/data")
        def get_data(name: str) -> str:
            return f"Data for {name}"

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://test/data"))
            assert result[0].text == "Data for test"  # type: ignore[attr-defined]

    async def test_resource_mismatched_params(self):
        """Test that mismatched parameters raise an error"""
        mcp = FastMCP()

        with pytest.raises(
            ValueError,
            match="Required function arguments .* must be a subset of the URI path parameters",
        ):

            @mcp.resource("resource://{name}/data")
            def get_data(user: str) -> str:
                return f"Data for {user}"

    async def test_resource_multiple_params(self):
        """Test that multiple parameters work correctly"""
        mcp = FastMCP()

        @mcp.resource("resource://{org}/{repo}/data")
        def get_data(org: str, repo: str) -> str:
            return f"Data for {org}/{repo}"

        async with Client(mcp) as client:
            result = await client.read_resource(
                AnyUrl("resource://cursor/fastmcp/data")
            )
            assert result[0].text == "Data for cursor/fastmcp"  # type: ignore[attr-defined]

    async def test_resource_multiple_mismatched_params(self):
        """Test that mismatched parameters raise an error"""
        mcp = FastMCP()

        with pytest.raises(
            ValueError,
            match="Required function arguments .* must be a subset of the URI path parameters",
        ):

            @mcp.resource("resource://{org}/{repo}/data")
            def get_data_mismatched(org: str, repo_2: str) -> str:
                return f"Data for {org}"

        """Test that a resource with no parameters works as a regular resource"""
        mcp = FastMCP()

        @mcp.resource("resource://static")
        def get_static_data() -> str:
            return "Static data"

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://static"))
            assert result[0].text == "Static data"  # type: ignore[attr-defined]

    async def test_template_with_varkwargs(self):
        """Test that a template can have **kwargs."""
        mcp = FastMCP()

        @mcp.resource("test://{x}/{y}/{z}")
        def func(**kwargs: int) -> int:
            return sum(kwargs.values())

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("test://1/2/3"))
            assert result[0].text == "6"  # type: ignore[attr-defined]

    async def test_template_with_default_params(self):
        """Test that a template can have default parameters."""
        mcp = FastMCP()

        @mcp.resource("math://add/{x}")
        def add(x: int, y: int = 10) -> int:
            return x + y

        # Verify it's registered as a template
        templates_dict = await mcp.get_resource_templates()
        templates = list(templates_dict.values())
        assert len(templates) == 1
        assert templates[0].uri_template == "math://add/{x}"

        # Call the template and verify it uses the default value
        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("math://add/5"))
            assert result[0].text == "15"  # type: ignore[attr-defined]

            # Can also call with explicit params
            result2 = await client.read_resource(AnyUrl("math://add/7"))
            assert result2[0].text == "17"  # type: ignore[attr-defined]

    async def test_template_to_resource_conversion(self):
        """Test that a template can be converted to a resource."""
        mcp = FastMCP()

        @mcp.resource("resource://{name}/data")
        def get_data(name: str) -> str:
            return f"Data for {name}"

        # Verify it's registered as a template
        templates_dict = await mcp.get_resource_templates()
        templates = list(templates_dict.values())
        assert len(templates) == 1
        assert templates[0].uri_template == "resource://{name}/data"

        # When accessed, should create a concrete resource
        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://test/data"))
            assert result[0].text == "Data for test"  # type: ignore[attr-defined]

    async def test_template_decorator_with_tags(self):
        mcp = FastMCP()

        @mcp.resource("resource://{param}", tags={"template", "test-tag"})
        def template_resource(param: str) -> str:
            return f"Template resource: {param}"

        templates_dict = await mcp.get_resource_templates()
        template = templates_dict["resource://{param}"]
        assert template.tags == {"template", "test-tag"}

    async def test_template_decorator_wildcard_param(self):
        mcp = FastMCP()

        @mcp.resource("resource://{param*}")
        def template_resource(param: str) -> str:
            return f"Template resource: {param}"

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://test/data"))
            assert result[0].text == "Template resource: test/data"  # type: ignore[attr-defined]

    async def test_template_with_query_params(self):
        """Test RFC 6570 query parameters in resource templates."""
        mcp = FastMCP()

        @mcp.resource("data://{id}{?format,limit}")
        def get_data(id: str, format: str = "json", limit: int = 10) -> str:
            return f"id={id}, format={format}, limit={limit}"

        async with Client(mcp) as client:
            # No query params - uses defaults
            result = await client.read_resource(AnyUrl("data://123"))
            assert result[0].text == "id=123, format=json, limit=10"  # type: ignore[attr-defined]

            # One query param
            result = await client.read_resource(AnyUrl("data://123?format=xml"))
            assert result[0].text == "id=123, format=xml, limit=10"  # type: ignore[attr-defined]

            # Multiple query params
            result = await client.read_resource(
                AnyUrl("data://123?format=csv&limit=50")
            )
            assert result[0].text == "id=123, format=csv, limit=50"  # type: ignore[attr-defined]

    async def test_templates_match_in_order_of_definition(self):
        """
        If a wildcard template is defined first, it will take priority over another
        matching template.

        """
        mcp = FastMCP()

        @mcp.resource("resource://{param*}")
        def template_resource(param: str) -> str:
            return f"Template resource 1: {param}"

        @mcp.resource("resource://{x}/{y}")
        def template_resource_with_params(x: str, y: str) -> str:
            return f"Template resource 2: {x}/{y}"

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://a/b/c"))
            assert result[0].text == "Template resource 1: a/b/c"  # type: ignore[attr-defined]

            result = await client.read_resource(AnyUrl("resource://a/b"))
            assert result[0].text == "Template resource 1: a/b"  # type: ignore[attr-defined]

    async def test_templates_shadow_each_other_reorder(self):
        """
        If a wildcard template is defined second, it will *not* take priority over
        another matching template.
        """
        mcp = FastMCP()

        @mcp.resource("resource://{x}/{y}")
        def template_resource_with_params(x: str, y: str) -> str:
            return f"Template resource 1: {x}/{y}"

        @mcp.resource("resource://{param*}")
        def template_resource(param: str) -> str:
            return f"Template resource 2: {param}"

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://a/b/c"))
            assert result[0].text == "Template resource 2: a/b/c"  # type: ignore[attr-defined]

            result = await client.read_resource(AnyUrl("resource://a/b"))
            assert result[0].text == "Template resource 1: a/b"  # type: ignore[attr-defined]

    async def test_resource_template_with_annotations(self):
        """Test that resource template annotations are visible to clients."""
        mcp = FastMCP()

        @mcp.resource(
            "api://users/{user_id}",
            annotations={"httpMethod": "GET", "Cache-Control": "no-cache"},
        )
        def get_user(user_id: str) -> str:
            return f"User {user_id} data"

        async with Client(mcp) as client:
            templates = await client.list_resource_templates()
            assert len(templates) == 1

            template = templates[0]
            assert template.uriTemplate == "api://users/{user_id}"

            assert template.annotations is not None
            assert hasattr(template.annotations, "httpMethod")
            assert getattr(template.annotations, "httpMethod") == "GET"
            assert hasattr(template.annotations, "Cache-Control")
            assert getattr(template.annotations, "Cache-Control") == "no-cache"


class TestResourceTemplatesTags:
    def create_server(self, include_tags=None, exclude_tags=None):
        mcp = FastMCP(include_tags=include_tags, exclude_tags=exclude_tags)

        @mcp.resource("resource://1/{param}", tags={"a", "b"})
        def template_resource_1(param: str) -> str:
            return f"Template resource 1: {param}"

        @mcp.resource("resource://2/{param}", tags={"b", "c"})
        def template_resource_2(param: str) -> str:
            return f"Template resource 2: {param}"

        return mcp

    async def test_include_tags_all_resources(self):
        mcp = self.create_server(include_tags={"a", "b"})

        async with Client(mcp) as client:
            resources = await client.list_resource_templates()
            assert {r.name for r in resources} == {
                "template_resource_1",
                "template_resource_2",
            }

    async def test_include_tags_some_resources(self):
        mcp = self.create_server(include_tags={"a"})

        async with Client(mcp) as client:
            resources = await client.list_resource_templates()
            assert {r.name for r in resources} == {"template_resource_1"}

    async def test_exclude_tags_all_resources(self):
        mcp = self.create_server(exclude_tags={"a", "b"})

        async with Client(mcp) as client:
            resources = await client.list_resource_templates()
            assert {r.name for r in resources} == set()

    async def test_exclude_tags_some_resources(self):
        mcp = self.create_server(exclude_tags={"a"})

        async with Client(mcp) as client:
            resources = await client.list_resource_templates()
            assert {r.name for r in resources} == {"template_resource_2"}

    async def test_exclude_takes_precedence_over_include(self):
        mcp = self.create_server(exclude_tags={"a"}, include_tags={"b"})

        async with Client(mcp) as client:
            resources = await client.list_resource_templates()
            assert {r.name for r in resources} == {"template_resource_2"}

    async def test_read_resource_template_includes_tags(self):
        mcp = self.create_server(include_tags={"a"})

        async with Client(mcp) as client:
            result = await client.read_resource("resource://1/x")
            assert result[0].text == "Template resource 1: x"  # type: ignore[attr-defined]

            with pytest.raises(McpError, match="Unknown resource"):
                await client.read_resource("resource://2/x")

    async def test_read_resource_template_excludes_tags(self):
        mcp = self.create_server(exclude_tags={"a"})

        async with Client(mcp) as client:
            with pytest.raises(McpError, match="Unknown resource"):
                await client.read_resource("resource://1/x")

            result = await client.read_resource("resource://2/x")
            assert result[0].text == "Template resource 2: x"  # type: ignore[attr-defined]


class TestResourceTemplateContext:
    async def test_resource_template_context(self):
        mcp = FastMCP()

        @mcp.resource("resource://{param}")
        def resource_template(param: str, ctx: Context) -> str:
            assert isinstance(ctx, Context)
            return f"Resource template: {param} {ctx.request_id}"

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://test"))
            assert result[0].text.startswith("Resource template: test 1")  # type: ignore[attr-defined]

    async def test_resource_template_context_with_callable_object(self):
        mcp = FastMCP()

        class MyResource:
            def __call__(self, param: str, ctx: Context) -> str:
                return f"Resource template: {param} {ctx.request_id}"

        template = ResourceTemplate.from_function(
            MyResource(), uri_template="resource://{param}"
        )
        mcp.add_template(template)

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://test"))
            assert result[0].text.startswith("Resource template: test 1")  # type: ignore[attr-defined]


class TestResourceTemplateEnabled:
    async def test_toggle_enabled(self):
        mcp = FastMCP()

        @mcp.resource("resource://{param}")
        def sample_template(param: str) -> str:
            return f"Template: {param}"

        assert sample_template.enabled

        template = await mcp.get_resource_template("resource://{param}")
        assert template.enabled

        template.disable()

        assert not template.enabled
        assert not sample_template.enabled

        template.enable()
        assert template.enabled
        assert sample_template.enabled

    async def test_template_disabled_in_decorator(self):
        mcp = FastMCP()

        @mcp.resource("resource://{param}", enabled=False)
        def sample_template(param: str) -> str:
            return f"Template: {param}"

        async with Client(mcp) as client:
            templates = await client.list_resource_templates()
            assert len(templates) == 0

            with pytest.raises(McpError, match="Unknown resource"):
                await client.read_resource(AnyUrl("resource://test"))

    async def test_template_toggle_enabled(self):
        mcp = FastMCP()

        @mcp.resource("resource://{param}", enabled=False)
        def sample_template(param: str) -> str:
            return f"Template: {param}"

        sample_template.enable()

        async with Client(mcp) as client:
            templates = await client.list_resource_templates()
            assert len(templates) == 1

    async def test_template_toggle_disabled(self):
        mcp = FastMCP()

        @mcp.resource("resource://{param}")
        def sample_template(param: str) -> str:
            return f"Template: {param}"

        sample_template.disable()

        async with Client(mcp) as client:
            templates = await client.list_resource_templates()
            assert len(templates) == 0

            with pytest.raises(McpError, match="Unknown resource"):
                await client.read_resource(AnyUrl("resource://test"))

    async def test_get_template_and_disable(self):
        mcp = FastMCP()

        @mcp.resource("resource://{param}")
        def sample_template(param: str) -> str:
            return f"Template: {param}"

        template = await mcp.get_resource_template("resource://{param}")
        assert template.enabled

        sample_template.disable()

        async with Client(mcp) as client:
            result = await client.list_resource_templates()
            assert len(result) == 0

            with pytest.raises(McpError, match="Unknown resource"):
                await client.read_resource(AnyUrl("resource://test"))

    async def test_cant_read_disabled_template(self):
        mcp = FastMCP()

        @mcp.resource("resource://{param}", enabled=False)
        def sample_template(param: str) -> str:
            return f"Template: {param}"

        with pytest.raises(McpError, match="Unknown resource"):
            async with Client(mcp) as client:
                await client.read_resource(AnyUrl("resource://test"))


class TestPrompts:
    """Test prompt functionality in FastMCP server."""

    async def test_prompt_decorator(self):
        """Test that the prompt decorator registers prompts correctly."""
        mcp = FastMCP()

        @mcp.prompt
        def fn() -> str:
            return "Hello, world!"

        prompts_dict = await mcp.get_prompts()
        assert len(prompts_dict) == 1
        prompt = prompts_dict["fn"]
        assert prompt.name == "fn"
        # Don't compare functions directly since validate_call wraps them
        content = await prompt.render()
        assert content[0].content.text == "Hello, world!"  # type: ignore[attr-defined]

    async def test_prompt_decorator_with_name(self):
        """Test prompt decorator with custom name."""
        mcp = FastMCP()

        @mcp.prompt(name="custom_name")
        def fn() -> str:
            return "Hello, world!"

        prompts_dict = await mcp.get_prompts()
        assert len(prompts_dict) == 1
        prompt = prompts_dict["custom_name"]
        assert prompt.name == "custom_name"
        content = await prompt.render()
        assert content[0].content.text == "Hello, world!"  # type: ignore[attr-defined]

    async def test_prompt_decorator_with_description(self):
        """Test prompt decorator with custom description."""
        mcp = FastMCP()

        @mcp.prompt(description="A custom description")
        def fn() -> str:
            return "Hello, world!"

        prompts_dict = await mcp.get_prompts()
        assert len(prompts_dict) == 1
        prompt = prompts_dict["fn"]
        assert prompt.description == "A custom description"
        content = await prompt.render()
        assert content[0].content.text == "Hello, world!"  # type: ignore[attr-defined]

    async def test_prompt_decorator_with_parens(self):
        mcp = FastMCP()

        @mcp.prompt
        def fn() -> str:
            return "Hello, world!"

        prompts_dict = await mcp.get_prompts()
        assert len(prompts_dict) == 1
        prompt = prompts_dict["fn"]
        assert prompt.name == "fn"

    async def test_list_prompts(self):
        """Test listing prompts through MCP protocol."""
        mcp = FastMCP()

        @mcp.prompt
        def fn(name: str, optional: str = "default") -> str:
            return f"Hello, {name}! {optional}"

        prompts_dict = await mcp.get_prompts()
        assert len(prompts_dict) == 1

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            assert len(prompts) == 1
            assert prompts[0].name == "fn"
            assert prompts[0].description is None
            assert prompts[0].arguments is not None
            assert len(prompts[0].arguments) == 2
            assert prompts[0].arguments[0].name == "name"
            assert prompts[0].arguments[0].required is True
            assert prompts[0].arguments[1].name == "optional"
            assert prompts[0].arguments[1].required is False

    async def test_list_prompts_with_enhanced_descriptions(self):
        """Test that enhanced descriptions with JSON schema are visible via MCP protocol."""
        mcp = FastMCP()

        @mcp.prompt
        def analyze_data(
            name: str, numbers: list[int], metadata: dict[str, str], threshold: float
        ) -> str:
            """Analyze some data."""
            return f"Analyzed {name}"

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            assert len(prompts) == 1
            prompt = prompts[0]
            assert prompt.name == "analyze_data"
            assert prompt.description == "Analyze some data."

            # Find each argument and verify schema enhancements
            assert prompt.arguments is not None
            args_by_name = {arg.name: arg for arg in prompt.arguments}

            # String parameter should not have schema enhancement
            name_arg = args_by_name["name"]
            assert name_arg.description is None

            # Non-string parameters should have schema enhancements
            numbers_arg = args_by_name["numbers"]
            assert numbers_arg.description is not None
            assert (
                "Provide as a JSON string matching the following schema:"
                in numbers_arg.description
            )
            assert (
                '{"items":{"type":"integer"},"type":"array"}' in numbers_arg.description
            )

            metadata_arg = args_by_name["metadata"]
            assert metadata_arg.description is not None
            assert (
                "Provide as a JSON string matching the following schema:"
                in metadata_arg.description
            )
            assert (
                '{"additionalProperties":{"type":"string"},"type":"object"}'
                in metadata_arg.description
            )

            threshold_arg = args_by_name["threshold"]
            assert threshold_arg.description is not None
            assert (
                "Provide as a JSON string matching the following schema:"
                in threshold_arg.description
            )
            assert '{"type":"number"}' in threshold_arg.description

    async def test_get_prompt(self):
        """Test getting a prompt through MCP protocol."""
        mcp = FastMCP()

        @mcp.prompt
        def fn(name: str) -> str:
            return f"Hello, {name}!"

        async with Client(mcp) as client:
            result = await client.get_prompt("fn", {"name": "World"})
            assert len(result.messages) == 1
            message = result.messages[0]
            assert message.role == "user"
            content = message.content
            assert content.text == "Hello, World!"  # type: ignore[attr-defined]

    async def test_get_prompt_with_resource(self):
        """Test getting a prompt that returns resource content."""
        mcp = FastMCP()

        @mcp.prompt
        def fn() -> PromptMessage:
            return PromptMessage(
                role="user",
                content=EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=AnyUrl("file://file.txt"),
                        text="File contents",
                        mimeType="text/plain",
                    ),
                ),
            )

        async with Client(mcp) as client:
            result = await client.get_prompt("fn")
            assert result.messages[0].role == "user"
            content = result.messages[0].content
            assert isinstance(content, EmbeddedResource)
            assert isinstance(content.resource, TextResourceContents)
            assert content.resource.text == "File contents"
            assert content.resource.mimeType == "text/plain"

    async def test_get_unknown_prompt(self):
        """Test error when getting unknown prompt."""
        mcp = FastMCP()
        with pytest.raises(McpError, match="Unknown prompt"):
            async with Client(mcp) as client:
                await client.get_prompt("unknown")

    async def test_get_prompt_missing_args(self):
        """Test error when required arguments are missing."""
        mcp = FastMCP()

        @mcp.prompt
        def prompt_fn(name: str) -> str:
            return f"Hello, {name}!"

        with pytest.raises(McpError, match="Missing required arguments"):
            async with Client(mcp) as client:
                await client.get_prompt("prompt_fn")

    async def test_resource_decorator_with_tags(self):
        """Test that the resource decorator supports tags."""
        mcp = FastMCP()

        @mcp.resource("resource://data", tags={"example", "test-tag"})
        def get_data() -> str:
            return "Hello, world!"

        resources_dict = await mcp.get_resources()
        resources = list(resources_dict.values())
        assert len(resources) == 1
        assert resources[0].tags == {"example", "test-tag"}

    async def test_template_decorator_with_tags(self):
        """Test that the template decorator properly sets tags."""
        mcp = FastMCP()

        @mcp.resource("resource://{param}", tags={"template", "test-tag"})
        def template_resource(param: str) -> str:
            return f"Template resource: {param}"

        templates_dict = await mcp.get_resource_templates()
        template = templates_dict["resource://{param}"]
        assert template.tags == {"template", "test-tag"}

    async def test_prompt_decorator_with_tags(self):
        """Test that the prompt decorator properly sets tags."""
        mcp = FastMCP()

        @mcp.prompt(tags={"example", "test-tag"})
        def sample_prompt() -> str:
            return "Hello, world!"

        prompts_dict = await mcp.get_prompts()
        assert len(prompts_dict) == 1
        prompt = prompts_dict["sample_prompt"]
        assert prompt.tags == {"example", "test-tag"}


class TestPromptEnabled:
    async def test_toggle_enabled(self):
        mcp = FastMCP()

        @mcp.prompt
        def sample_prompt() -> str:
            return "Hello, world!"

        assert sample_prompt.enabled

        prompt = await mcp.get_prompt("sample_prompt")
        assert prompt.enabled

        prompt.disable()

        assert not prompt.enabled
        assert not sample_prompt.enabled

        prompt.enable()
        assert prompt.enabled
        assert sample_prompt.enabled

    async def test_prompt_disabled_in_decorator(self):
        mcp = FastMCP()

        @mcp.prompt(enabled=False)
        def sample_prompt() -> str:
            return "Hello, world!"

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            assert len(prompts) == 0

            with pytest.raises(McpError, match="Unknown prompt"):
                await client.get_prompt("sample_prompt")

    async def test_prompt_toggle_enabled(self):
        mcp = FastMCP()

        @mcp.prompt(enabled=False)
        def sample_prompt() -> str:
            return "Hello, world!"

        sample_prompt.enable()

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            assert len(prompts) == 1

    async def test_prompt_toggle_disabled(self):
        mcp = FastMCP()

        @mcp.prompt
        def sample_prompt() -> str:
            return "Hello, world!"

        sample_prompt.disable()

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            assert len(prompts) == 0

            with pytest.raises(McpError, match="Unknown prompt"):
                await client.get_prompt("sample_prompt")

    async def test_get_prompt_and_disable(self):
        mcp = FastMCP()

        @mcp.prompt
        def sample_prompt() -> str:
            return "Hello, world!"

        prompt = await mcp.get_prompt("sample_prompt")
        assert prompt.enabled

        sample_prompt.disable()

        async with Client(mcp) as client:
            result = await client.list_prompts()
            assert len(result) == 0

            with pytest.raises(McpError, match="Unknown prompt"):
                await client.get_prompt("sample_prompt")

    async def test_cant_get_disabled_prompt(self):
        mcp = FastMCP()

        @mcp.prompt(enabled=False)
        def sample_prompt() -> str:
            return "Hello, world!"

        with pytest.raises(McpError, match="Unknown prompt"):
            async with Client(mcp) as client:
                await client.get_prompt("sample_prompt")


class TestPromptContext:
    async def test_prompt_context(self):
        mcp = FastMCP()

        @mcp.prompt
        def prompt_fn(name: str, ctx: Context) -> str:
            assert isinstance(ctx, Context)
            return f"Hello, {name}! {ctx.request_id}"

        async with Client(mcp) as client:
            result = await client.get_prompt("prompt_fn", {"name": "World"})
            assert len(result.messages) == 1
            message = result.messages[0]
            assert message.role == "user"

    async def test_prompt_context_with_callable_object(self):
        mcp = FastMCP()

        class MyPrompt:
            def __call__(self, name: str, ctx: Context) -> str:
                return f"Hello, {name}! {ctx.request_id}"

        mcp.add_prompt(Prompt.from_function(MyPrompt(), name="my_prompt"))  # noqa: F821

        async with Client(mcp) as client:
            result = await client.get_prompt("my_prompt", {"name": "World"})
            assert len(result.messages) == 1
            message = result.messages[0]
            assert message.role == "user"
            assert message.content.text == "Hello, World! 1"  # type: ignore[attr-defined]


class TestPromptTags:
    def create_server(self, include_tags=None, exclude_tags=None):
        mcp = FastMCP(include_tags=include_tags, exclude_tags=exclude_tags)

        @mcp.prompt(tags={"a", "b"})
        def prompt_1() -> str:
            return "1"

        @mcp.prompt(tags={"b", "c"})
        def prompt_2() -> str:
            return "2"

        return mcp

    async def test_include_tags_all_prompts(self):
        mcp = self.create_server(include_tags={"a", "b"})

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            assert {p.name for p in prompts} == {"prompt_1", "prompt_2"}

    async def test_include_tags_some_prompts(self):
        mcp = self.create_server(include_tags={"a"})

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            assert {p.name for p in prompts} == {"prompt_1"}

    async def test_exclude_tags_all_prompts(self):
        mcp = self.create_server(exclude_tags={"a", "b"})

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            assert {p.name for p in prompts} == set()

    async def test_exclude_tags_some_prompts(self):
        mcp = self.create_server(exclude_tags={"a"})

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            assert {p.name for p in prompts} == {"prompt_2"}

    async def test_exclude_takes_precedence_over_include(self):
        mcp = self.create_server(exclude_tags={"a"}, include_tags={"b"})

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            assert {p.name for p in prompts} == {"prompt_2"}

    async def test_read_prompt_includes_tags(self):
        mcp = self.create_server(include_tags={"a"})

        async with Client(mcp) as client:
            result = await client.get_prompt("prompt_1")
            assert result.messages[0].content.text == "1"  # type: ignore[attr-defined]

            with pytest.raises(McpError, match="Unknown prompt"):
                await client.get_prompt("prompt_2")

    async def test_read_prompt_excludes_tags(self):
        mcp = self.create_server(exclude_tags={"a"})

        async with Client(mcp) as client:
            with pytest.raises(McpError, match="Unknown prompt"):
                await client.get_prompt("prompt_1")

            result = await client.get_prompt("prompt_2")
            assert result.messages[0].content.text == "2"  # type: ignore[attr-defined]


class TestMeta:
    """Test that include_fastmcp_meta controls whether _fastmcp key is present in meta."""

    async def test_tool_tags_in_meta_with_default_setting(self):
        """Test that tool tags appear in meta under _fastmcp key with default setting."""
        mcp = FastMCP()

        @mcp.tool(tags={"tool-example", "test-tool-tag"})
        def sample_tool(x: int) -> int:
            """A sample tool."""
            return x * 2

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "sample_tool")
            assert tool.meta is not None
            assert set(tool.meta["_fastmcp"]["tags"]) == {
                "tool-example",
                "test-tool-tag",
            }

    async def test_resource_tags_in_meta_with_default_setting(self):
        """Test that resource tags appear in meta under _fastmcp key with default setting."""
        mcp = FastMCP()

        @mcp.resource(
            uri="test://resource", tags={"resource-example", "test-resource-tag"}
        )
        def sample_resource() -> str:
            """A sample resource."""
            return "resource content"

        async with Client(mcp) as client:
            resources = await client.list_resources()
            resource = next(r for r in resources if str(r.uri) == "test://resource")
            assert resource.meta is not None
            assert set(resource.meta["_fastmcp"]["tags"]) == {
                "resource-example",
                "test-resource-tag",
            }

    async def test_resource_template_tags_in_meta_with_default_setting(self):
        """Test that resource template tags appear in meta under _fastmcp key with default setting."""
        mcp = FastMCP()

        @mcp.resource(
            "test://template/{id}", tags={"template-example", "test-template-tag"}
        )
        def sample_template(id: str) -> str:
            """A sample resource template."""
            return f"template content for {id}"

        async with Client(mcp) as client:
            templates = await client.list_resource_templates()
            template = next(
                t for t in templates if t.uriTemplate == "test://template/{id}"
            )
            assert template.meta is not None
            assert set(template.meta["_fastmcp"]["tags"]) == {
                "template-example",
                "test-template-tag",
            }

    async def test_prompt_tags_in_meta_with_default_setting(self):
        """Test that prompt tags appear in meta under _fastmcp key with default setting."""
        mcp = FastMCP()

        @mcp.prompt(tags={"example", "test-tag"})
        def sample_prompt() -> str:
            return "Hello, world!"

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            prompt = next(p for p in prompts if p.name == "sample_prompt")
            assert prompt.meta is not None
            assert set(prompt.meta["_fastmcp"]["tags"]) == {"example", "test-tag"}

    async def test_tool_meta_with_include_fastmcp_meta_false(self):
        mcp = FastMCP(include_fastmcp_meta=False)

        @mcp.tool(tags={"tool-example", "test-tool-tag"})
        def sample_tool(x: int) -> int:
            """A sample tool."""
            return x * 2

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "sample_tool")
            # Meta should be None when include_fastmcp_meta is False and no explicit meta is set
            assert tool.meta is None

    async def test_resource_meta_with_include_fastmcp_meta_false(self):
        mcp = FastMCP(include_fastmcp_meta=False)

        @mcp.resource(
            uri="test://resource", tags={"resource-example", "test-resource-tag"}
        )
        def sample_resource() -> str:
            """A sample resource."""
            return "resource content"

        async with Client(mcp) as client:
            resources = await client.list_resources()
            resource = next(r for r in resources if str(r.uri) == "test://resource")
            # Meta should be None when include_fastmcp_meta is False and no explicit meta is set
            assert resource.meta is None

    async def test_resource_template_meta_with_include_fastmcp_meta_false(self):
        mcp = FastMCP(include_fastmcp_meta=False)

        @mcp.resource(
            "test://template/{id}", tags={"template-example", "test-template-tag"}
        )
        def sample_template(id: str) -> str:
            """A sample resource template."""
            return f"template content for {id}"

        async with Client(mcp) as client:
            templates = await client.list_resource_templates()
            template = next(
                t for t in templates if t.uriTemplate == "test://template/{id}"
            )
            # Meta should be None when include_fastmcp_meta is False and no explicit meta is set
            assert template.meta is None

    async def test_prompt_meta_with_include_fastmcp_meta_false(self):
        mcp = FastMCP(include_fastmcp_meta=False)

        @mcp.prompt(tags={"example", "test-tag"})
        def sample_prompt() -> str:
            return "Hello, world!"

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            prompt = next(p for p in prompts if p.name == "sample_prompt")
            # Meta should be None when include_fastmcp_meta is False and no explicit meta is set
            assert prompt.meta is None

    async def test_global_settings_inheritance(self):
        """Test that servers inherit the global include_fastmcp_meta setting."""
        with temporary_settings(include_fastmcp_meta=False):
            # Server should inherit global setting
            mcp = FastMCP()

            @mcp.tool(tags={"test-tag"})
            def sample_tool(x: int) -> int:
                return x * 2

            async with Client(mcp) as client:
                tools = await client.list_tools()
                tool = next(t for t in tools if t.name == "sample_tool")
                # Meta should be None because global setting is False
                assert tool.meta is None

        # Verify that default behavior is restored
        mcp2 = FastMCP()

        @mcp2.tool(tags={"test-tag"})
        def another_tool(x: int) -> int:
            return x * 2

        async with Client(mcp2) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "another_tool")
            # Meta should have _fastmcp key because global setting is back to default (True)
            assert tool.meta is not None
            assert "_fastmcp" in tool.meta
            assert tool.meta["_fastmcp"]["tags"] == ["test-tag"]

    async def test_explicit_override_of_global_setting(self):
        """Test that explicit include_fastmcp_meta parameter overrides global setting."""
        with temporary_settings(include_fastmcp_meta=False):
            # Explicitly override global setting to True
            mcp = FastMCP(include_fastmcp_meta=True)

            @mcp.tool(tags={"test-tag"})
            def sample_tool(x: int) -> int:
                return x * 2

            async with Client(mcp) as client:
                tools = await client.list_tools()
                tool = next(t for t in tools if t.name == "sample_tool")
                # Meta should have _fastmcp key because explicit setting overrides global
                assert tool.meta is not None
                assert "_fastmcp" in tool.meta
                assert tool.meta["_fastmcp"]["tags"] == ["test-tag"]
