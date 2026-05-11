from typing import cast

from mcp.types import TextContent

from fastmcp import Client, Context, FastMCP
from fastmcp.client.sampling import RequestContext, SamplingMessage, SamplingParams
from fastmcp.server.sampling import SamplingTool


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
