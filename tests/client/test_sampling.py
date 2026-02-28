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


class TestAutomaticToolLoop:
    """Tests for automatic tool execution loop in ctx.sample()."""

    async def test_automatic_tool_loop_executes_tools(self):
        """Test that ctx.sample() automatically executes tool calls."""
        from mcp.types import CreateMessageResultWithTools, ToolUseContent

        call_count = 0
        tool_was_called = False

        def get_weather(city: str) -> str:
            """Get weather for a city."""
            nonlocal tool_was_called
            tool_was_called = True
            return f"Weather in {city}: sunny, 72°F"

        def sampling_handler(
            messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
        ) -> CreateMessageResultWithTools:
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                # First call: return tool use
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[
                        ToolUseContent(
                            type="tool_use",
                            id="call_1",
                            name="get_weather",
                            input={"city": "Seattle"},
                        )
                    ],
                    model="test-model",
                    stopReason="toolUse",
                )
            else:
                # Second call: return final response
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[TextContent(type="text", text="The weather is sunny!")],
                    model="test-model",
                    stopReason="endTurn",
                )

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def weather_assistant(question: str, context: Context) -> str:
            result = await context.sample(
                messages=question,
                tools=[get_weather],
            )
            # Get text from SamplingResult
            return result.text or ""

        async with Client(mcp) as client:
            result = await client.call_tool(
                "weather_assistant", {"question": "What's the weather?"}
            )

        assert tool_was_called
        assert call_count == 2
        assert result.data == "The weather is sunny!"

    async def test_automatic_tool_loop_multiple_tools(self):
        """Test that multiple tool calls in one response are all executed."""
        from mcp.types import CreateMessageResultWithTools, ToolUseContent

        executed_tools: list[str] = []

        def tool_a(x: int) -> int:
            """Tool A."""
            executed_tools.append(f"tool_a({x})")
            return x * 2

        def tool_b(y: int) -> int:
            """Tool B."""
            executed_tools.append(f"tool_b({y})")
            return y + 10

        call_count = 0

        def sampling_handler(
            messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
        ) -> CreateMessageResultWithTools:
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                # Return multiple tool calls
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[
                        ToolUseContent(
                            type="tool_use", id="call_a", name="tool_a", input={"x": 5}
                        ),
                        ToolUseContent(
                            type="tool_use", id="call_b", name="tool_b", input={"y": 3}
                        ),
                    ],
                    model="test-model",
                    stopReason="toolUse",
                )
            else:
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[TextContent(type="text", text="Done!")],
                    model="test-model",
                    stopReason="endTurn",
                )

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def multi_tool(context: Context) -> str:
            result = await context.sample(messages="Run tools", tools=[tool_a, tool_b])
            return result.text or ""

        async with Client(mcp) as client:
            result = await client.call_tool("multi_tool", {})

        assert executed_tools == ["tool_a(5)", "tool_b(3)"]
        assert result.data == "Done!"

    async def test_automatic_tool_loop_handles_unknown_tool(self):
        """Test that unknown tool names result in error being passed to LLM."""
        from mcp.types import (
            CreateMessageResultWithTools,
            ToolResultContent,
            ToolUseContent,
        )

        def known_tool() -> str:
            """A known tool."""
            return "known result"

        messages_received: list[list[SamplingMessage]] = []

        def sampling_handler(
            messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
        ) -> CreateMessageResultWithTools:
            messages_received.append(list(messages))

            if len(messages_received) == 1:
                # Request unknown tool
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[
                        ToolUseContent(
                            type="tool_use",
                            id="call_1",
                            name="unknown_tool",
                            input={},
                        )
                    ],
                    model="test-model",
                    stopReason="toolUse",
                )
            else:
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[TextContent(type="text", text="Handled error")],
                    model="test-model",
                    stopReason="endTurn",
                )

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def test_unknown(context: Context) -> str:
            result = await context.sample(messages="Test", tools=[known_tool])
            return result.text or ""

        async with Client(mcp) as client:
            result = await client.call_tool("test_unknown", {})

        # Check that error was passed back in messages
        assert len(messages_received) == 2
        last_messages = messages_received[1]
        # Find the tool result in list content
        tool_result = None
        for msg in last_messages:
            # Tool results are now in a list
            if isinstance(msg.content, list):
                for item in msg.content:
                    if isinstance(item, ToolResultContent):
                        tool_result = item
                        break
            elif isinstance(msg.content, ToolResultContent):
                tool_result = msg.content
                break
        assert tool_result is not None
        assert tool_result.isError is True
        # Content is list of TextContent objects
        assert isinstance(tool_result.content[0], TextContent)
        error_text = tool_result.content[0].text
        assert "Unknown tool" in error_text
        assert result.data == "Handled error"

    async def test_automatic_tool_loop_handles_tool_exception(self):
        """Test that tool exceptions are caught and passed to LLM as errors."""
        from mcp.types import (
            CreateMessageResultWithTools,
            ToolResultContent,
            ToolUseContent,
        )

        def failing_tool() -> str:
            """A tool that raises an exception."""
            raise ValueError("Tool failed intentionally")

        messages_received: list[list[SamplingMessage]] = []

        def sampling_handler(
            messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
        ) -> CreateMessageResultWithTools:
            messages_received.append(list(messages))

            if len(messages_received) == 1:
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[
                        ToolUseContent(
                            type="tool_use",
                            id="call_1",
                            name="failing_tool",
                            input={},
                        )
                    ],
                    model="test-model",
                    stopReason="toolUse",
                )
            else:
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[TextContent(type="text", text="Handled error")],
                    model="test-model",
                    stopReason="endTurn",
                )

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def test_exception(context: Context) -> str:
            result = await context.sample(messages="Test", tools=[failing_tool])
            return result.text or ""

        async with Client(mcp) as client:
            result = await client.call_tool("test_exception", {})

        # Check that error was passed back
        assert len(messages_received) == 2
        last_messages = messages_received[1]
        # Find the tool result in list content
        tool_result = None
        for msg in last_messages:
            # Tool results are now in a list
            if isinstance(msg.content, list):
                for item in msg.content:
                    if isinstance(item, ToolResultContent):
                        tool_result = item
                        break
            elif isinstance(msg.content, ToolResultContent):
                tool_result = msg.content
                break
        assert tool_result is not None
        assert tool_result.isError is True
        # Content is list of TextContent objects
        assert isinstance(tool_result.content[0], TextContent)
        error_text = tool_result.content[0].text
        assert "Tool failed intentionally" in error_text
        assert result.data == "Handled error"

    async def test_concurrent_tool_execution_default_sequential(self):
        """Test that tools execute sequentially by default."""
        import asyncio
        import time

        from mcp.types import CreateMessageResultWithTools, ToolUseContent

        execution_order: list[tuple[str, float]] = []

        async def slow_tool_a(x: int) -> int:
            """Slow tool A."""
            start = time.time()
            execution_order.append(("tool_a_start", start))
            await asyncio.sleep(0.1)
            execution_order.append(("tool_a_end", time.time()))
            return x * 2

        async def slow_tool_b(y: int) -> int:
            """Slow tool B."""
            start = time.time()
            execution_order.append(("tool_b_start", start))
            await asyncio.sleep(0.1)
            execution_order.append(("tool_b_end", time.time()))
            return y + 10

        call_count = 0

        def sampling_handler(
            messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
        ) -> CreateMessageResultWithTools:
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[
                        ToolUseContent(
                            type="tool_use",
                            id="call_a",
                            name="slow_tool_a",
                            input={"x": 5},
                        ),
                        ToolUseContent(
                            type="tool_use",
                            id="call_b",
                            name="slow_tool_b",
                            input={"y": 3},
                        ),
                    ],
                    model="test-model",
                    stopReason="toolUse",
                )
            else:
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[TextContent(type="text", text="Done!")],
                    model="test-model",
                    stopReason="endTurn",
                )

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def test_tool(context: Context) -> str:
            result = await context.sample(
                messages="Run tools",
                tools=[slow_tool_a, slow_tool_b],
                # Default: tool_concurrency=None (sequential)
            )
            return result.text or ""

        async with Client(mcp) as client:
            result = await client.call_tool("test_tool", {})

        assert result.data == "Done!"
        # Verify sequential execution: tool_a must complete before tool_b starts
        events = [e[0] for e in execution_order]
        assert events == ["tool_a_start", "tool_a_end", "tool_b_start", "tool_b_end"]

    async def test_concurrent_tool_execution_unlimited(self):
        """Test unlimited parallel tool execution with tool_concurrency=0."""
        import asyncio
        import time

        from mcp.types import CreateMessageResultWithTools, ToolUseContent

        execution_times: dict[str, dict[str, float]] = {}

        async def slow_tool_a(x: int) -> int:
            """Slow tool A."""
            execution_times["tool_a"] = {"start": time.time()}
            await asyncio.sleep(0.1)
            execution_times["tool_a"]["end"] = time.time()
            return x * 2

        async def slow_tool_b(y: int) -> int:
            """Slow tool B."""
            execution_times["tool_b"] = {"start": time.time()}
            await asyncio.sleep(0.1)
            execution_times["tool_b"]["end"] = time.time()
            return y + 10

        call_count = 0

        def sampling_handler(
            messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
        ) -> CreateMessageResultWithTools:
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[
                        ToolUseContent(
                            type="tool_use",
                            id="call_a",
                            name="slow_tool_a",
                            input={"x": 5},
                        ),
                        ToolUseContent(
                            type="tool_use",
                            id="call_b",
                            name="slow_tool_b",
                            input={"y": 3},
                        ),
                    ],
                    model="test-model",
                    stopReason="toolUse",
                )
            else:
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[TextContent(type="text", text="Done!")],
                    model="test-model",
                    stopReason="endTurn",
                )

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def test_tool(context: Context) -> str:
            result = await context.sample(
                messages="Run tools",
                tools=[slow_tool_a, slow_tool_b],
                tool_concurrency=0,  # Unlimited parallel
            )
            return result.text or ""

        async with Client(mcp) as client:
            result = await client.call_tool("test_tool", {})

        assert result.data == "Done!"
        # Verify parallel execution: both tools should overlap in time
        assert "tool_a" in execution_times
        assert "tool_b" in execution_times
        # tool_b should start before tool_a finishes (overlap)
        assert execution_times["tool_b"]["start"] < execution_times["tool_a"]["end"]

    async def test_concurrent_tool_execution_bounded(self):
        """Test bounded parallel execution with tool_concurrency=2."""
        import asyncio
        import time

        from mcp.types import CreateMessageResultWithTools, ToolUseContent

        execution_order: list[tuple[str, float]] = []

        async def slow_tool(name: str, duration: float = 0.1) -> str:
            """Generic slow tool."""
            execution_order.append((f"{name}_start", time.time()))
            await asyncio.sleep(duration)
            execution_order.append((f"{name}_end", time.time()))
            return f"{name} done"

        call_count = 0

        def sampling_handler(
            messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
        ) -> CreateMessageResultWithTools:
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                # Request 3 tools (with concurrency=2, first 2 run parallel, then 3rd)
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[
                        ToolUseContent(
                            type="tool_use",
                            id="call_1",
                            name="slow_tool",
                            input={"name": "tool_1", "duration": 0.1},
                        ),
                        ToolUseContent(
                            type="tool_use",
                            id="call_2",
                            name="slow_tool",
                            input={"name": "tool_2", "duration": 0.1},
                        ),
                        ToolUseContent(
                            type="tool_use",
                            id="call_3",
                            name="slow_tool",
                            input={"name": "tool_3", "duration": 0.05},
                        ),
                    ],
                    model="test-model",
                    stopReason="toolUse",
                )
            else:
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[TextContent(type="text", text="Done!")],
                    model="test-model",
                    stopReason="endTurn",
                )

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def test_tool(context: Context) -> str:
            result = await context.sample(
                messages="Run tools",
                tools=[slow_tool],
                tool_concurrency=2,  # Max 2 concurrent
            )
            return result.text or ""

        async with Client(mcp) as client:
            result = await client.call_tool("test_tool", {})

        assert result.data == "Done!"
        # Verify that at most 2 tools run concurrently
        events = [e[0] for e in execution_order]
        # First 2 tools should start before either ends
        assert events[0] in ["tool_1_start", "tool_2_start"]
        assert events[1] in ["tool_1_start", "tool_2_start"]
        # Third tool should start after at least one of the first two finishes
        tool_3_start_idx = events.index("tool_3_start")
        assert (
            "tool_1_end" in events[:tool_3_start_idx]
            or "tool_2_end" in events[:tool_3_start_idx]
        )

    async def test_sequential_tool_forces_sequential_execution(self):
        """Test that sequential=True forces all tools to execute sequentially."""
        import asyncio
        import time

        from mcp.types import CreateMessageResultWithTools, ToolUseContent

        execution_order: list[tuple[str, float]] = []

        async def normal_tool(x: int) -> int:
            """Normal tool."""
            execution_order.append(("normal_start", time.time()))
            await asyncio.sleep(0.05)
            execution_order.append(("normal_end", time.time()))
            return x * 2

        async def sequential_tool(y: int) -> int:
            """Sequential tool."""
            execution_order.append(("sequential_start", time.time()))
            await asyncio.sleep(0.05)
            execution_order.append(("sequential_end", time.time()))
            return y + 10

        call_count = 0

        def sampling_handler(
            messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
        ) -> CreateMessageResultWithTools:
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[
                        ToolUseContent(
                            type="tool_use",
                            id="call_1",
                            name="normal_tool",
                            input={"x": 5},
                        ),
                        ToolUseContent(
                            type="tool_use",
                            id="call_2",
                            name="sequential_tool",
                            input={"y": 3},
                        ),
                    ],
                    model="test-model",
                    stopReason="toolUse",
                )
            else:
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[TextContent(type="text", text="Done!")],
                    model="test-model",
                    stopReason="endTurn",
                )

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def test_tool(context: Context) -> str:
            # Create tools with sequential=True for one of them
            normal = SamplingTool.from_function(normal_tool, sequential=False)
            sequential = SamplingTool.from_function(sequential_tool, sequential=True)

            result = await context.sample(
                messages="Run tools",
                tools=[normal, sequential],
                tool_concurrency=0,  # Request unlimited, but sequential tool forces sequential
            )
            return result.text or ""

        async with Client(mcp) as client:
            result = await client.call_tool("test_tool", {})

        assert result.data == "Done!"
        # Verify sequential execution: first tool must complete before second starts
        events = [e[0] for e in execution_order]
        assert events[0] in ["normal_start", "sequential_start"]
        assert events[1] in ["normal_end", "sequential_end"]
        # Ensure the second tool starts after the first ends
        if events[0] == "normal_start":
            assert events[1] == "normal_end"
            assert events[2] == "sequential_start"
        else:
            assert events[1] == "sequential_end"
            assert events[2] == "normal_start"

    async def test_concurrent_tool_execution_error_handling(self):
        """Test that errors are captured per-tool in parallel execution."""
        from mcp.types import (
            CreateMessageResultWithTools,
            ToolResultContent,
            ToolUseContent,
        )

        def good_tool() -> str:
            return "success"

        def bad_tool() -> str:
            raise ValueError("Tool error")

        messages_received: list[list[SamplingMessage]] = []

        def sampling_handler(
            messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
        ) -> CreateMessageResultWithTools:
            messages_received.append(list(messages))

            if len(messages_received) == 1:
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[
                        ToolUseContent(
                            type="tool_use", id="call_1", name="good_tool", input={}
                        ),
                        ToolUseContent(
                            type="tool_use", id="call_2", name="bad_tool", input={}
                        ),
                    ],
                    model="test-model",
                    stopReason="toolUse",
                )
            else:
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[TextContent(type="text", text="Handled errors")],
                    model="test-model",
                    stopReason="endTurn",
                )

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def test_tool(context: Context) -> str:
            result = await context.sample(
                messages="Run tools",
                tools=[good_tool, bad_tool],
                tool_concurrency=0,  # Parallel execution
            )
            return result.text or ""

        async with Client(mcp) as client:
            result = await client.call_tool("test_tool", {})

        assert result.data == "Handled errors"
        # Check that tool results include both success and error
        tool_result_message = messages_received[1][-1]
        assert tool_result_message.role == "user"
        tool_results = cast(list[ToolResultContent], tool_result_message.content)
        assert len(tool_results) == 2
        # One should be success, one should be error
        assert any(not r.isError for r in tool_results)
        assert any(r.isError for r in tool_results)

    async def test_concurrent_tool_result_order_preserved(self):
        """Test that tool results maintain the same order as tool calls."""
        import asyncio

        from mcp.types import (
            CreateMessageResultWithTools,
            ToolResultContent,
            ToolUseContent,
        )

        async def tool_with_delay(value: int, delay: float) -> int:
            """Tool that takes variable time."""
            await asyncio.sleep(delay)
            return value

        messages_received: list[list[SamplingMessage]] = []

        def sampling_handler(
            messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
        ) -> CreateMessageResultWithTools:
            messages_received.append(list(messages))

            if len(messages_received) == 1:
                # Tools with different delays - later tools finish first
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[
                        ToolUseContent(
                            type="tool_use",
                            id="call_1",
                            name="tool_with_delay",
                            input={"value": 1, "delay": 0.15},
                        ),
                        ToolUseContent(
                            type="tool_use",
                            id="call_2",
                            name="tool_with_delay",
                            input={"value": 2, "delay": 0.05},
                        ),
                        ToolUseContent(
                            type="tool_use",
                            id="call_3",
                            name="tool_with_delay",
                            input={"value": 3, "delay": 0.1},
                        ),
                    ],
                    model="test-model",
                    stopReason="toolUse",
                )
            else:
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[TextContent(type="text", text="Done!")],
                    model="test-model",
                    stopReason="endTurn",
                )

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def test_tool(context: Context) -> str:
            result = await context.sample(
                messages="Run tools",
                tools=[tool_with_delay],
                tool_concurrency=0,  # Parallel execution
            )
            return result.text or ""

        async with Client(mcp) as client:
            result = await client.call_tool("test_tool", {})

        assert result.data == "Done!"
        # Check that results are in the correct order (1, 2, 3) despite finishing order (2, 3, 1)
        tool_result_message = messages_received[1][-1]
        tool_results = cast(list[ToolResultContent], tool_result_message.content)
        assert len(tool_results) == 3
        assert tool_results[0].toolUseId == "call_1"
        assert tool_results[1].toolUseId == "call_2"
        assert tool_results[2].toolUseId == "call_3"
        # Check values are correct
        result_texts = [cast(TextContent, r.content[0]).text for r in tool_results]
        assert result_texts == ["1", "2", "3"]


class TestSamplingResultType:
    """Tests for result_type parameter (structured output)."""

    async def test_result_type_creates_final_response_tool(self):
        """Test that result_type creates a synthetic final_response tool."""
        from mcp.types import CreateMessageResultWithTools, ToolUseContent
        from pydantic import BaseModel

        class MathResult(BaseModel):
            answer: int
            explanation: str

        received_tools: list = []

        def sampling_handler(
            messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
        ) -> CreateMessageResultWithTools:
            received_tools.extend(params.tools or [])

            # Return the final_response tool call
            return CreateMessageResultWithTools(
                role="assistant",
                content=[
                    ToolUseContent(
                        type="tool_use",
                        id="call_1",
                        name="final_response",
                        input={"answer": 42, "explanation": "The meaning of life"},
                    )
                ],
                model="test-model",
                stopReason="toolUse",
            )

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def math_tool(context: Context) -> str:
            result = await context.sample(
                messages="What is 6 * 7?",
                result_type=MathResult,
            )
            # result.result should be a MathResult object
            assert isinstance(result.result, MathResult)
            return f"{result.result.answer}: {result.result.explanation}"

        async with Client(mcp) as client:
            result = await client.call_tool("math_tool", {})

        # Check that final_response tool was added
        tool_names = [t.name for t in received_tools]
        assert "final_response" in tool_names

        # Check the result
        assert result.data == "42: The meaning of life"

    async def test_result_type_with_user_tools(self):
        """Test result_type works alongside user-provided tools."""
        from mcp.types import CreateMessageResultWithTools, ToolUseContent
        from pydantic import BaseModel

        class SearchResult(BaseModel):
            summary: str
            sources: list[str]

        def search(query: str) -> str:
            """Search for information."""
            return f"Found info about: {query}"

        call_count = 0
        tool_was_called = False

        def sampling_handler(
            messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
        ) -> CreateMessageResultWithTools:
            nonlocal call_count, tool_was_called
            call_count += 1

            if call_count == 1:
                # First call: use the search tool
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[
                        ToolUseContent(
                            type="tool_use",
                            id="call_1",
                            name="search",
                            input={"query": "Python tutorials"},
                        )
                    ],
                    model="test-model",
                    stopReason="toolUse",
                )
            else:
                # Second call: call final_response
                tool_was_called = True
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[
                        ToolUseContent(
                            type="tool_use",
                            id="call_2",
                            name="final_response",
                            input={
                                "summary": "Python is great",
                                "sources": ["python.org", "docs.python.org"],
                            },
                        )
                    ],
                    model="test-model",
                    stopReason="toolUse",
                )

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def research(context: Context) -> str:
            result = await context.sample(
                messages="Research Python",
                tools=[search],
                result_type=SearchResult,
            )
            assert isinstance(result.result, SearchResult)
            return f"{result.result.summary} - {len(result.result.sources)} sources"

        async with Client(mcp) as client:
            result = await client.call_tool("research", {})

        assert tool_was_called
        assert result.data == "Python is great - 2 sources"

    async def test_result_type_validation_error_retries(self):
        """Test that validation errors are sent back to LLM for retry."""
        from mcp.types import (
            CreateMessageResultWithTools,
            ToolResultContent,
            ToolUseContent,
        )
        from pydantic import BaseModel

        class StrictResult(BaseModel):
            value: int  # Must be an int

        messages_received: list[list[SamplingMessage]] = []

        def sampling_handler(
            messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
        ) -> CreateMessageResultWithTools:
            messages_received.append(list(messages))

            if len(messages_received) == 1:
                # First call: invalid type
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[
                        ToolUseContent(
                            type="tool_use",
                            id="call_1",
                            name="final_response",
                            input={"value": "not_an_int"},  # Wrong type
                        )
                    ],
                    model="test-model",
                    stopReason="toolUse",
                )
            else:
                # Second call: valid type after seeing error
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[
                        ToolUseContent(
                            type="tool_use",
                            id="call_2",
                            name="final_response",
                            input={"value": 42},  # Correct type
                        )
                    ],
                    model="test-model",
                    stopReason="toolUse",
                )

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def validate_tool(context: Context) -> str:
            result = await context.sample(
                messages="Give me a number",
                result_type=StrictResult,
            )
            assert isinstance(result.result, StrictResult)
            return str(result.result.value)

        async with Client(mcp) as client:
            result = await client.call_tool("validate_tool", {})

        # Should have retried after validation error
        assert len(messages_received) == 2

        # Check that error was passed back
        last_messages = messages_received[1]
        # Find the tool result in list content
        tool_result = None
        for msg in last_messages:
            # Tool results are now in a list
            if isinstance(msg.content, list):
                for item in msg.content:
                    if isinstance(item, ToolResultContent):
                        tool_result = item
                        break
            elif isinstance(msg.content, ToolResultContent):
                tool_result = msg.content
                break
        assert tool_result is not None
        assert tool_result.isError is True
        assert isinstance(tool_result.content[0], TextContent)
        error_text = tool_result.content[0].text
        assert "Validation error" in error_text

        # Final result should be correct
        assert result.data == "42"

    async def test_sampling_result_has_text_and_history(self):
        """Test that SamplingResult has text, result, and history attributes."""
        from mcp.types import CreateMessageResultWithTools

        def sampling_handler(
            messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
        ) -> CreateMessageResultWithTools:
            return CreateMessageResultWithTools(
                role="assistant",
                content=[TextContent(type="text", text="Hello world")],
                model="test-model",
                stopReason="endTurn",
            )

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def check_result(context: Context) -> str:
            result = await context.sample(messages="Say hello")
            # Check all attributes exist
            assert result.text == "Hello world"
            assert result.result == "Hello world"
            assert len(result.history) >= 1
            return "ok"

        async with Client(mcp) as client:
            result = await client.call_tool("check_result", {})

        assert result.data == "ok"


class TestSampleStep:
    """Tests for ctx.sample_step() - single LLM call with manual control."""

    async def test_sample_step_basic(self):
        """Test basic sample_step returns text response."""
        from mcp.types import CreateMessageResultWithTools

        def sampling_handler(
            messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
        ) -> CreateMessageResultWithTools:
            return CreateMessageResultWithTools(
                role="assistant",
                content=[TextContent(type="text", text="Hello from step")],
                model="test-model",
                stopReason="endTurn",
            )

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def test_step(context: Context) -> str:
            step = await context.sample_step(messages="Hi")
            assert not step.is_tool_use
            assert step.text == "Hello from step"
            return step.text or ""

        async with Client(mcp) as client:
            result = await client.call_tool("test_step", {})

        assert result.data == "Hello from step"

    async def test_sample_step_with_tool_execution(self):
        """Test sample_step executes tools by default."""
        from mcp.types import CreateMessageResultWithTools, ToolUseContent

        call_count = 0

        def my_tool(x: int) -> str:
            """A test tool."""
            return f"result:{x}"

        def sampling_handler(
            messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
        ) -> CreateMessageResultWithTools:
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[
                        ToolUseContent(
                            type="tool_use",
                            id="call_1",
                            name="my_tool",
                            input={"x": 42},
                        )
                    ],
                    model="test-model",
                    stopReason="toolUse",
                )
            else:
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[TextContent(type="text", text="Done")],
                    model="test-model",
                    stopReason="endTurn",
                )

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def test_step(context: Context) -> str:
            messages: str | list[SamplingMessage] = "Run tool"

            while True:
                step = await context.sample_step(messages=messages, tools=[my_tool])

                if not step.is_tool_use:
                    return step.text or ""

                # History should include tool results when execute_tools=True
                messages = step.history

        async with Client(mcp) as client:
            result = await client.call_tool("test_step", {})

        assert result.data == "Done"
        assert call_count == 2

    async def test_sample_step_execute_tools_false(self):
        """Test sample_step with execute_tools=False doesn't execute tools."""
        from mcp.types import CreateMessageResultWithTools, ToolUseContent

        tool_executed = False

        def my_tool() -> str:
            """A test tool."""
            nonlocal tool_executed
            tool_executed = True
            return "executed"

        def sampling_handler(
            messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
        ) -> CreateMessageResultWithTools:
            return CreateMessageResultWithTools(
                role="assistant",
                content=[
                    ToolUseContent(
                        type="tool_use",
                        id="call_1",
                        name="my_tool",
                        input={},
                    )
                ],
                model="test-model",
                stopReason="toolUse",
            )

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def test_step(context: Context) -> str:
            step = await context.sample_step(
                messages="Run tool",
                tools=[my_tool],
                execute_tools=False,
            )
            assert step.is_tool_use
            assert len(step.tool_calls) == 1
            assert step.tool_calls[0].name == "my_tool"
            # History should include assistant message but no tool results
            assert len(step.history) == 2  # user + assistant
            return "ok"

        async with Client(mcp) as client:
            result = await client.call_tool("test_step", {})

        assert result.data == "ok"
        assert not tool_executed  # Tool should not have been executed

    async def test_sample_step_history_includes_assistant_message(self):
        """Test that history includes assistant message when execute_tools=False."""
        from mcp.types import CreateMessageResultWithTools, ToolUseContent

        def sampling_handler(
            messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
        ) -> CreateMessageResultWithTools:
            return CreateMessageResultWithTools(
                role="assistant",
                content=[
                    ToolUseContent(
                        type="tool_use",
                        id="call_1",
                        name="my_tool",
                        input={"query": "test"},
                    )
                ],
                model="test-model",
                stopReason="toolUse",
            )

        mcp = FastMCP(sampling_handler=sampling_handler)

        def my_tool(query: str) -> str:
            return f"result for {query}"

        @mcp.tool
        async def test_step(context: Context) -> str:
            step = await context.sample_step(
                messages="Search",
                tools=[my_tool],
                execute_tools=False,
            )
            # History should have: user message + assistant message
            assert len(step.history) == 2
            assert step.history[0].role == "user"
            assert step.history[1].role == "assistant"
            return "ok"

        async with Client(mcp) as client:
            result = await client.call_tool("test_step", {})

        assert result.data == "ok"
