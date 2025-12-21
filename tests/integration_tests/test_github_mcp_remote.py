import json
import os

import pytest
from mcp import McpError
from mcp.types import TextContent, Tool

from fastmcp import Client
from fastmcp.client import StreamableHttpTransport
from fastmcp.client.auth.bearer import BearerAuth

GITHUB_REMOTE_MCP_URL = "https://api.githubcopilot.com/mcp/"

HEADER_AUTHORIZATION = "Authorization"
FASTMCP_GITHUB_TOKEN = os.getenv("FASTMCP_GITHUB_TOKEN")


# Skip tests if no GitHub token is available
pytestmark = pytest.mark.xfail(
    not FASTMCP_GITHUB_TOKEN,
    reason="The FASTMCP_GITHUB_TOKEN environment variable is not set or empty",
)


@pytest.fixture(name="streamable_http_client")
def fixture_streamable_http_client() -> Client[StreamableHttpTransport]:
    assert FASTMCP_GITHUB_TOKEN is not None

    return Client(
        StreamableHttpTransport(
            url=GITHUB_REMOTE_MCP_URL,
            auth=BearerAuth(FASTMCP_GITHUB_TOKEN),
        )
    )


class TestGithubMCPRemote:
    async def test_connect_disconnect(
        self,
        streamable_http_client: Client[StreamableHttpTransport],
    ):
        async with streamable_http_client:
            assert streamable_http_client.is_connected() is True
            await streamable_http_client._disconnect()  # pylint: disable=W0212 (protected-access)
            assert streamable_http_client.is_connected() is False

    async def test_ping(self, streamable_http_client: Client[StreamableHttpTransport]):
        """Test pinging the server."""
        async with streamable_http_client:
            assert streamable_http_client.is_connected() is True
            result = await streamable_http_client.ping()
            assert result is True

    async def test_list_tools(
        self, streamable_http_client: Client[StreamableHttpTransport]
    ):
        """Test listing the MCP tools"""
        async with streamable_http_client:
            assert streamable_http_client.is_connected()
            tools = await streamable_http_client.list_tools()
            assert isinstance(tools, list)
            assert len(tools) > 0  # Ensure the tools list is non-empty
            for tool in tools:
                assert isinstance(tool, Tool)
                assert len(tool.name) > 0
                assert tool.description is not None and len(tool.description) > 0
                assert isinstance(tool.inputSchema, dict)
                assert len(tool.inputSchema) > 0

    async def test_list_resources(
        self, streamable_http_client: Client[StreamableHttpTransport]
    ):
        """Test listing the MCP resources"""
        async with streamable_http_client:
            assert streamable_http_client.is_connected()
            resources = await streamable_http_client.list_resources()
            assert isinstance(resources, list)
            assert len(resources) == 0

    async def test_list_prompts(
        self, streamable_http_client: Client[StreamableHttpTransport]
    ):
        """Test listing the MCP prompts"""
        async with streamable_http_client:
            assert streamable_http_client.is_connected()
            prompts = await streamable_http_client.list_prompts()
            # there is at least one prompt (as of July 2025)
            assert len(prompts) >= 1

    async def test_call_tool_ko(
        self, streamable_http_client: Client[StreamableHttpTransport]
    ):
        """Test calling a non-existing tool"""
        async with streamable_http_client:
            assert streamable_http_client.is_connected()
            with pytest.raises(McpError, match=r"unknown tool|tool not found"):
                await streamable_http_client.call_tool("foo")

    async def test_call_tool_list_commits(
        self,
        streamable_http_client: Client[StreamableHttpTransport],
    ):
        """Test calling a list_commit tool"""
        async with streamable_http_client:
            assert streamable_http_client.is_connected()
            result = await streamable_http_client.call_tool(
                "list_commits", {"owner": "jlowin", "repo": "fastmcp"}
            )

            # at this time, the github server does not support structured content
            assert result.structured_content is None
            assert isinstance(result.content, list)
            assert len(result.content) == 1
            assert isinstance(result.content[0], TextContent)
            commits = json.loads(result.content[0].text)
            for commit in commits:
                assert isinstance(commit, dict)
                assert "sha" in commit
                assert "commit" in commit
                assert "author" in commit["commit"]
                assert len(commit["commit"]["author"]["date"]) > 0
                assert len(commit["commit"]["author"]["name"]) > 0
                assert len(commit["commit"]["author"]["email"]) > 0
