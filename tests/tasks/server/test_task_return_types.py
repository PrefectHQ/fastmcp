"""
Tests to verify all tool return types work identically with task=True.

SEP-2663 tasks are tools-only. Every tool below is exercised twice: once
synchronously (no tasks opt-in) and once as a background task. Both paths run
the same `tool.convert_result(...).to_mcp_result()` pipeline, so the inlined
task result must be byte-for-byte identical to the synchronous result. These
tests assert that equivalence across every supported return type.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import mcp_types
import pytest
from docket import Docket
from pydantic import BaseModel
from typing_extensions import TypedDict

from fastmcp import FastMCP
from fastmcp.tools.base import ToolResult
from fastmcp.utilities.types import Audio, File, Image
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import (
    call_tool_without_optin,
    run_task,
    running_task_server,
)


@pytest.fixture(autouse=True)
def reset_docket_memory_server():
    """Force a fresh memory:// Docket server bound to each test's event loop."""
    if hasattr(Docket, "_memory_server"):
        delattr(Docket, "_memory_server")
    yield
    if hasattr(Docket, "_memory_server"):
        delattr(Docket, "_memory_server")


def _sync_result_to_wire(result: ToolResult) -> dict[str, Any]:
    """Serialize a synchronous ToolResult into the inlined task wire shape."""
    mcp_result = result.to_mcp_result()
    if isinstance(mcp_result, mcp_types.CallToolResult):
        call_tool_result = mcp_result
    elif isinstance(mcp_result, tuple):
        content, structured_content = mcp_result
        call_tool_result = mcp_types.CallToolResult(
            content=content,
            structuredContent=structured_content,
        )
    else:
        call_tool_result = mcp_types.CallToolResult(content=mcp_result)
    return call_tool_result.model_dump(by_alias=True, mode="json", exclude_none=True)


async def assert_task_matches_sync(
    server: FastMCP,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> None:
    """Run a tool sync and as a task; assert the inlined results are identical."""
    async with running_task_server(server):
        sync_result = await call_tool_without_optin(server, tool_name, arguments)
        assert isinstance(sync_result, ToolResult)

        task_result = await run_task(server, tool_name, arguments)
        assert task_result.status == "completed"
        assert task_result.result is not None

        assert task_result.result == _sync_result_to_wire(sync_result)


class UserData(BaseModel):
    """Example structured output."""

    name: str
    age: int
    active: bool


# ==============================================================================
# Basic Types
# ==============================================================================


@pytest.fixture
def return_type_server():
    """Server with tools that return various basic types."""
    mcp = FastMCP("return-type-test")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def return_string() -> str:
        return "Hello, World!"

    @mcp.tool(task=True)
    async def return_int() -> int:
        return 42

    @mcp.tool(task=True)
    async def return_float() -> float:
        return 3.14159

    @mcp.tool(task=True)
    async def return_bool() -> bool:
        return True

    @mcp.tool(task=True)
    async def return_dict() -> dict[str, int]:
        return {"count": 100, "total": 500}

    @mcp.tool(task=True)
    async def return_list() -> list[str]:
        return ["apple", "banana", "cherry"]

    @mcp.tool(task=True)
    async def return_model() -> UserData:
        return UserData(name="Alice", age=30, active=True)

    @mcp.tool(task=True)
    async def return_none() -> None:
        return None

    return mcp


@pytest.mark.parametrize(
    "tool_name",
    [
        "return_string",
        "return_int",
        "return_float",
        "return_bool",
        "return_dict",
        "return_list",
        "return_model",
        "return_none",
    ],
)
async def test_task_basic_types_match_sync(
    return_type_server: FastMCP,
    tool_name: str,
):
    """Task mode returns basic types identically to the synchronous path."""
    await assert_task_matches_sync(return_type_server, tool_name)


# ==============================================================================
# Binary & Special Types
# ==============================================================================


@pytest.fixture
def binary_type_server(tmp_path):
    """Server with tools returning binary and special types."""
    mcp = FastMCP("binary-test")
    mcp.add_extension(TasksExtension())

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
    "tool_name",
    ["return_bytes", "return_uuid", "return_path", "return_datetime"],
)
async def test_task_binary_types_match_sync(
    binary_type_server: FastMCP,
    tool_name: str,
):
    """Task mode handles binary and special types identically to sync."""
    await assert_task_matches_sync(binary_type_server, tool_name)


# ==============================================================================
# Collection Varieties
# ==============================================================================


@pytest.fixture
def collection_server():
    """Server with tools returning various collection types."""
    mcp = FastMCP("collection-test")
    mcp.add_extension(TasksExtension())

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
    "tool_name",
    ["return_tuple", "return_set", "return_empty_list", "return_empty_dict"],
)
async def test_task_collection_types_match_sync(
    collection_server: FastMCP,
    tool_name: str,
):
    """Task mode handles collection types identically to sync."""
    await assert_task_matches_sync(collection_server, tool_name)


# ==============================================================================
# Media Types (Image, Audio, File)
# ==============================================================================


@pytest.fixture
def media_server(tmp_path):
    """Server with tools returning media types."""
    mcp = FastMCP("media-test")
    mcp.add_extension(TasksExtension())

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
    "tool_name",
    ["return_image_path", "return_image_data", "return_audio", "return_file"],
)
async def test_task_media_types_match_sync(
    media_server: FastMCP,
    tool_name: str,
):
    """Task mode handles media types (Image, Audio, File) identically to sync."""
    await assert_task_matches_sync(media_server, tool_name)


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
def structured_type_server():
    """Server with tools returning structured types."""
    mcp = FastMCP("structured-test")
    mcp.add_extension(TasksExtension())

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
    "tool_name",
    [
        "return_typeddict",
        "return_dataclass",
        "return_union",
        "return_union_int",
        "return_optional",
        "return_optional_none",
    ],
)
async def test_task_structured_types_match_sync(
    structured_type_server: FastMCP,
    tool_name: str,
):
    """Task mode handles TypedDict, dataclass, union and optional returns."""
    await assert_task_matches_sync(structured_type_server, tool_name)


# ==============================================================================
# MCP Content Blocks
# ==============================================================================


@pytest.fixture
def mcp_content_server(tmp_path):
    """Server with tools returning MCP content blocks."""
    import base64

    from mcp_types import (
        EmbeddedResource,
        ImageContent,
        ResourceLink,
        TextContent,
        TextResourceContents,
    )

    mcp = FastMCP("content-test")
    mcp.add_extension(TasksExtension())

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
            mime_type="image/png",
        )

    @mcp.tool(task=True)
    async def return_embedded_resource() -> EmbeddedResource:
        return EmbeddedResource(
            type="resource",
            resource=TextResourceContents(uri="test://resource", text="embedded"),
        )

    @mcp.tool(task=True)
    async def return_resource_link() -> ResourceLink:
        return ResourceLink(
            type="resource_link", uri="test://linked", name="Test Resource"
        )

    @mcp.tool(task=True)
    async def return_mixed_content() -> list[TextContent | ImageContent]:
        return [
            TextContent(type="text", text="First block"),
            ImageContent(
                type="image",
                data=base64.b64encode(test_image.read_bytes()).decode(),
                mime_type="image/png",
            ),
            TextContent(type="text", text="Third block"),
        ]

    return mcp


@pytest.mark.parametrize(
    "tool_name",
    [
        "return_text_content",
        "return_image_content",
        "return_embedded_resource",
        "return_resource_link",
        "return_mixed_content",
    ],
)
async def test_task_mcp_content_types_match_sync(
    mcp_content_server: FastMCP,
    tool_name: str,
):
    """Task mode handles MCP content block types identically to sync."""
    await assert_task_matches_sync(mcp_content_server, tool_name)
