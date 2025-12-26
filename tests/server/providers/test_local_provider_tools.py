"""Tests for tool behavior in LocalProvider.

Tests cover:
- Tool return types and serialization
- Tool parameters and validation
- Tool output schemas
- Tool context injection
- Tool decorator patterns
"""

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
from mcp.types import (
    AudioContent,
    BlobResourceContents,
    EmbeddedResource,
    ImageContent,
    TextContent,
)
from pydantic import AnyUrl, BaseModel, Field, TypeAdapter
from typing_extensions import TypedDict

from fastmcp import Context, FastMCP
from fastmcp.exceptions import NotFoundError
from fastmcp.tools.tool import Tool, ToolResult
from fastmcp.utilities.json_schema import compress_schema
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
        return File(data=b"hello world", format="plain")

    return mcp


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

        tools = await mcp.get_tools()
        assert len(tools) == 1
        tool = tools[0]

        properties = tool.parameters["properties"]
        assert "name" in properties
        assert properties["name"]["description"] == "The name to greet"
        assert "title" in properties
        assert properties["title"]["description"] == "Optional title"
        assert properties["title"]["default"] == ""
        assert tool.parameters["required"] == ["name"]

    async def test_parameter_descriptions_with_field_defaults(self):
        mcp = FastMCP("Test Server")

        @mcp.tool
        def greet(
            name: str = Field(description="The name to greet"),
            title: str = Field(description="Optional title", default=""),
        ) -> str:
            """A greeting tool"""
            return f"Hello {title} {name}"

        tools = await mcp.get_tools()
        assert len(tools) == 1
        tool = tools[0]

        properties = tool.parameters["properties"]
        assert "name" in properties
        assert properties["name"]["description"] == "The name to greet"
        assert "title" in properties
        assert properties["title"]["description"] == "Optional title"
        assert properties["title"]["default"] == ""
        assert tool.parameters["required"] == ["name"]

    async def test_tool_with_bytes_input(self):
        mcp = FastMCP()

        @mcp.tool
        def process_image(image: bytes) -> Image:
            return Image(data=image)

        result = await mcp.call_tool("process_image", {"image": b"fake png data"})
        assert result.structured_content is None
        assert isinstance(result.content, list)
        assert isinstance(result.content[0], ImageContent)
        assert result.content[0].mimeType == "image/png"
        assert result.content[0].data == base64.b64encode(b"fake png data").decode()

    async def test_tool_with_invalid_input(self):
        from pydantic import ValidationError

        mcp = FastMCP()

        @mcp.tool
        def my_tool(x: int) -> int:
            return x + 1

        with pytest.raises(
            ValidationError,
            match="Input should be a valid integer",
        ):
            await mcp.call_tool("my_tool", {"x": "not an int"})

    async def test_tool_int_coercion(self):
        """Test that string ints are coerced by default."""
        mcp = FastMCP()

        @mcp.tool
        def add_one(x: int) -> int:
            return x + 1

        result = await mcp.call_tool("add_one", {"x": "42"})
        assert result.structured_content == {"result": 43}

    async def test_tool_bool_coercion(self):
        """Test that string bools are coerced by default."""
        mcp = FastMCP()

        @mcp.tool
        def toggle(flag: bool) -> bool:
            return not flag

        result = await mcp.call_tool("toggle", {"flag": "true"})
        assert result.structured_content == {"result": False}

        result = await mcp.call_tool("toggle", {"flag": "false"})
        assert result.structured_content == {"result": True}

    async def test_annotated_field_validation(self):
        from pydantic import ValidationError

        mcp = FastMCP()

        @mcp.tool
        def analyze(x: Annotated[int, Field(ge=1)]) -> None:
            pass

        with pytest.raises(
            ValidationError,
            match="Input should be greater than or equal to 1",
        ):
            await mcp.call_tool("analyze", {"x": 0})

    async def test_default_field_validation(self):
        from pydantic import ValidationError

        mcp = FastMCP()

        @mcp.tool
        def analyze(x: int = Field(ge=1)) -> None:
            pass

        with pytest.raises(
            ValidationError,
            match="Input should be greater than or equal to 1",
        ):
            await mcp.call_tool("analyze", {"x": 0})

    async def test_default_field_is_still_required_if_no_default_specified(self):
        from pydantic import ValidationError

        mcp = FastMCP()

        @mcp.tool
        def analyze(x: int = Field()) -> None:
            pass

        with pytest.raises(ValidationError, match="missing"):
            await mcp.call_tool("analyze", {})

    async def test_literal_type_validation_error(self):
        from pydantic import ValidationError

        mcp = FastMCP()

        @mcp.tool
        def analyze(x: Literal["a", "b"]) -> None:
            pass

        with pytest.raises(
            ValidationError,
            match="Input should be 'a' or 'b'",
        ):
            await mcp.call_tool("analyze", {"x": "c"})

    async def test_literal_type_validation_success(self):
        mcp = FastMCP()

        @mcp.tool
        def analyze(x: Literal["a", "b"]) -> str:
            return x

        result = await mcp.call_tool("analyze", {"x": "a"})
        assert result.structured_content == {"result": "a"}

    async def test_enum_type_validation_error(self):
        from pydantic import ValidationError

        mcp = FastMCP()

        class MyEnum(Enum):
            RED = "red"
            GREEN = "green"
            BLUE = "blue"

        @mcp.tool
        def analyze(x: MyEnum) -> str:
            return x.value

        with pytest.raises(
            ValidationError,
            match="Input should be 'red', 'green' or 'blue'",
        ):
            await mcp.call_tool("analyze", {"x": "some-color"})

    async def test_enum_type_validation_success(self):
        mcp = FastMCP()

        class MyEnum(Enum):
            RED = "red"
            GREEN = "green"
            BLUE = "blue"

        @mcp.tool
        def analyze(x: MyEnum) -> str:
            return x.value

        result = await mcp.call_tool("analyze", {"x": "red"})
        assert result.structured_content == {"result": "red"}

    async def test_union_type_validation(self):
        from pydantic import ValidationError

        mcp = FastMCP()

        @mcp.tool
        def analyze(x: int | float) -> str:
            return str(x)

        result = await mcp.call_tool("analyze", {"x": 1})
        assert result.structured_content == {"result": "1"}

        result = await mcp.call_tool("analyze", {"x": 1.0})
        assert result.structured_content == {"result": "1.0"}

        with pytest.raises(
            ValidationError,
            match="Input should be a valid",
        ):
            await mcp.call_tool("analyze", {"x": "not a number"})

    async def test_path_type(self):
        mcp = FastMCP()

        @mcp.tool
        def send_path(path: Path) -> str:
            assert isinstance(path, Path)
            return str(path)

        test_path = Path("tmp") / "test.txt"

        result = await mcp.call_tool("send_path", {"path": str(test_path)})
        assert result.structured_content == {"result": str(test_path)}

    async def test_path_type_error(self):
        from pydantic import ValidationError

        mcp = FastMCP()

        @mcp.tool
        def send_path(path: Path) -> str:
            return str(path)

        with pytest.raises(ValidationError, match="Input is not a valid path"):
            await mcp.call_tool("send_path", {"path": 1})

    async def test_uuid_type(self):
        mcp = FastMCP()

        @mcp.tool
        def send_uuid(x: uuid.UUID) -> str:
            assert isinstance(x, uuid.UUID)
            return str(x)

        test_uuid = uuid.uuid4()

        result = await mcp.call_tool("send_uuid", {"x": test_uuid})
        assert result.structured_content == {"result": str(test_uuid)}

    async def test_uuid_type_error(self):
        from pydantic import ValidationError

        mcp = FastMCP()

        @mcp.tool
        def send_uuid(x: uuid.UUID) -> str:
            return str(x)

        with pytest.raises(ValidationError, match="Input should be a valid UUID"):
            await mcp.call_tool("send_uuid", {"x": "not a uuid"})

    async def test_datetime_type(self):
        mcp = FastMCP()

        @mcp.tool
        def send_datetime(x: datetime.datetime) -> str:
            return x.isoformat()

        dt = datetime.datetime(2025, 4, 25, 1, 2, 3)

        result = await mcp.call_tool("send_datetime", {"x": dt})
        assert result.structured_content == {"result": dt.isoformat()}

    async def test_datetime_type_parse_string(self):
        mcp = FastMCP()

        @mcp.tool
        def send_datetime(x: datetime.datetime) -> str:
            return x.isoformat()

        result = await mcp.call_tool("send_datetime", {"x": "2021-01-01T00:00:00"})
        assert result.structured_content == {"result": "2021-01-01T00:00:00"}

    async def test_datetime_type_error(self):
        from pydantic import ValidationError

        mcp = FastMCP()

        @mcp.tool
        def send_datetime(x: datetime.datetime) -> str:
            return x.isoformat()

        with pytest.raises(ValidationError, match="Input should be a valid datetime"):
            await mcp.call_tool("send_datetime", {"x": "not a datetime"})

    async def test_date_type(self):
        mcp = FastMCP()

        @mcp.tool
        def send_date(x: datetime.date) -> str:
            return x.isoformat()

        result = await mcp.call_tool("send_date", {"x": datetime.date.today()})
        assert result.structured_content == {
            "result": datetime.date.today().isoformat()
        }

    async def test_date_type_parse_string(self):
        mcp = FastMCP()

        @mcp.tool
        def send_date(x: datetime.date) -> str:
            return x.isoformat()

        result = await mcp.call_tool("send_date", {"x": "2021-01-01"})
        assert result.structured_content == {"result": "2021-01-01"}

    async def test_timedelta_type(self):
        mcp = FastMCP()

        @mcp.tool
        def send_timedelta(x: datetime.timedelta) -> str:
            return str(x)

        result = await mcp.call_tool(
            "send_timedelta", {"x": datetime.timedelta(days=1)}
        )
        assert result.structured_content == {"result": "1 day, 0:00:00"}

    async def test_timedelta_type_parse_int(self):
        """Test that int input is coerced to timedelta (seconds)."""
        mcp = FastMCP()

        @mcp.tool
        def send_timedelta(x: datetime.timedelta) -> str:
            return str(x)

        result = await mcp.call_tool("send_timedelta", {"x": 1000})
        assert result.structured_content is not None
        result_str = result.structured_content["result"]
        assert (
            "0:16:40" in result_str or "16:40" in result_str
        )  # 1000 seconds = 16 minutes 40 seconds

    async def test_annotated_string_description(self):
        mcp = FastMCP()

        @mcp.tool
        def f(x: Annotated[int, "A number"]):
            return x

        tools = await mcp.get_tools()
        assert len(tools) == 1
        assert tools[0].parameters["properties"]["x"]["description"] == "A number"


class TestToolOutputSchema:
    @pytest.mark.parametrize("annotation", [str, int, float, bool, list, AnyUrl])
    async def test_simple_output_schema(self, annotation):
        mcp = FastMCP()

        @mcp.tool
        def f() -> annotation:
            return "hello"

        tools = await mcp.get_tools()
        assert len(tools) == 1

        type_schema = TypeAdapter(annotation).json_schema()
        type_schema = compress_schema(type_schema, prune_titles=True)
        assert tools[0].output_schema == {
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
        def f() -> annotation:
            return {"name": "John", "age": 30}

        tools = await mcp.get_tools()

        type_schema = compress_schema(
            TypeAdapter(annotation).json_schema(), prune_titles=True
        )
        assert len(tools) == 1

        actual_schema = _normalize_anyof_order(tools[0].output_schema)
        expected_schema = _normalize_anyof_order(type_schema)
        assert actual_schema == expected_schema

    async def test_disabled_output_schema_no_structured_content(self):
        mcp = FastMCP()

        @mcp.tool(output_schema=None)
        def f() -> int:
            return 42

        result = await mcp.call_tool("f", {})
        assert isinstance(result.content, list)
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "42"
        assert result.structured_content is None

    async def test_manual_structured_content(self):
        mcp = FastMCP()

        @mcp.tool
        def f() -> ToolResult:
            return ToolResult(
                content="Hello, world!", structured_content={"message": "Hello, world!"}
            )

        assert f.output_schema is None

        result = await mcp.call_tool("f", {})
        assert isinstance(result.content, list)
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "Hello, world!"
        assert result.structured_content == {"message": "Hello, world!"}

    async def test_output_schema_none(self):
        """Test that output_schema=None works correctly."""
        mcp = FastMCP()

        @mcp.tool(output_schema=None)
        def simple_tool() -> int:
            return 42

        tools = await mcp.get_tools()
        tool = next(t for t in tools if t.name == "simple_tool")
        assert tool.output_schema is None

        result = await mcp.call_tool("simple_tool", {})
        assert result.structured_content is None
        assert isinstance(result.content, list)
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "42"

    async def test_output_schema_explicit_object(self):
        """Test explicit object output schema."""
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

        tools = await mcp.get_tools()
        tool = next(t for t in tools if t.name == "explicit_tool")
        expected_schema = {
            "type": "object",
            "properties": {
                "greeting": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": ["greeting"],
        }
        assert tool.output_schema == expected_schema

        result = await mcp.call_tool("explicit_tool", {})
        assert result.structured_content == {"greeting": "Hello", "count": 42}

    async def test_output_schema_wrapped_primitive(self):
        """Test wrapped primitive output schema."""
        mcp = FastMCP()

        @mcp.tool
        def primitive_tool() -> str:
            return "Hello, primitives!"

        tools = await mcp.get_tools()
        tool = next(t for t in tools if t.name == "primitive_tool")
        expected_schema = {
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result"],
            "x-fastmcp-wrap-result": True,
        }
        assert tool.output_schema == expected_schema

        result = await mcp.call_tool("primitive_tool", {})
        assert result.structured_content == {"result": "Hello, primitives!"}

    async def test_output_schema_complex_type(self):
        """Test complex type output schema."""
        mcp = FastMCP()

        @mcp.tool
        def complex_tool() -> list[dict[str, int]]:
            return [{"a": 1, "b": 2}, {"c": 3, "d": 4}]

        tools = await mcp.get_tools()
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
        assert tool.output_schema == expected_schema

        result = await mcp.call_tool("complex_tool", {})
        expected_data = [{"a": 1, "b": 2}, {"c": 3, "d": 4}]
        assert result.structured_content == {"result": expected_data}

    async def test_output_schema_dataclass(self):
        """Test dataclass output schema."""
        mcp = FastMCP()

        @dataclass
        class User:
            name: str
            age: int

        @mcp.tool
        def dataclass_tool() -> User:
            return User(name="Alice", age=30)

        tools = await mcp.get_tools()
        tool = next(t for t in tools if t.name == "dataclass_tool")
        expected_schema = compress_schema(
            TypeAdapter(User).json_schema(), prune_titles=True
        )
        assert tool.output_schema == expected_schema
        assert tool.output_schema and "x-fastmcp-wrap-result" not in tool.output_schema

        result = await mcp.call_tool("dataclass_tool", {})
        assert result.structured_content == {"name": "Alice", "age": 30}

    async def test_output_schema_mixed_content_types(self):
        """Test tools with mixed content and output schemas."""
        mcp = FastMCP()

        @mcp.tool
        def mixed_output() -> list[Any]:
            return [
                "text message",
                {"structured": "data"},
                TextContent(type="text", text="direct MCP content"),
            ]

        result = await mcp.call_tool("mixed_output", {})
        assert isinstance(result.content, list)
        assert len(result.content) == 3
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "text message"
        assert isinstance(result.content[1], TextContent)
        assert result.content[1].text == '{"structured":"data"}'
        assert isinstance(result.content[2], TextContent)
        assert result.content[2].text == "direct MCP content"

    async def test_output_schema_serialization_edge_cases(self):
        """Test edge cases in output schema serialization."""
        mcp = FastMCP()

        @mcp.tool
        def edge_case_tool() -> tuple[int, str]:
            return (42, "hello")

        tools = await mcp.get_tools()
        tool = next(t for t in tools if t.name == "edge_case_tool")

        assert tool.output_schema and "x-fastmcp-wrap-result" in tool.output_schema

        result = await mcp.call_tool("edge_case_tool", {})
        assert result.structured_content == {"result": [42, "hello"]}


class TestToolContextInjection:
    """Test context injection in tools."""

    async def test_context_detection(self):
        """Test that context parameters are properly detected and excluded from schema."""
        mcp = FastMCP()

        @mcp.tool
        def tool_with_context(x: int, ctx: Context) -> str:
            return f"Request: {x}"

        tools = await mcp.get_tools()
        assert len(tools) == 1
        assert tools[0].name == "tool_with_context"
        # Context param should not appear in schema
        assert "ctx" not in tools[0].parameters.get("properties", {})

    async def test_context_injection_basic(self):
        """Test that context is properly injected into tool calls."""
        mcp = FastMCP()

        @mcp.tool
        def tool_with_context(x: int, ctx: Context) -> str:
            assert isinstance(ctx, Context)
            return f"Got context with x={x}"

        result = await mcp.call_tool("tool_with_context", {"x": 42})
        assert result.structured_content == {"result": "Got context with x=42"}

    async def test_async_context(self):
        """Test that context works in async functions."""
        mcp = FastMCP()

        @mcp.tool
        async def async_tool(x: int, ctx: Context) -> str:
            assert isinstance(ctx, Context)
            return f"Async with x={x}"

        result = await mcp.call_tool("async_tool", {"x": 42})
        assert result.structured_content == {"result": "Async with x=42"}

    async def test_optional_context(self):
        """Test that context is optional."""
        mcp = FastMCP()

        @mcp.tool
        def no_context(x: int) -> int:
            return x * 2

        result = await mcp.call_tool("no_context", {"x": 21})
        assert result.structured_content == {"result": 42}

    async def test_context_resource_access(self):
        """Test that context can access resources."""
        mcp = FastMCP()

        @mcp.resource("test://data")
        def test_resource() -> str:
            return "resource data"

        @mcp.tool
        async def tool_with_resource(ctx: Context) -> str:
            result = await ctx.read_resource("test://data")
            assert len(result.contents) == 1
            r = result.contents[0]
            return f"Read resource: {r.content} with mime type {r.mime_type}"

        result = await mcp.call_tool("tool_with_resource", {})
        assert result.structured_content == {
            "result": "Read resource: resource data with mime type text/plain"
        }

    async def test_tool_decorator_with_tags(self):
        """Test that the tool decorator properly sets tags."""
        mcp = FastMCP()

        @mcp.tool(tags={"example", "test-tag"})
        def sample_tool(x: int) -> int:
            return x * 2

        tools = await mcp.get_tools()
        assert len(tools) == 1
        assert tools[0].tags == {"example", "test-tag"}

    async def test_callable_object_with_context(self):
        """Test that a callable object can be used as a tool with context."""
        mcp = FastMCP()

        class MyTool:
            async def __call__(self, x: int, ctx: Context) -> int:
                assert isinstance(ctx, Context)
                return x + 1

        mcp.add_tool(Tool.from_function(MyTool(), name="MyTool"))

        result = await mcp.call_tool("MyTool", {"x": 2})
        assert result.structured_content == {"result": 3}

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

        tools = await mcp.get_tools()
        tool = next(t for t in tools if t.name == "decorated_tool")
        assert "ctx" not in tool.parameters.get("properties", {})

        result = await mcp.call_tool("decorated_tool", {"query": "test"})
        assert result.structured_content == {"result": "query: test"}


class TestToolDecorator:
    async def test_no_tools_before_decorator(self):
        from fastmcp.exceptions import NotFoundError

        mcp = FastMCP()

        with pytest.raises(NotFoundError, match="Unknown tool: 'add'"):
            await mcp.call_tool("add", {"x": 1, "y": 2})

    async def test_tool_decorator(self):
        mcp = FastMCP()

        @mcp.tool
        def add(x: int, y: int) -> int:
            return x + y

        result = await mcp.call_tool("add", {"x": 1, "y": 2})
        assert result.structured_content == {"result": 3}

    async def test_tool_decorator_without_parentheses(self):
        """Test that @tool decorator works without parentheses."""
        mcp = FastMCP()

        @mcp.tool
        def add(x: int, y: int) -> int:
            return x + y

        tools = await mcp.get_tools()
        assert any(t.name == "add" for t in tools)

        result = await mcp.call_tool("add", {"x": 1, "y": 2})
        assert result.structured_content == {"result": 3}

    async def test_tool_decorator_with_name(self):
        mcp = FastMCP()

        @mcp.tool(name="custom-add")
        def add(x: int, y: int) -> int:
            return x + y

        result = await mcp.call_tool("custom-add", {"x": 1, "y": 2})
        assert result.structured_content == {"result": 3}

    async def test_tool_decorator_with_description(self):
        mcp = FastMCP()

        @mcp.tool(description="Add two numbers")
        def add(x: int, y: int) -> int:
            return x + y

        tools = await mcp.get_tools()
        assert len(tools) == 1
        tool = tools[0]
        assert tool.description == "Add two numbers"

    async def test_tool_decorator_instance_method(self):
        mcp = FastMCP()

        class MyClass:
            def __init__(self, x: int):
                self.x = x

            def add(self, y: int) -> int:
                return self.x + y

        obj = MyClass(10)
        mcp.add_tool(Tool.from_function(obj.add))
        result = await mcp.call_tool("add", {"y": 2})
        assert result.structured_content == {"result": 12}

    async def test_tool_decorator_classmethod(self):
        mcp = FastMCP()

        class MyClass:
            x: int = 10

            @classmethod
            def add(cls, y: int) -> int:
                return cls.x + y

        mcp.add_tool(Tool.from_function(MyClass.add))
        result = await mcp.call_tool("add", {"y": 2})
        assert result.structured_content == {"result": 12}

    async def test_tool_decorator_staticmethod(self):
        mcp = FastMCP()

        class MyClass:
            @mcp.tool
            @staticmethod
            def add(x: int, y: int) -> int:
                return x + y

        result = await mcp.call_tool("add", {"x": 1, "y": 2})
        assert result.structured_content == {"result": 3}

    async def test_tool_decorator_async_function(self):
        mcp = FastMCP()

        @mcp.tool
        async def add(x: int, y: int) -> int:
            return x + y

        result = await mcp.call_tool("add", {"x": 1, "y": 2})
        assert result.structured_content == {"result": 3}

    async def test_tool_decorator_classmethod_error(self):
        mcp = FastMCP()

        with pytest.raises(ValueError, match="To decorate a classmethod"):

            class MyClass:
                @mcp.tool
                @classmethod
                def add(cls, y: int) -> None:
                    pass

    async def test_tool_decorator_classmethod_async_function(self):
        mcp = FastMCP()

        class MyClass:
            x = 10

            @classmethod
            async def add(cls, y: int) -> int:
                return cls.x + y

        mcp.add_tool(Tool.from_function(MyClass.add))
        result = await mcp.call_tool("add", {"y": 2})
        assert result.structured_content == {"result": 12}

    async def test_tool_decorator_staticmethod_async_function(self):
        mcp = FastMCP()

        class MyClass:
            @staticmethod
            async def add(x: int, y: int) -> int:
                return x + y

        mcp.add_tool(Tool.from_function(MyClass.add))
        result = await mcp.call_tool("add", {"x": 1, "y": 2})
        assert result.structured_content == {"result": 3}

    async def test_tool_decorator_staticmethod_order(self):
        """Test that the recommended decorator order works for static methods"""
        mcp = FastMCP()

        class MyClass:
            @mcp.tool
            @staticmethod
            def add_v1(x: int, y: int) -> int:
                return x + y

        result = await mcp.call_tool("add_v1", {"x": 1, "y": 2})
        assert result.structured_content == {"result": 3}

    async def test_tool_decorator_with_tags(self):
        """Test that the tool decorator properly sets tags."""
        mcp = FastMCP()

        @mcp.tool(tags={"example", "test-tag"})
        def sample_tool(x: int) -> int:
            return x * 2

        tools = await mcp.get_tools()
        assert len(tools) == 1
        assert tools[0].tags == {"example", "test-tag"}

    async def test_add_tool_with_custom_name(self):
        """Test adding a tool with a custom name using server.add_tool()."""
        mcp = FastMCP()

        def multiply(a: int, b: int) -> int:
            """Multiply two numbers."""
            return a * b

        mcp.add_tool(Tool.from_function(multiply, name="custom_multiply"))

        tools = await mcp.get_tools()
        assert any(t.name == "custom_multiply" for t in tools)

        result = await mcp.call_tool("custom_multiply", {"a": 5, "b": 3})
        assert result.structured_content == {"result": 15}

        assert not any(t.name == "multiply" for t in tools)

    async def test_tool_with_annotated_arguments(self):
        """Test that tools with annotated arguments work correctly."""
        mcp = FastMCP()

        @mcp.tool
        def add(
            x: Annotated[int, Field(description="x is an int")],
            y: Annotated[str, Field(description="y is not an int")],
        ) -> None:
            pass

        tools = await mcp.get_tools()
        tool = next(t for t in tools if t.name == "add")
        assert tool.parameters["properties"]["x"]["description"] == "x is an int"
        assert tool.parameters["properties"]["y"]["description"] == "y is not an int"

    async def test_tool_with_field_defaults(self):
        """Test that tools with annotated arguments work correctly."""
        mcp = FastMCP()

        @mcp.tool
        def add(
            x: int = Field(description="x is an int"),
            y: str = Field(description="y is not an int"),
        ) -> None:
            pass

        tools = await mcp.get_tools()
        tool = next(t for t in tools if t.name == "add")
        assert tool.parameters["properties"]["x"]["description"] == "x is an int"
        assert tool.parameters["properties"]["y"]["description"] == "y is not an int"

    async def test_tool_direct_function_call(self):
        """Test that tools can be registered via direct function call."""
        from fastmcp.tools import FunctionTool

        mcp = FastMCP()

        def standalone_function(x: int, y: int) -> int:
            """A standalone function to be registered."""
            return x + y

        result_fn = mcp.tool(standalone_function, name="direct_call_tool")

        assert isinstance(result_fn, FunctionTool)

        tools = await mcp.get_tools()
        tool = next(t for t in tools if t.name == "direct_call_tool")
        assert tool is result_fn

        result = await mcp.call_tool("direct_call_tool", {"x": 5, "y": 3})
        assert result.structured_content == {"result": 8}

    async def test_tool_decorator_with_string_name(self):
        """Test that @tool("custom_name") syntax works correctly."""
        mcp = FastMCP()

        @mcp.tool("string_named_tool")
        def my_function(x: int) -> str:
            """A function with a string name."""
            return f"Result: {x}"

        tools = await mcp.get_tools()
        assert any(t.name == "string_named_tool" for t in tools)
        assert not any(t.name == "my_function" for t in tools)

        result = await mcp.call_tool("string_named_tool", {"x": 42})
        assert result.structured_content == {"result": "Result: 42"}

    async def test_tool_decorator_conflicting_names_error(self):
        """Test that providing both positional and keyword name raises an error."""
        mcp = FastMCP()

        with pytest.raises(
            TypeError,
            match="Cannot specify both a name as first argument and as keyword argument",
        ):

            @mcp.tool("positional_name", name="keyword_name")
            def my_function(x: int) -> str:
                return f"Result: {x}"

    async def test_tool_decorator_with_output_schema(self):
        mcp = FastMCP()

        with pytest.raises(
            ValueError, match="Output schemas must represent object types"
        ):

            @mcp.tool(output_schema={"type": "integer"})
            def my_function(x: int) -> str:
                return f"Result: {x}"

    async def test_tool_decorator_with_meta(self):
        """Test that meta parameter is passed through the tool decorator."""
        mcp = FastMCP()

        meta_data = {"version": "1.0", "author": "test"}

        @mcp.tool(meta=meta_data)
        def multiply(a: int, b: int) -> int:
            """Multiply two numbers."""
            return a * b

        tools = await mcp.get_tools()
        tool = next(t for t in tools if t.name == "multiply")

        assert tool.meta == meta_data


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
        tools = await mcp.get_tools()
        assert {t.name for t in tools} == {"tool_1", "tool_2"}

    async def test_include_tags_some_tools(self):
        mcp = self.create_server(include_tags={"a", "z"})
        tools = await mcp.get_tools()
        assert {t.name for t in tools} == {"tool_1"}

    async def test_exclude_tags_all_tools(self):
        mcp = self.create_server(exclude_tags={"a", "b"})
        tools = await mcp.get_tools()
        assert {t.name for t in tools} == set()

    async def test_exclude_tags_some_tools(self):
        mcp = self.create_server(exclude_tags={"a", "z"})
        tools = await mcp.get_tools()
        assert {t.name for t in tools} == {"tool_2"}

    async def test_exclude_precedence(self):
        mcp = self.create_server(exclude_tags={"a"}, include_tags={"b"})
        tools = await mcp.get_tools()
        assert {t.name for t in tools} == {"tool_2"}

    async def test_call_included_tool(self):
        mcp = self.create_server(include_tags={"a"})
        result_1 = await mcp.call_tool("tool_1", {})
        assert result_1.structured_content == {"result": 1}

        with pytest.raises(NotFoundError, match="Unknown tool"):
            await mcp.call_tool("tool_2", {})

    async def test_call_excluded_tool(self):
        mcp = self.create_server(exclude_tags={"a"})
        with pytest.raises(NotFoundError, match="Unknown tool"):
            await mcp.call_tool("tool_1", {})

        result_2 = await mcp.call_tool("tool_2", {})
        assert result_2.structured_content == {"result": 2}


class TestToolEnabled:
    async def test_toggle_enabled(self):
        mcp = FastMCP()

        @mcp.tool
        def sample_tool(x: int) -> int:
            return x * 2

        # Tool is enabled by default
        tools = await mcp.get_tools()
        assert any(t.name == "sample_tool" for t in tools)

        # Disable via server
        mcp.disable(keys=["tool:sample_tool"])

        # Tool should not be in list when disabled
        tools = await mcp.get_tools()
        assert not any(t.name == "sample_tool" for t in tools)

        # Re-enable via server
        mcp.enable(keys=["tool:sample_tool"])
        tools = await mcp.get_tools()
        assert any(t.name == "sample_tool" for t in tools)

    async def test_tool_disabled_via_server(self):
        mcp = FastMCP()

        @mcp.tool
        def sample_tool(x: int) -> int:
            return x * 2

        mcp.disable(keys=["tool:sample_tool"])
        tools = await mcp.get_tools()
        assert len(tools) == 0

        with pytest.raises(NotFoundError, match="Unknown tool"):
            await mcp.call_tool("sample_tool", {"x": 5})

    async def test_tool_toggle_enabled(self):
        mcp = FastMCP()

        @mcp.tool
        def sample_tool(x: int) -> int:
            return x * 2

        mcp.disable(keys=["tool:sample_tool"])
        mcp.enable(keys=["tool:sample_tool"])
        tools = await mcp.get_tools()
        assert len(tools) == 1

    async def test_tool_toggle_disabled(self):
        mcp = FastMCP()

        @mcp.tool
        def sample_tool(x: int) -> int:
            return x * 2

        mcp.disable(keys=["tool:sample_tool"])
        tools = await mcp.get_tools()
        assert len(tools) == 0

        with pytest.raises(NotFoundError, match="Unknown tool"):
            await mcp.call_tool("sample_tool", {"x": 5})

    async def test_get_tool_and_disable(self):
        mcp = FastMCP()

        @mcp.tool
        def sample_tool(x: int) -> int:
            return x * 2

        tool = await mcp.get_tool(name="sample_tool")
        assert tool is not None

        mcp.disable(keys=["tool:sample_tool"])
        tools = await mcp.get_tools()
        assert len(tools) == 0

        with pytest.raises(NotFoundError, match="Unknown tool"):
            await mcp.call_tool("sample_tool", {"x": 5})

    async def test_cant_call_disabled_tool(self):
        mcp = FastMCP()

        @mcp.tool
        def sample_tool(x: int) -> int:
            return x * 2

        mcp.disable(keys=["tool:sample_tool"])

        with pytest.raises(NotFoundError, match="Unknown tool"):
            await mcp.call_tool("sample_tool", {"x": 5})
