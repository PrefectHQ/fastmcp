"""
Tests to verify all return types work identically with task=True.

These tests ensure that enabling background task support doesn't break
existing functionality - any tool/prompt/resource should work exactly
the same whether task=True or task=False.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel
from typing_extensions import TypedDict

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.utilities.types import Audio, File, Image


class UserData(BaseModel):
    """Example structured output."""

    name: str
    age: int
    active: bool


@pytest.fixture
async def return_type_server():
    """Server with tools that return various types."""
    mcp = FastMCP("return-type-test")

    # String return
    @mcp.tool(task=True)
    async def return_string() -> str:
        return "Hello, World!"

    # Integer return
    @mcp.tool(task=True)
    async def return_int() -> int:
        return 42

    # Float return
    @mcp.tool(task=True)
    async def return_float() -> float:
        return 3.14159

    # Boolean return
    @mcp.tool(task=True)
    async def return_bool() -> bool:
        return True

    # Dict return
    @mcp.tool(task=True)
    async def return_dict() -> dict[str, int]:
        return {"count": 100, "total": 500}

    # List return
    @mcp.tool(task=True)
    async def return_list() -> list[str]:
        return ["apple", "banana", "cherry"]

    # BaseModel return (structured output)
    @mcp.tool(task=True)
    async def return_model() -> UserData:
        return UserData(name="Alice", age=30, active=True)

    # None/null return
    @mcp.tool(task=True)
    async def return_none() -> None:
        return None

    return mcp


@pytest.mark.parametrize(
    "tool_name,expected_type,expected_value",
    [
        ("return_string", str, "Hello, World!"),
        ("return_int", int, 42),
        ("return_float", float, 3.14159),
        ("return_bool", bool, True),
        ("return_dict", dict, {"count": 100, "total": 500}),
        ("return_list", list, ["apple", "banana", "cherry"]),
        ("return_none", type(None), None),
    ],
)
async def test_task_basic_types(
    return_type_server: FastMCP,
    tool_name: str,
    expected_type: type,
    expected_value: Any,
):
    """Task mode returns basic types correctly."""
    async with Client(return_type_server) as client:
        task = await client.call_tool(tool_name, task=True)
        result = await task
        assert isinstance(result.data, expected_type)
        assert result.data == expected_value


async def test_task_model_return(return_type_server):
    """Task mode returns same BaseModel (as dict) as immediate mode."""
    async with Client(return_type_server) as client:
        task = await client.call_tool("return_model", task=True)
        result = await task

        # Client deserializes to dynamic class (type name lost with title pruning)
        assert result.data.__class__.__name__ == "Root"
        assert result.data.name == "Alice"
        assert result.data.age == 30
        assert result.data.active is True


async def test_task_vs_immediate_equivalence(return_type_server):
    """Verify task mode and immediate mode return identical results."""
    async with Client(return_type_server) as client:
        # Test a few types to verify equivalence
        tools_to_test = ["return_string", "return_int", "return_dict"]

        for tool_name in tools_to_test:
            # Call as task
            task = await client.call_tool(tool_name, task=True)
            task_result = await task

            # Call immediately (server should decline background execution when no task meta)
            immediate_result = await client.call_tool(tool_name)

            # Results should be identical
            assert task_result.data == immediate_result.data, (
                f"Mismatch for {tool_name}"
            )


@pytest.fixture
async def prompt_return_server():
    """Server with prompts that return various message structures."""
    mcp = FastMCP("prompt-return-test")

    @mcp.prompt(task=True)
    async def single_message_prompt() -> str:
        """Return a single string message."""
        return "Single message content"

    @mcp.prompt(task=True)
    async def multi_message_prompt() -> list[str]:
        """Return multiple messages."""
        return [
            "First message",
            "Second message",
            "Third message",
        ]

    return mcp


async def test_prompt_task_single_message(prompt_return_server):
    """Prompt task returns single message correctly."""
    async with Client(prompt_return_server) as client:
        task = await client.get_prompt("single_message_prompt", task=True)
        result = await task

        assert len(result.messages) == 1
        assert result.messages[0].content.text == "Single message content"


async def test_prompt_task_multiple_messages(prompt_return_server):
    """Prompt task returns multiple messages correctly."""
    async with Client(prompt_return_server) as client:
        task = await client.get_prompt("multi_message_prompt", task=True)
        result = await task

        assert len(result.messages) == 3
        assert result.messages[0].content.text == "First message"
        assert result.messages[1].content.text == "Second message"
        assert result.messages[2].content.text == "Third message"


@pytest.fixture
async def resource_return_server():
    """Server with resources that return various content types."""
    mcp = FastMCP("resource-return-test")

    @mcp.resource("text://simple", task=True)
    async def simple_text() -> str:
        """Return simple text content."""
        return "Simple text resource"

    @mcp.resource("data://json", task=True)
    async def json_data() -> str:
        """Return JSON-like data."""
        import json

        return json.dumps({"key": "value", "count": 123})

    return mcp


async def test_resource_task_text_content(resource_return_server):
    """Resource task returns text content correctly."""
    async with Client(resource_return_server) as client:
        task = await client.read_resource("text://simple", task=True)
        contents = await task

        assert len(contents) == 1
        assert contents[0].text == "Simple text resource"


async def test_resource_task_json_content(resource_return_server):
    """Resource task returns structured content correctly."""
    async with Client(resource_return_server) as client:
        task = await client.read_resource("data://json", task=True)
        contents = await task

        # Content should be JSON serialized
        assert len(contents) == 1
        import json

        data = json.loads(contents[0].text)
        assert data == {"key": "value", "count": 123}


# ==============================================================================
# Binary & Special Types
# ==============================================================================


@pytest.fixture
async def binary_type_server():
    """Server with tools returning binary and special types."""
    mcp = FastMCP("binary-test")

    @mcp.tool(task=True)
    async def return_bytes() -> bytes:
        return b"Hello bytes!"

    @mcp.tool(task=True)
    async def return_uuid() -> UUID:
        return UUID("12345678-1234-5678-1234-567812345678")

    @mcp.tool(task=True)
    async def return_path() -> Path:
        return Path("/tmp/test.txt")

    @mcp.tool(task=True)
    async def return_datetime() -> datetime:
        return datetime(2025, 11, 5, 12, 30, 45)

    return mcp


@pytest.mark.parametrize(
    "tool_name,expected_type,assertion_fn",
    [
        (
            "return_bytes",
            str,
            lambda r: "Hello bytes!" in r.data or "SGVsbG8gYnl0ZXMh" in r.data,
        ),
        (
            "return_uuid",
            str,
            lambda r: r.data == "12345678-1234-5678-1234-567812345678",
        ),
        (
            "return_path",
            str,
            lambda r: "tmp" in r.data and "test.txt" in r.data,
        ),
        (
            "return_datetime",
            datetime,
            lambda r: r.data == datetime(2025, 11, 5, 12, 30, 45),
        ),
    ],
)
async def test_task_binary_types(
    binary_type_server: FastMCP,
    tool_name: str,
    expected_type: type,
    assertion_fn: Any,
):
    """Task mode handles binary and special types."""
    async with Client(binary_type_server) as client:
        task = await client.call_tool(tool_name, task=True)
        result = await task
        assert isinstance(result.data, expected_type)
        assert assertion_fn(result)


# ==============================================================================
# Collection Varieties
# ==============================================================================


@pytest.fixture
async def collection_server():
    """Server with tools returning various collection types."""
    mcp = FastMCP("collection-test")

    @mcp.tool(task=True)
    async def return_tuple() -> tuple[int, str, bool]:
        return (42, "hello", True)

    @mcp.tool(task=True)
    async def return_set() -> set[int]:
        return {1, 2, 3}

    @mcp.tool(task=True)
    async def return_empty_list() -> list[str]:
        return []

    @mcp.tool(task=True)
    async def return_empty_dict() -> dict[str, Any]:
        return {}

    return mcp


@pytest.mark.parametrize(
    "tool_name,expected_type,expected_value",
    [
        ("return_tuple", list, [42, "hello", True]),
        ("return_set", set, {1, 2, 3}),
        ("return_empty_list", list, []),
    ],
)
async def test_task_collection_types(
    collection_server: FastMCP,
    tool_name: str,
    expected_type: type,
    expected_value: Any,
):
    """Task mode handles collection types."""
    async with Client(collection_server) as client:
        task = await client.call_tool(tool_name, task=True)
        result = await task
        assert isinstance(result.data, expected_type)
        assert result.data == expected_value


async def test_task_empty_dict_return(collection_server):
    """Task mode handles empty dict return."""
    async with Client(collection_server) as client:
        task = await client.call_tool("return_empty_dict", task=True)
        result = await task
        # Empty structured content becomes None in data
        assert result.data is None
        # But structured content is still {}
        assert result.structured_content == {}


# ==============================================================================
# Media Types (Image, Audio, File)
# ==============================================================================


@pytest.fixture
async def media_server(tmp_path):
    """Server with tools returning media types."""
    mcp = FastMCP("media-test")

    # Create test files
    test_image = tmp_path / "test.png"
    test_image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake png data")

    test_audio = tmp_path / "test.mp3"
    test_audio.write_bytes(b"ID3" + b"fake mp3 data")

    test_file = tmp_path / "test.txt"
    test_file.write_text("test file content")

    @mcp.tool(task=True)
    async def return_image_path() -> Image:
        return Image(path=str(test_image))

    @mcp.tool(task=True)
    async def return_image_data() -> Image:
        return Image(data=test_image.read_bytes(), format="png")

    @mcp.tool(task=True)
    async def return_audio() -> Audio:
        return Audio(path=str(test_audio))

    @mcp.tool(task=True)
    async def return_file() -> File:
        return File(path=str(test_file))

    return mcp


@pytest.mark.parametrize(
    "tool_name,assertion_fn",
    [
        (
            "return_image_path",
            lambda r: len(r.content) == 1 and r.content[0].type == "image",
        ),
        (
            "return_image_data",
            lambda r: len(r.content) == 1
            and r.content[0].type == "image"
            and r.content[0].mimeType == "image/png",
        ),
        (
            "return_audio",
            lambda r: len(r.content) == 1 and r.content[0].type in ["text", "audio"],
        ),
        (
            "return_file",
            lambda r: len(r.content) == 1 and r.content[0].type == "resource",
        ),
    ],
)
async def test_task_media_types(
    media_server: FastMCP,
    tool_name: str,
    assertion_fn: Any,
):
    """Task mode handles media types (Image, Audio, File)."""
    async with Client(media_server) as client:
        task = await client.call_tool(tool_name, task=True)
        result = await task
        assert assertion_fn(result)


# ==============================================================================
# Structured Types (TypedDict, dataclass, unions)
# ==============================================================================


class PersonTypedDict(TypedDict):
    """Example TypedDict."""

    name: str
    age: int


@dataclass
class PersonDataclass:
    """Example dataclass."""

    name: str
    age: int


@pytest.fixture
async def structured_type_server():
    """Server with tools returning structured types."""
    mcp = FastMCP("structured-test")

    @mcp.tool(task=True)
    async def return_typeddict() -> PersonTypedDict:
        return {"name": "Bob", "age": 25}

    @mcp.tool(task=True)
    async def return_dataclass() -> PersonDataclass:
        return PersonDataclass(name="Charlie", age=35)

    @mcp.tool(task=True)
    async def return_union() -> str | int:
        return "string value"

    @mcp.tool(task=True)
    async def return_union_int() -> str | int:
        return 123

    @mcp.tool(task=True)
    async def return_optional() -> str | None:
        return "has value"

    @mcp.tool(task=True)
    async def return_optional_none() -> str | None:
        return None

    return mcp


@pytest.mark.parametrize(
    "tool_name,expected_name,expected_age",
    [
        ("return_typeddict", "Bob", 25),
        ("return_dataclass", "Charlie", 35),
    ],
)
async def test_task_structured_dict_types(
    structured_type_server: FastMCP,
    tool_name: str,
    expected_name: str,
    expected_age: int,
):
    """Task mode handles TypedDict and dataclass returns."""
    async with Client(structured_type_server) as client:
        task = await client.call_tool(tool_name, task=True)
        result = await task
        # Both deserialize to dynamic Root class
        assert result.data.name == expected_name
        assert result.data.age == expected_age


@pytest.mark.parametrize(
    "tool_name,expected_type,expected_value",
    [
        ("return_union", str, "string value"),
        ("return_union_int", int, 123),
    ],
)
async def test_task_union_types(
    structured_type_server: FastMCP,
    tool_name: str,
    expected_type: type,
    expected_value: Any,
):
    """Task mode handles union type branches."""
    async with Client(structured_type_server) as client:
        task = await client.call_tool(tool_name, task=True)
        result = await task
        assert isinstance(result.data, expected_type)
        assert result.data == expected_value


@pytest.mark.parametrize(
    "tool_name,expected_type,expected_value",
    [
        ("return_optional", str, "has value"),
        ("return_optional_none", type(None), None),
    ],
)
async def test_task_optional_types(
    structured_type_server: FastMCP,
    tool_name: str,
    expected_type: type,
    expected_value: Any,
):
    """Task mode handles Optional types."""
    async with Client(structured_type_server) as client:
        task = await client.call_tool(tool_name, task=True)
        result = await task
        assert isinstance(result.data, expected_type)
        assert result.data == expected_value


# ==============================================================================
# MCP Content Blocks
# ==============================================================================


@pytest.fixture
async def mcp_content_server(tmp_path):
    """Server with tools returning MCP content blocks."""
    import base64

    from mcp.types import (
        AnyUrl,
        EmbeddedResource,
        ImageContent,
        ResourceLink,
        TextContent,
        TextResourceContents,
    )

    mcp = FastMCP("content-test")

    test_image = tmp_path / "content.png"
    test_image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"content")

    @mcp.tool(task=True)
    async def return_text_content() -> TextContent:
        return TextContent(type="text", text="Direct text content")

    @mcp.tool(task=True)
    async def return_image_content() -> ImageContent:
        return ImageContent(
            type="image",
            data=base64.b64encode(test_image.read_bytes()).decode(),
            mimeType="image/png",
        )

    @mcp.tool(task=True)
    async def return_embedded_resource() -> EmbeddedResource:
        return EmbeddedResource(
            type="resource",
            resource=TextResourceContents(
                uri=AnyUrl("test://resource"), text="embedded"
            ),
        )

    @mcp.tool(task=True)
    async def return_resource_link() -> ResourceLink:
        return ResourceLink(
            type="resource_link", uri=AnyUrl("test://linked"), name="Test Resource"
        )

    @mcp.tool(task=True)
    async def return_mixed_content() -> list[TextContent | ImageContent]:
        return [
            TextContent(type="text", text="First block"),
            ImageContent(
                type="image",
                data=base64.b64encode(test_image.read_bytes()).decode(),
                mimeType="image/png",
            ),
            TextContent(type="text", text="Third block"),
        ]

    return mcp


@pytest.mark.parametrize(
    "tool_name,assertion_fn",
    [
        (
            "return_text_content",
            lambda r: len(r.content) == 1
            and r.content[0].type == "text"
            and r.content[0].text == "Direct text content",
        ),
        (
            "return_image_content",
            lambda r: len(r.content) == 1
            and r.content[0].type == "image"
            and r.content[0].mimeType == "image/png",
        ),
        (
            "return_embedded_resource",
            lambda r: len(r.content) == 1 and r.content[0].type == "resource",
        ),
        (
            "return_resource_link",
            lambda r: len(r.content) == 1
            and r.content[0].type == "resource_link"
            and str(r.content[0].uri) == "test://linked",
        ),
    ],
)
async def test_task_mcp_content_types(
    mcp_content_server: FastMCP,
    tool_name: str,
    assertion_fn: Any,
):
    """Task mode handles MCP content block types."""
    async with Client(mcp_content_server) as client:
        task = await client.call_tool(tool_name, task=True)
        result = await task
        assert assertion_fn(result)


async def test_task_mixed_content_return(mcp_content_server):
    """Task mode handles mixed content list return."""
    async with Client(mcp_content_server) as client:
        task = await client.call_tool("return_mixed_content", task=True)
        result = await task
        assert len(result.content) == 3
        assert result.content[0].type == "text"
        assert result.content[0].text == "First block"
        assert result.content[1].type == "image"
        assert result.content[2].type == "text"
        assert result.content[2].text == "Third block"
