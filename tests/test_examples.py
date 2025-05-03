"""Tests for example servers"""

import pytest
from mcp.shared.memory import (
    create_connected_server_and_client_session as client_session,
)
from mcp.types import (
    PromptMessage,
    ReadResourceResult,
    TextContent,
    TextResourceContents,
)


@pytest.mark.anyio
async def test_simple_echo():
    """Test the simple echo server"""
    from examples.simple_echo import mcp

    async with client_session(mcp._mcp_server) as client:
        result = await client.call_tool("echo", {"text": "hello"})
        assert len(result.content) == 1
        content = result.content[0]
        assert isinstance(content, TextContent)
        assert content.text == "hello"


@pytest.mark.anyio
async def test_complex_inputs():
    """Test the complex inputs server"""
    from examples.complex_inputs import mcp

    async with client_session(mcp._mcp_server) as client:
        tank = {"shrimp": [{"name": "bob"}, {"name": "alice"}]}
        result = await client.call_tool(
            "name_shrimp", {"tank": tank, "extra_names": ["charlie"]}
        )
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == '[\n  "bob",\n  "alice",\n  "charlie"\n]'


@pytest.mark.anyio
async def test_desktop(monkeypatch):
    """Test the desktop server"""
    from pathlib import Path

    from pydantic import AnyUrl

    from examples.desktop import mcp

    # Mock desktop directory listing
    mock_files = [Path("/fake/path/file1.txt"), Path("/fake/path/file2.txt")]
    monkeypatch.setattr(Path, "iterdir", lambda self: mock_files)
    monkeypatch.setattr(Path, "home", lambda: Path("/fake/home"))

    async with client_session(mcp._mcp_server) as client:
        # Test the add function
        result = await client.call_tool("add", {"a": 1, "b": 2})
        assert len(result.content) == 1
        content = result.content[0]
        assert isinstance(content, TextContent)
        assert content.text == "3"

    async with client_session(mcp._mcp_server) as client:
        result = await client.read_resource("greeting://rooter12")
        assert len(result.contents) == 1
        content = result.contents[0]
        assert isinstance(result, ReadResourceResult)
        assert content.text == "Hello, rooter12!"

        # Test the desktop resource
        result = await client.read_resource(AnyUrl("dir://desktop"))
        assert len(result.contents) == 1
        content = result.contents[0]
        assert isinstance(content, TextResourceContents)
        assert isinstance(content.text, str)
        assert "/fake/path/file1.txt" in content.text
        assert "/fake/path/file2.txt" in content.text


@pytest.mark.anyio
async def test_echo():
    """Test the echo server"""
    from examples.echo import mcp

    async with client_session(mcp._mcp_server) as client:
        result = await client.call_tool("echo_tool", {"text": "hello"})
        assert len(result.content) == 1
        content = result.content[0]
        assert isinstance(content, TextContent)
        assert content.text == "hello"

    async with client_session(mcp._mcp_server) as client:
        result = await client.read_resource("echo://static")
        assert len(result.contents) == 1
        content = result.contents[0]
        assert isinstance(result, ReadResourceResult)
        assert content.text == 'Echo!'

    async with client_session(mcp._mcp_server) as client:
        result = await client.read_resource("echo://server42")
        assert len(result.contents) == 1
        content = result.contents[0]
        assert isinstance(result, ReadResourceResult)
        assert content.text == "Echo: server42"

    async with client_session(mcp._mcp_server) as client:
        result = await client.get_prompt("echo", {"text": "hello"})
        assert len(result.messages) == 1
        assert isinstance(content, TextResourceContents)
        assert isinstance(result.messages[0], PromptMessage)
        assert result.messages[0].content.text == 'hello'

