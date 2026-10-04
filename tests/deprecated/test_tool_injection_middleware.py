"""Tests for deprecated PromptToolMiddleware and ResourceToolMiddleware."""

from collections.abc import Callable

import pytest
from inline_snapshot import snapshot
from mcp.types import TextContent
from mcp.types import Tool as SDKTool

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.client import CallToolResult
from fastmcp.client.transports import FastMCPTransport
from fastmcp.server.auth import require_scopes
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.server.middleware import AuthMiddleware
from fastmcp.server.middleware.tool_injection import (
    PromptToolMiddleware,
    ResourceToolMiddleware,
    ToolInjectionMiddleware,
)
from fastmcp.utilities.tests import run_server_async


class TestPromptToolMiddleware:
    """Tests for PromptToolMiddleware."""

    @pytest.fixture
    def server_with_prompts(self):
        """Create a FastMCP server with prompts."""
        mcp = FastMCP("PromptServer")

        @mcp.tool
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        @mcp.prompt
        def greeting(name: str) -> str:
            """Generate a greeting message."""
            return f"Hello, {name}!"

        @mcp.prompt
        def farewell(name: str) -> str:
            """Generate a farewell message."""
            return f"Goodbye, {name}!"

        return mcp

    async def test_prompt_tools_added_to_list(self, server_with_prompts: FastMCP):
        """Test that prompt tools are added to the tool list."""
        middleware = PromptToolMiddleware()
        server_with_prompts.add_middleware(middleware)

        async with Client[FastMCPTransport](server_with_prompts) as client:
            tools: list[SDKTool] = await client.list_tools()

        tool_names: list[str] = [tool.name for tool in tools]
        # Should have: add, list_prompts, get_prompt
        assert len(tools) == 3
        assert "add" in tool_names
        assert "list_prompts" in tool_names
        assert "get_prompt" in tool_names

    async def test_list_prompts_tool_works(self, server_with_prompts: FastMCP):
        """Test that the list_prompts tool can be called."""
        middleware = PromptToolMiddleware()
        server_with_prompts.add_middleware(middleware)

        async with Client[FastMCPTransport](server_with_prompts) as client:
            result: CallToolResult = await client.call_tool(
                name="list_prompts", arguments={}
            )

        assert result.content == snapshot(
            [
                TextContent(
                    type="text",
                    text='[{"name":"greeting","title":null,"description":"Generate a greeting message.","arguments":[{"name":"name","description":null,"required":true}],"icons":null,"_meta":{"fastmcp":{"tags":[]}}},{"name":"farewell","title":null,"description":"Generate a farewell message.","arguments":[{"name":"name","description":null,"required":true}],"icons":null,"_meta":{"fastmcp":{"tags":[]}}}]',
                )
            ]
        )
        assert result.structured_content is not None
        assert result.structured_content["result"] == snapshot(
            [
                {
                    "name": "greeting",
                    "title": None,
                    "description": "Generate a greeting message.",
                    "arguments": [
                        {"name": "name", "description": None, "required": True}
                    ],
                    "icons": None,
                    "_meta": {"fastmcp": {"tags": []}},
                },
                {
                    "name": "farewell",
                    "title": None,
                    "description": "Generate a farewell message.",
                    "arguments": [
                        {"name": "name", "description": None, "required": True}
                    ],
                    "icons": None,
                    "_meta": {"fastmcp": {"tags": []}},
                },
            ]
        )

    async def test_get_prompt_tool_works(self, server_with_prompts: FastMCP):
        """Test that the get_prompt tool can be called."""
        middleware = PromptToolMiddleware()
        server_with_prompts.add_middleware(middleware)

        async with Client[FastMCPTransport](server_with_prompts) as client:
            result: CallToolResult = await client.call_tool(
                name="get_prompt",
                arguments={"name": "greeting", "arguments": {"name": "World"}},
            )

        # The tool returns the prompt result with structured_content
        assert result.content == snapshot(
            [
                TextContent(
                    type="text",
                    text='{"_meta":null,"description":"Generate a greeting message.","messages":[{"role":"user","content":{"type":"text","text":"Hello, World!","annotations":null,"_meta":null}}]}',
                )
            ]
        )
        assert result.structured_content is not None
        assert result.structured_content == snapshot(
            {
                "_meta": None,
                "description": "Generate a greeting message.",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": "Hello, World!",
                            "annotations": None,
                            "_meta": None,
                        },
                    }
                ],
            }
        )


class TestResourceToolMiddleware:
    """Tests for ResourceToolMiddleware."""

    @pytest.fixture
    def server_with_resources(self):
        """Create a FastMCP server with resources."""
        mcp = FastMCP("ResourceServer")

        @mcp.tool
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        @mcp.resource("file://config.txt")
        def config_resource() -> str:
            """Get configuration."""
            return "debug=true"

        @mcp.resource("file://data.json")
        def data_resource() -> str:
            """Get data."""
            return '{"count": 42}'

        return mcp

    async def test_resource_tools_added_to_list(self, server_with_resources: FastMCP):
        """Test that resource tools are added to the tool list."""
        middleware = ResourceToolMiddleware()
        server_with_resources.add_middleware(middleware)

        async with Client[FastMCPTransport](server_with_resources) as client:
            tools: list[SDKTool] = await client.list_tools()

        tool_names: list[str] = [tool.name for tool in tools]
        # Should have: add, list_resources, read_resource
        assert len(tools) == 3
        assert "add" in tool_names
        assert "list_resources" in tool_names
        assert "read_resource" in tool_names

    async def test_list_resources_tool_works(self, server_with_resources: FastMCP):
        """Test that the list_resources tool can be called."""
        middleware = ResourceToolMiddleware()
        server_with_resources.add_middleware(middleware)

        async with Client[FastMCPTransport](server_with_resources) as client:
            result: CallToolResult = await client.call_tool(
                name="list_resources", arguments={}
            )

        assert result.structured_content is not None
        assert result.structured_content["result"] == snapshot(
            [
                {
                    "name": "config_resource",
                    "title": None,
                    "uri": "file://config.txt/",
                    "description": "Get configuration.",
                    "mimeType": "text/plain",
                    "size": None,
                    "icons": None,
                    "annotations": None,
                    "_meta": {"fastmcp": {"tags": []}},
                },
                {
                    "name": "data_resource",
                    "title": None,
                    "uri": "file://data.json/",
                    "description": "Get data.",
                    "mimeType": "text/plain",
                    "size": None,
                    "icons": None,
                    "annotations": None,
                    "_meta": {"fastmcp": {"tags": []}},
                },
            ]
        )

    async def test_read_resource_tool_works(self, server_with_resources: FastMCP):
        """Test that the read_resource tool can be called."""
        middleware = ResourceToolMiddleware()
        server_with_resources.add_middleware(middleware)

        async with Client[FastMCPTransport](server_with_resources) as client:
            result: CallToolResult = await client.call_tool(
                name="read_resource", arguments={"uri": "file://config.txt"}
            )

        assert result.content == snapshot(
            [
                TextContent(
                    type="text",
                    text='{"contents":[{"content":"debug=true","mime_type":"text/plain","meta":null}],"meta":null}',
                )
            ]
        )
        assert result.structured_content == snapshot(
            {
                "contents": [
                    {"content": "debug=true", "mime_type": "text/plain", "meta": None}
                ],
                "meta": None,
            }
        )


TOKENS = {
    "read-token": {"client_id": "reader", "scopes": ["read"]},
    "admin-token": {"client_id": "administrator", "scopes": ["read", "admin"]},
}


class TestDeprecatedToolMiddlewareAuthorization:
    """AuthMiddleware applies to the tools these middleware add."""

    @pytest.mark.parametrize(
        "middleware_cls, tool_name",
        [
            (PromptToolMiddleware, "list_prompts"),
            (ResourceToolMiddleware, "list_resources"),
        ],
    )
    @pytest.mark.parametrize("injection_first", [True, False])
    @pytest.mark.parametrize(
        "token, allowed",
        [("read-token", False), ("admin-token", True)],
    )
    async def test_auth_middleware_applies_to_added_tools(
        self,
        middleware_cls: Callable[[], ToolInjectionMiddleware],
        tool_name: str,
        injection_first: bool,
        token: str,
        allowed: bool,
    ):
        injection = middleware_cls()
        auth = AuthMiddleware(auth=require_scopes("admin"))
        server = FastMCP(
            "AuthServer",
            auth=StaticTokenVerifier(TOKENS),
            middleware=[injection, auth] if injection_first else [auth, injection],
        )

        async with run_server_async(server) as url:
            async with Client(url, auth=token) as client:
                tools = await client.list_tools()
                result = await client.call_tool(tool_name, {}, raise_on_error=False)

        assert (tool_name in [tool.name for tool in tools]) is allowed
        assert result.is_error is not allowed
