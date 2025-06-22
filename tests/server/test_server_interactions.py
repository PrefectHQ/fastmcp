import base64
import datetime
import json
import uuid
from enum import Enum
from pathlib import Path
from typing import Annotated, Literal

import pydantic_core
import pytest
from mcp import McpError
from mcp.types import (
    AudioContent,
    BlobResourceContents,
    EmbeddedResource,
    ImageContent,
    TextContent,
    TextResourceContents,
)
from pydantic import AnyUrl, Field

from fastmcp import Client, Context, FastMCP
from fastmcp.client.transports import FastMCPTransport
from fastmcp.exceptions import ToolError
from fastmcp.prompts.prompt import Prompt, PromptMessage
from fastmcp.resources import FileResource, ResourceTemplate
from fastmcp.resources.resource import FunctionResource
from fastmcp.tools.tool import Tool
from fastmcp.utilities.types import Audio, File, Image


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

    @mcp.tool
    def mixed_list_fn(image_path: str) -> list:
        return [
            "text message",
            Image(image_path),
            {"key": "value"},
            TextContent(type="text", text="direct content"),
        ]

    @mcp.tool
    def mixed_audio_list_fn(audio_path: str) -> list:
        return [
            "text message",
            Audio(audio_path),
            {"key": "value"},
            TextContent(type="text", text="direct content"),
        ]

    @mcp.tool
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

    async def test_call_tool(self, tool_server: FastMCP):
        async with Client(tool_server) as client:
            result = await client.call_tool("add", {"x": 1, "y": 2})
            assert result[0].text == "3"  # type: ignore[attr-defined]

    async def test_call_tool_as_client(self, tool_server: FastMCP):
        async with Client(tool_server) as client:
            result = await client.call_tool("add", {"x": 1, "y": 2})
            assert result[0].text == "3"  # type: ignore[attr-defined]

    async def test_call_tool_error(self, tool_server: FastMCP):
        async with Client(tool_server) as client:
            with pytest.raises(Exception):
                await client.call_tool("error_tool", {})

    async def test_call_tool_error_as_client(self, tool_server: FastMCP):
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
            assert result[0].text == '[\n  "x",\n  2\n]'  # type: ignore[attr-defined]

    async def test_file_text_tool(self, tool_server: FastMCP):
        async with Client(tool_server) as client:
            result = await client.call_tool("file_text_tool", {})
            assert len(result) == 1
            embedded = result[0]
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
            assert result_1[0].text == "1"  # type: ignore[attr-defined]

            with pytest.raises(ToolError, match="Unknown tool"):
                await client.call_tool("tool_2", {})

    async def test_call_excluded_tool(self):
        mcp = self.create_server(exclude_tags={"a"})

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="Unknown tool"):
                await client.call_tool("tool_1", {})

            result_2 = await client.call_tool("tool_2", {})
            assert result_2[0].text == "2"  # type: ignore[attr-defined]


class TestToolReturnTypes:
    async def test_string(self):
        mcp = FastMCP()

        @mcp.tool
        def string_tool() -> str:
            return "Hello, world!"

        async with Client(mcp) as client:
            result = await client.call_tool("string_tool", {})
            assert result[0].text == "Hello, world!"  # type: ignore[attr-defined]

    async def test_bytes(self, tmp_path: Path):
        mcp = FastMCP()

        @mcp.tool
        def bytes_tool() -> bytes:
            return b"Hello, world!"

        async with Client(mcp) as client:
            result = await client.call_tool("bytes_tool", {})
            assert result[0].text == '"Hello, world!"'  # type: ignore[attr-defined]

    async def test_uuid(self):
        mcp = FastMCP()

        test_uuid = uuid.uuid4()

        @mcp.tool
        def uuid_tool() -> uuid.UUID:
            return test_uuid

        async with Client(mcp) as client:
            result = await client.call_tool("uuid_tool", {})
            assert result[0].text == pydantic_core.to_json(test_uuid).decode()  # type: ignore[attr-defined]

    async def test_path(self):
        mcp = FastMCP()

        test_path = Path("/tmp/test.txt")

        @mcp.tool
        def path_tool() -> Path:
            return test_path

        async with Client(mcp) as client:
            result = await client.call_tool("path_tool", {})
            assert result[0].text == pydantic_core.to_json(test_path).decode()  # type: ignore[attr-defined]

    async def test_datetime(self):
        mcp = FastMCP()

        dt = datetime.datetime(2025, 4, 25, 1, 2, 3)

        @mcp.tool
        def datetime_tool() -> datetime.datetime:
            return dt

        async with Client(mcp) as client:
            result = await client.call_tool("datetime_tool", {})
            assert result[0].text == pydantic_core.to_json(dt).decode()  # type: ignore[attr-defined]

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
            content = result[0]
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
            content = result[0]
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
            content = result[0]
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
            assert len(result) == 3
            content1 = result[0]
            content2 = result[1]
            content3 = result[2]
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
        correctly. Note that the non-MCP content will be grouped together."""
        # Create a test image
        image_path = tmp_path / "test.png"
        image_path.write_bytes(b"test image data")

        async with Client(tool_server) as client:
            result = await client.call_tool(
                "mixed_list_fn", {"image_path": str(image_path)}
            )
            assert len(result) == 3
            # Check text conversion
            content1 = result[0]
            assert isinstance(content1, TextContent)
            assert json.loads(content1.text) == ["text message", {"key": "value"}]
            # Check image conversion
            content2 = result[1]
            assert isinstance(content2, ImageContent)
            assert content2.mimeType == "image/png"
            assert base64.b64decode(content2.data) == b"test image data"
            # Check direct TextContent
            content3 = result[2]
            assert isinstance(content3, TextContent)
            assert content3.text == "direct content"

    async def test_tool_mixed_list_with_audio(
        self, tool_server: FastMCP, tmp_path: Path
    ):
        """Test that lists containing Audio objects and other types are handled
        correctly. Note that the non-MCP content will be grouped together."""
        # Create a test audio file
        audio_path = tmp_path / "test.wav"
        audio_path.write_bytes(b"test audio data")

        async with Client(tool_server) as client:
            result = await client.call_tool(
                "mixed_audio_list_fn", {"audio_path": str(audio_path)}
            )
            assert len(result) == 3
            # Check text conversion
            content1 = result[0]
            assert isinstance(content1, TextContent)
            assert json.loads(content1.text) == ["text message", {"key": "value"}]
            # Check audio conversion
            content2 = result[1]
            assert isinstance(content2, AudioContent)
            assert content2.mimeType == "audio/wav"
            assert base64.b64decode(content2.data) == b"test audio data"
            # Check direct TextContent
            content3 = result[2]
            assert isinstance(content3, TextContent)
            assert content3.text == "direct content"

    async def test_tool_mixed_list_with_file(
        self, tool_server: FastMCP, tmp_path: Path
    ):
        """Test that lists containing File objects and other types are handled
        correctly. Note that the non-MCP content will be grouped together."""
        # Create a test file
        file_path = tmp_path / "test.bin"
        file_path.write_bytes(b"test file data")

        async with Client(tool_server) as client:
            result = await client.call_tool(
                "mixed_file_list_fn", {"file_path": str(file_path)}
            )
            assert len(result) == 3
            # Check text conversion
            content1 = result[0]
            assert isinstance(content1, TextContent)
            assert json.loads(content1.text) == ["text message", {"key": "value"}]
            # Check file conversion
            content2 = result[1]
            assert isinstance(content2, EmbeddedResource)
            assert content2.type == "resource"
            resource = content2.resource
            assert resource.mimeType == "application/octet-stream"
            assert hasattr(resource, "blob")
            blob_data = getattr(resource, "blob")
            assert base64.b64decode(blob_data) == b"test file data"
            # Check direct TextContent
            content3 = result[2]
            assert isinstance(content3, TextContent)
            assert content3.text == "direct content"


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
            assert isinstance(result[0], ImageContent)
            assert result[0].mimeType == "image/png"
            assert result[0].data == base64.b64encode(b"fake png data").decode()

    async def test_tool_with_invalid_input(self):
        mcp = FastMCP()

        @mcp.tool
        def my_tool(x: int) -> int:
            return x + 1

        async with Client(mcp) as client:
            with pytest.raises(
                ToolError,
                match="Error calling tool 'my_tool'",
            ):
                await client.call_tool("my_tool", {"x": "not an int"})

    async def test_tool_int_coercion(self):
        """Test string-to-int type coercion."""
        mcp = FastMCP()

        @mcp.tool
        def add_one(x: int) -> int:
            return x + 1

        async with Client(mcp) as client:
            # String with integer value should be coerced to int
            result = await client.call_tool("add_one", {"x": "42"})
            assert result[0].text == "43"  # type: ignore[attr-defined]

    async def test_tool_bool_coercion(self):
        """Test string-to-bool type coercion."""
        mcp = FastMCP()

        @mcp.tool
        def toggle(flag: bool) -> bool:
            return not flag

        async with Client(mcp) as client:
            # String with boolean value should be coerced to bool
            result = await client.call_tool("toggle", {"flag": "true"})
            assert result[0].text == "false"  # type: ignore[attr-defined]

            result = await client.call_tool("toggle", {"flag": "false"})
            assert result[0].text == "true"  # type: ignore[attr-defined]

    async def test_annotated_field_validation(self):
        mcp = FastMCP()

        @mcp.tool
        def analyze(x: Annotated[int, Field(ge=1)]) -> None:
            pass

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="Error calling tool 'analyze'"):
                await client.call_tool("analyze", {"x": 0})

    async def test_default_field_validation(self):
        mcp = FastMCP()

        @mcp.tool
        def analyze(x: int = Field(ge=1)) -> None:
            pass

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="Error calling tool 'analyze'"):
                await client.call_tool("analyze", {"x": 0})

    async def test_default_field_is_still_required_if_no_default_specified(self):
        mcp = FastMCP()

        @mcp.tool
        def analyze(x: int = Field()) -> None:
            pass

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="Error calling tool 'analyze'"):
                await client.call_tool("analyze", {})

    async def test_literal_type_validation_error(self):
        mcp = FastMCP()

        @mcp.tool
        def analyze(x: Literal["a", "b"]) -> None:
            pass

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="Error calling tool 'analyze'"):
                await client.call_tool("analyze", {"x": "c"})

    async def test_literal_type_validation_success(self):
        mcp = FastMCP()

        @mcp.tool
        def analyze(x: Literal["a", "b"]) -> str:
            return x

        async with Client(mcp) as client:
            result = await client.call_tool("analyze", {"x": "a"})
            assert result[0].text == "a"  # type: ignore[attr-defined]

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
            with pytest.raises(ToolError, match="Error calling tool 'analyze'"):
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
            assert result[0].text == "red"  # type: ignore[attr-defined]

    async def test_union_type_validation(self):
        mcp = FastMCP()

        @mcp.tool
        def analyze(x: int | float) -> str:
            return str(x)

        async with Client(mcp) as client:
            result = await client.call_tool("analyze", {"x": 1})
            assert result[0].text == "1"  # type: ignore[attr-defined]

            result = await client.call_tool("analyze", {"x": 1.0})
            assert result[0].text == "1.0"  # type: ignore[attr-defined]

            with pytest.raises(ToolError, match="Error calling tool 'analyze'"):
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
            assert result[0].text == str(test_path)  # type: ignore[attr-defined]

    async def test_path_type_error(self):
        mcp = FastMCP()

        @mcp.tool
        def send_path(path: Path) -> str:
            return str(path)

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="Error calling tool 'send_path'"):
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
            assert result[0].text == str(test_uuid)  # type: ignore[attr-defined]

    async def test_uuid_type_error(self):
        mcp = FastMCP()

        @mcp.tool
        def send_uuid(x: uuid.UUID) -> str:
            return str(x)

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="Error calling tool 'send_uuid'"):
                await client.call_tool("send_uuid", {"x": "not a uuid"})

    async def test_datetime_type(self):
        mcp = FastMCP()

        @mcp.tool
        def send_datetime(x: datetime.datetime) -> str:
            return x.isoformat()

        dt = datetime.datetime(2025, 4, 25, 1, 2, 3)

        async with Client(mcp) as client:
            result = await client.call_tool("send_datetime", {"x": dt})
            assert result[0].text == dt.isoformat()  # type: ignore[attr-defined]

    async def test_datetime_type_parse_string(self):
        mcp = FastMCP()

        @mcp.tool
        def send_datetime(x: datetime.datetime) -> str:
            return x.isoformat()

        async with Client(mcp) as client:
            result = await client.call_tool(
                "send_datetime", {"x": "2021-01-01T00:00:00"}
            )
            assert result[0].text == "2021-01-01T00:00:00"  # type: ignore[attr-defined]

    async def test_datetime_type_error(self):
        mcp = FastMCP()

        @mcp.tool
        def send_datetime(x: datetime.datetime) -> str:
            return x.isoformat()

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="Error calling tool 'send_datetime'"):
                await client.call_tool("send_datetime", {"x": "not a datetime"})

    async def test_date_type(self):
        mcp = FastMCP()

        @mcp.tool
        def send_date(x: datetime.date) -> str:
            return x.isoformat()

        async with Client(mcp) as client:
            result = await client.call_tool("send_date", {"x": datetime.date.today()})
            assert result[0].text == datetime.date.today().isoformat()  # type: ignore[attr-defined]

    async def test_date_type_parse_string(self):
        mcp = FastMCP()

        @mcp.tool
        def send_date(x: datetime.date) -> str:
            return x.isoformat()

        async with Client(mcp) as client:
            result = await client.call_tool("send_date", {"x": "2021-01-01"})
            assert result[0].text == "2021-01-01"  # type: ignore[attr-defined]

    async def test_timedelta_type(self):
        mcp = FastMCP()

        @mcp.tool
        def send_timedelta(x: datetime.timedelta) -> str:
            return str(x)

        async with Client(mcp) as client:
            result = await client.call_tool(
                "send_timedelta", {"x": datetime.timedelta(days=1)}
            )
            assert result[0].text == "1 day, 0:00:00"  # type: ignore[attr-defined]

    async def test_timedelta_type_parse_int(self):
        mcp = FastMCP()

        @mcp.tool
        def send_timedelta(x: datetime.timedelta) -> str:
            return str(x)

        async with Client(mcp) as client:
            result = await client.call_tool("send_timedelta", {"x": 1000})
            assert result[0].text == "0:16:40"  # type: ignore[attr-defined]


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
            assert len(result) == 1
            content = result[0]
            assert content.text == "1"  # type: ignore[attr-defined]

    async def test_async_context(self):
        """Test that context works in async functions."""
        mcp = FastMCP()

        @mcp.tool
        async def async_tool(x: int, ctx: Context) -> str:
            assert ctx.request_id is not None
            return f"Async request {ctx.request_id}: {x}"

        async with Client(mcp) as client:
            result = await client.call_tool("async_tool", {"x": 42})
            assert len(result) == 1
            content = result[0]
            assert content.text == "Async request 1: 42"  # type: ignore[attr-defined]

    async def test_optional_context(self):
        """Test that context is optional."""
        mcp = FastMCP()

        @mcp.tool
        def no_context(x: int) -> int:
            return x * 2

        async with Client(mcp) as client:
            result = await client.call_tool("no_context", {"x": 21})
            assert len(result) == 1
            content = result[0]
            assert content.text == "42"  # type: ignore[attr-defined]

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
            assert len(result) == 1
            content = result[0]
            assert "Read resource: resource data" in content.text  # type: ignore[attr-defined]

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
            assert result[0].text == "3"  # type: ignore[attr-defined]


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
            match="Required function arguments .* must be a subset of the URI parameters",
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
            match="Required function arguments .* must be a subset of the URI parameters",
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
