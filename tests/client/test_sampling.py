import json
from typing import cast
from unittest.mock import AsyncMock

import pytest
from mcp.types import TextContent
from pydantic_core import to_json

from fastmcp import Client, Context, FastMCP
from fastmcp.client.sampling import RequestContext, SamplingMessage, SamplingParams
from fastmcp.server.sampling import SamplingResult, SamplingTool
from fastmcp.utilities.types import Image


@pytest.fixture
def fastmcp_server():
    mcp = FastMCP()

    @mcp.tool
    async def simple_sample(message: str, context: Context) -> str:
        result = await context.sample("Hello, world!")
        assert isinstance(result, SamplingResult)
        assert result.text is not None
        return result.text

    @mcp.tool
    async def sample_with_system_prompt(message: str, context: Context) -> str:
        result = await context.sample("Hello, world!", system_prompt="You love FastMCP")
        assert isinstance(result, SamplingResult)
        assert result.text is not None
        return result.text

    @mcp.tool
    async def sample_with_messages(message: str, context: Context) -> str:
        result = await context.sample(
            [
                "Hello!",
                SamplingMessage(
                    content=TextContent(
                        type="text", text="How can I assist you today?"
                    ),
                    role="assistant",
                ),
            ]
        )
        assert isinstance(result, SamplingResult)
        assert result.text is not None
        return result.text

    @mcp.tool
    async def sample_with_image(image_bytes: bytes, context: Context) -> str:
        image = Image(data=image_bytes)

        result = await context.sample(
            [
                SamplingMessage(
                    content=TextContent(type="text", text="What's in this image?"),
                    role="user",
                ),
                SamplingMessage(
                    content=image.to_image_content(),
                    role="user",
                ),
            ]
        )
        assert isinstance(result, SamplingResult)
        assert result.text is not None
        return result.text

    return mcp


async def test_simple_sampling(fastmcp_server: FastMCP):
    def sampling_handler(
        messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
    ) -> str:
        return "This is the sample message!"

    async with Client(fastmcp_server, sampling_handler=sampling_handler) as client:
        result = await client.call_tool("simple_sample", {"message": "Hello, world!"})
        assert result.data == "This is the sample message!"


async def test_sampling_with_system_prompt(fastmcp_server: FastMCP):
    def sampling_handler(
        messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
    ) -> str:
        assert params.systemPrompt is not None
        return params.systemPrompt

    async with Client(fastmcp_server, sampling_handler=sampling_handler) as client:
        result = await client.call_tool(
            "sample_with_system_prompt", {"message": "Hello, world!"}
        )
        assert result.data == "You love FastMCP"


async def test_sampling_with_messages(fastmcp_server: FastMCP):
    def sampling_handler(
        messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
    ) -> str:
        assert len(messages) == 2

        assert isinstance(messages[0].content, TextContent)
        assert messages[0].content.type == "text"
        assert messages[0].content.text == "Hello!"

        assert isinstance(messages[1].content, TextContent)
        assert messages[1].content.type == "text"
        assert messages[1].content.text == "How can I assist you today?"
        return "I need to think."

    async with Client(fastmcp_server, sampling_handler=sampling_handler) as client:
        result = await client.call_tool(
            "sample_with_messages", {"message": "Hello, world!"}
        )
        assert result.data == "I need to think."


async def test_sampling_with_fallback(fastmcp_server: FastMCP):
    openai_sampling_handler = AsyncMock(return_value="But I need to think")

    fastmcp_server = FastMCP(
        sampling_handler=openai_sampling_handler,
    )

    @fastmcp_server.tool
    async def sample_with_fallback(context: Context) -> str:
        sampling_result = await context.sample("Do not think.")
        return cast(TextContent, sampling_result).text

    client = Client(fastmcp_server)

    async with client:
        call_tool_result = await client.call_tool("sample_with_fallback")

    assert call_tool_result.data == "But I need to think"


async def test_sampling_with_image(fastmcp_server: FastMCP):
    def sampling_handler(
        messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
    ) -> str:
        assert len(messages) == 2
        return to_json(messages).decode()

    async with Client(fastmcp_server, sampling_handler=sampling_handler) as client:
        image_bytes = b"abc123"
        result = await client.call_tool(
            "sample_with_image", {"image_bytes": image_bytes}
        )
        assert json.loads(result.data) == [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": "What's in this image?",
                    "annotations": None,
                    "_meta": None,
                },
                "_meta": None,
            },
            {
                "role": "user",
                "content": {
                    "type": "image",
                    "data": "YWJjMTIz",
                    "mimeType": "image/png",
                    "annotations": None,
                    "_meta": None,
                },
                "_meta": None,
            },
        ]


class TestSamplingDefaultCapabilities:
    """Tests for default sampling capability advertisement (issue #3329)."""

    async def test_default_sampling_capabilities_omit_tools(self):
        """Default sampling capabilities should not include tools field.

        When serialized with exclude_none=True (as the MCP session does),
        the capability should produce {"sampling": {}} rather than
        {"sampling": {"tools": {}}}, ensuring compatibility with servers
        that don't recognize the tools sub-field (e.g. older Java MCP SDK).
        """
        import mcp.types as mcp_types

        server = FastMCP()

        def handler(
            messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
        ) -> str:
            return "ok"

        client = Client(server, sampling_handler=handler)
        caps = client._session_kwargs["sampling_capabilities"]
        assert isinstance(caps, mcp_types.SamplingCapability)
        assert caps.tools is None

    async def test_set_sampling_callback_default_capabilities_omit_tools(self):
        """set_sampling_callback should also default to no tools capability."""
        import mcp.types as mcp_types

        server = FastMCP()
        client = Client(server)
        client.set_sampling_callback(lambda msgs, params, ctx: "ok")
        caps = client._session_kwargs["sampling_capabilities"]
        assert isinstance(caps, mcp_types.SamplingCapability)
        assert caps.tools is None

    async def test_explicit_tools_capability_is_preserved(self):
        """Explicitly passing tools capability should be respected."""
        import mcp.types as mcp_types

        server = FastMCP()

        def handler(
            messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
        ) -> str:
            return "ok"

        explicit_caps = mcp_types.SamplingCapability(
            tools=mcp_types.SamplingToolsCapability()
        )
        client = Client(
            server, sampling_handler=handler, sampling_capabilities=explicit_caps
        )
        caps = client._session_kwargs["sampling_capabilities"]
        assert isinstance(caps, mcp_types.SamplingCapability)
        assert caps.tools is not None


class TestSamplingWithTools:
    """Tests for sampling with tools functionality."""

    async def test_sampling_with_tools_requires_capability(self):
        """Test that sampling with tools raises error when client lacks capability."""
        import mcp.types as mcp_types

        from fastmcp.exceptions import ToolError

        server = FastMCP()

        def search(query: str) -> str:
            """Search the web."""
            return f"Results for: {query}"

        @server.tool
        async def sample_with_tool(context: Context) -> str:
            # This should fail because the client doesn't advertise tools capability
            result = await context.sample(
                messages="Search for Python tutorials",
                tools=[search],
            )
            return str(result)

        def sampling_handler(
            messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
        ) -> str:
            return "Response"

        # Explicitly disable tools capability by passing SamplingCapability without tools
        async with Client(
            server,
            sampling_handler=sampling_handler,
            sampling_capabilities=mcp_types.SamplingCapability(),  # No tools
        ) as client:
            with pytest.raises(ToolError, match="sampling.tools capability"):
                await client.call_tool("sample_with_tool", {})

    async def test_sampling_with_tools_fallback_handler_can_return_string(self):
        """Test that fallback handler can return a string even when tools are provided.

        The LLM might choose not to use any tools and just return a text response.
        """
        # This handler returns a string - valid even when tools are provided
        simple_handler = AsyncMock(return_value="Direct response without tools")

        mcp = FastMCP(sampling_handler=simple_handler)

        def search(query: str) -> str:
            """Search the web."""
            return f"Results for: {query}"

        @mcp.tool
        async def sample_with_tool(context: Context) -> str:
            result = await context.sample(
                messages="Search for Python tutorials",
                tools=[search],
            )
            return result.text or "no text"

        # Client without sampling handler - will use server's fallback
        async with Client(mcp) as client:
            result = await client.call_tool("sample_with_tool", {})

        # Handler returned string directly, which is treated as final text response
        assert result.data == "Direct response without tools"

    def test_sampling_tool_schema(self):
        """Test that SamplingTool generates correct schema."""

        def search(query: str, limit: int = 10) -> str:
            """Search the web for results."""
            return f"Results for: {query}"

        tool = SamplingTool.from_function(search)
        assert tool.name == "search"
        assert tool.description == "Search the web for results."
        assert "query" in tool.parameters.get("properties", {})
        assert "limit" in tool.parameters.get("properties", {})

    async def test_sampling_tool_run(self):
        """Test that SamplingTool.run() executes correctly."""

        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        tool = SamplingTool.from_function(add)
        result = await tool.run({"a": 5, "b": 3})
        assert result == 8

    async def test_sampling_tool_run_async(self):
        """Test that SamplingTool.run() works with async functions."""

        async def async_multiply(a: int, b: int) -> int:
            """Multiply two numbers."""
            return a * b

        tool = SamplingTool.from_function(async_multiply)
        result = await tool.run({"a": 4, "b": 7})
        assert result == 28

    def test_tool_choice_parameter(self):
        """Test that tool_choice parameter accepts string literals."""
        from fastmcp.server.context import ToolChoiceOption

        # Verify ToolChoiceOption type accepts the valid string values
        choices: list[ToolChoiceOption] = ["auto", "required", "none"]
        assert len(choices) == 3
        assert "auto" in choices
        assert "required" in choices
        assert "none" in choices
