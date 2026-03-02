from mcp.types import TextContent

from fastmcp import Client, Context, FastMCP
from fastmcp.client.sampling import RequestContext, SamplingMessage, SamplingParams


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
