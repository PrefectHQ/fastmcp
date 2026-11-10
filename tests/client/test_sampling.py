import json

import mcp_types
import pytest
from mcp_types import TextContent
from pydantic_core import to_json

from fastmcp import Client, Context, FastMCP
from fastmcp.client.sampling import RequestContext, SamplingMessage, SamplingParams
from fastmcp.utilities.types import Image


async def _sample(
    context: Context,
    messages: list[SamplingMessage],
    *,
    system_prompt: str | None = None,
) -> str:
    """Issue a handshake-era `sampling/createMessage` request from a server.

    `Context` has no `sample()` — server-initiated sampling is not part of
    FastMCP's server API. These tests cover the *client* side, which must keep
    answering a legacy server, so the stand-in server reaches the SDK session
    directly.
    """
    result = await context.session.create_message(  # ty: ignore[deprecated]
        messages=messages,
        system_prompt=system_prompt,
        max_tokens=512,
        related_request_id=context.origin_request_id,
    )
    assert isinstance(result.content, TextContent)
    return result.content.text


@pytest.fixture
def fastmcp_server():
    mcp = FastMCP()

    @mcp.tool
    async def simple_sample(message: str, context: Context) -> str:
        return await _sample(
            context,
            [
                SamplingMessage(
                    role="user",
                    content=TextContent(type="text", text="Hello, world!"),
                )
            ],
        )

    @mcp.tool
    async def sample_with_system_prompt(message: str, context: Context) -> str:
        return await _sample(
            context,
            [
                SamplingMessage(
                    role="user",
                    content=TextContent(type="text", text="Hello, world!"),
                )
            ],
            system_prompt="You love FastMCP",
        )

    @mcp.tool
    async def sample_with_messages(message: str, context: Context) -> str:
        return await _sample(
            context,
            [
                SamplingMessage(
                    role="user", content=TextContent(type="text", text="Hello!")
                ),
                SamplingMessage(
                    role="assistant",
                    content=TextContent(
                        type="text", text="How can I assist you today?"
                    ),
                ),
            ],
        )

    @mcp.tool
    async def sample_with_image(image_bytes: bytes, context: Context) -> str:
        image = Image(data=image_bytes)
        return await _sample(
            context,
            [
                SamplingMessage(
                    content=TextContent(type="text", text="What's in this image?"),
                    role="user",
                ),
                SamplingMessage(content=image.to_image_content(), role="user"),
            ],
        )

    return mcp


async def test_simple_sampling(fastmcp_server: FastMCP):
    def sampling_handler(
        messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
    ) -> str:
        return "This is the sample message!"

    async with Client(
        fastmcp_server, mode="legacy", sampling_handler=sampling_handler
    ) as client:
        result = await client.call_tool("simple_sample", {"message": "Hello, world!"})
        assert result.data == "This is the sample message!"


async def test_sampling_with_system_prompt(fastmcp_server: FastMCP):
    def sampling_handler(
        messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
    ) -> str:
        assert params.system_prompt is not None
        return params.system_prompt

    async with Client(
        fastmcp_server, mode="legacy", sampling_handler=sampling_handler
    ) as client:
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

    async with Client(
        fastmcp_server, mode="legacy", sampling_handler=sampling_handler
    ) as client:
        result = await client.call_tool(
            "sample_with_messages", {"message": "Hello, world!"}
        )
        assert result.data == "I need to think."


async def test_sampling_with_image(fastmcp_server: FastMCP):
    def sampling_handler(
        messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
    ) -> str:
        assert len(messages) == 2
        return to_json(messages).decode()

    async with Client(
        fastmcp_server, mode="legacy", sampling_handler=sampling_handler
    ) as client:
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
        server = FastMCP()
        client = Client(server)
        client.set_sampling_callback(lambda msgs, params, ctx: "ok")
        caps = client._session_kwargs["sampling_capabilities"]
        assert isinstance(caps, mcp_types.SamplingCapability)
        assert caps.tools is None

    async def test_explicit_tools_capability_is_preserved(self):
        """Explicitly passing tools capability should be respected."""
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
