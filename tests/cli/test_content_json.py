"""CLI JSON retains supported MCP content, wire aliases, and metadata."""

import json
from typing import Any

import mcp_types as t
import pytest

from fastmcp import Client, FastMCP
from fastmcp.cli.client import (
    _call_result_to_dict,
    _content_block_to_dict,
    _handle_prompt,
)
from fastmcp.prompts import Message, PromptResult


@pytest.mark.parametrize(
    "block,expected",
    [
        (t.TextContent(type="text", text=""), {"type": "text", "text": ""}),
        (
            t.ImageContent(type="image", data="YQ==", mime_type="image/png"),
            {"type": "image", "data": "YQ==", "mimeType": "image/png"},
        ),
        (
            t.AudioContent(type="audio", data="YQ==", mime_type="audio/wav"),
            {"type": "audio", "data": "YQ==", "mimeType": "audio/wav"},
        ),
        (
            t.ResourceLink(
                type="resource_link", name="r", uri="https://example.test/x", size=0
            ),
            {
                "type": "resource_link",
                "name": "r",
                "uri": "https://example.test/x",
                "size": 0,
            },
        ),
        (
            t.EmbeddedResource(
                type="resource",
                resource=t.TextResourceContents(
                    uri="file:///tmp/a%20b", text="", meta={"source": "prompt"}
                ),
            ),
            {
                "type": "resource",
                "resource": {
                    "uri": "file:///tmp/a%20b",
                    "text": "",
                    "_meta": {"source": "prompt"},
                },
            },
        ),
        (
            t.EmbeddedResource(
                type="resource",
                resource=t.BlobResourceContents(
                    uri="file:///tmp/image", mime_type="image/png", blob="YQ=="
                ),
            ),
            {
                "type": "resource",
                "resource": {
                    "uri": "file:///tmp/image",
                    "mimeType": "image/png",
                    "blob": "YQ==",
                },
            },
        ),
    ],
)
def test_content_variants(block: t.ContentBlock, expected: dict[str, Any]) -> None:
    assert json.loads(json.dumps(_content_block_to_dict(block))) == expected


@pytest.mark.parametrize(
    "kind", ["text", "image", "audio", "resource_link", "resource"]
)
def test_annotations_and_metadata(kind: str) -> None:
    payload: dict[str, Any] = {
        "type": kind,
        "annotations": {
            "audience": [],
            "priority": 0,
            "lastModified": "2026-10-30T00:00:00Z",
        },
        "_meta": {"nullable": None, "false": False, "zero": 0, "list": [None]},
    }
    payload.update(
        {
            "text": {"text": ""},
            "image": {"data": "YQ==", "mimeType": "image/png"},
            "audio": {"data": "YQ==", "mimeType": "audio/wav"},
            "resource_link": {"name": "r", "uri": "https://example.test/x"},
            "resource": {"resource": {"uri": "file:///tmp/a", "text": "hello"}},
        }[kind]
    )
    block = t.CallToolResult.model_validate({"content": [payload]}).content[0]
    actual = _content_block_to_dict(block)
    assert actual == payload
    assert type(actual["_meta"]["false"]) is bool
    assert type(actual["_meta"]["zero"]) is int


async def test_tool_json_preserves_resource() -> None:
    server = FastMCP()

    @server.tool
    def linked() -> t.ResourceLink:
        return t.ResourceLink(
            type="resource_link",
            name="data",
            uri="https://example.test/data",
            meta={"source": "tool"},
        )

    async with Client(server) as client:
        result = _call_result_to_dict(await client.call_tool("linked"))
    assert result["content"] == [
        {
            "type": "resource_link",
            "name": "data",
            "uri": "https://example.test/data",
            "_meta": {"source": "tool"},
        }
    ]
    assert result["is_error"] is False


async def test_prompt_json_preserves_embedded_resource(
    capsys: pytest.CaptureFixture[str],
) -> None:
    server = FastMCP()

    @server.prompt
    def document() -> PromptResult:
        resource = t.EmbeddedResource(
            type="resource",
            resource=t.TextResourceContents(
                uri="file:///tmp/doc", text="hello", meta={"source": "prompt"}
            ),
        )
        return PromptResult(messages=[Message(role="user", content=resource)])

    async with Client(server) as client:
        await _handle_prompt(client, "document", (), None, True)
    assert json.loads(capsys.readouterr().out) == {
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "resource",
                    "resource": {
                        "uri": "file:///tmp/doc",
                        "text": "hello",
                        "_meta": {"source": "prompt"},
                    },
                },
            }
        ]
    }
