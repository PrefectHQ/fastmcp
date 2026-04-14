"""Tests for OpenTelemetry instrumentation of sampling/createMessage."""

from __future__ import annotations

from mcp.types import (
    CreateMessageResultWithTools,
    TextContent,
    ToolUseContent,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from pydantic import BaseModel

from fastmcp import Client, Context, FastMCP
from fastmcp.client.sampling import RequestContext, SamplingMessage, SamplingParams


class TestSamplingCreateMessageSpan:
    """Verify the top-level sampling/createMessage span."""

    async def test_span_created_with_correct_attributes(
        self, trace_exporter: InMemorySpanExporter
    ):
        """sampling/createMessage span has expected attributes."""

        def sampling_handler(
            messages: list[SamplingMessage],
            params: SamplingParams,
            ctx: RequestContext,
        ) -> str:
            return "Hello from LLM"

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def do_sample(context: Context) -> str:
            result = await context.sample(
                "Hi",
                temperature=0.7,
                max_tokens=256,
            )
            return result.text or ""

        async with Client(mcp) as client:
            await client.call_tool("do_sample", {})

        spans = trace_exporter.get_finished_spans()
        sampling_spans = [s for s in spans if s.name == "sampling/createMessage"]
        assert len(sampling_spans) == 1

        span = sampling_spans[0]
        assert span.kind == SpanKind.INTERNAL
        assert span.attributes is not None
        assert span.attributes["mcp.method.name"] == "sampling/createMessage"
        assert span.attributes["gen_ai.request.temperature"] == 0.7
        assert span.attributes["gen_ai.request.max_tokens"] == 256
        assert span.attributes["fastmcp.sampling.tool_count"] == 0
        assert span.attributes["fastmcp.sampling.result_type"] == "str"

    async def test_span_records_iteration_count(
        self, trace_exporter: InMemorySpanExporter
    ):
        """sampling/createMessage span has fastmcp.sampling.iterations on completion."""

        def sampling_handler(
            messages: list[SamplingMessage],
            params: SamplingParams,
            ctx: RequestContext,
        ) -> str:
            return "Done"

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def do_sample(context: Context) -> str:
            result = await context.sample("Hi")
            return result.text or ""

        async with Client(mcp) as client:
            await client.call_tool("do_sample", {})

        spans = trace_exporter.get_finished_spans()
        sampling_spans = [s for s in spans if s.name == "sampling/createMessage"]
        assert len(sampling_spans) == 1
        span = sampling_spans[0]
        assert span.attributes is not None
        assert span.attributes["fastmcp.sampling.iterations"] == 1

    async def test_result_type_attribute(self, trace_exporter: InMemorySpanExporter):
        """result_type is recorded on the span."""

        class MyResult(BaseModel):
            value: int

        def sampling_handler(
            messages: list[SamplingMessage],
            params: SamplingParams,
            ctx: RequestContext,
        ) -> CreateMessageResultWithTools:
            return CreateMessageResultWithTools(
                role="assistant",
                content=[
                    ToolUseContent(
                        type="tool_use",
                        id="call_1",
                        name="final_response",
                        input={"value": 42},
                    )
                ],
                model="test-model",
                stopReason="toolUse",
            )

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def do_sample(context: Context) -> str:
            result = await context.sample("Hi", result_type=MyResult)
            return str(result.result.value)

        async with Client(mcp) as client:
            await client.call_tool("do_sample", {})

        spans = trace_exporter.get_finished_spans()
        sampling_spans = [s for s in spans if s.name == "sampling/createMessage"]
        assert len(sampling_spans) == 1
        span = sampling_spans[0]
        assert span.attributes is not None
        assert span.attributes["fastmcp.sampling.result_type"] == "MyResult"


class TestSamplingStepSpans:
    """Verify child sampling/createMessage step spans."""

    async def test_step_span_created_per_iteration(
        self, trace_exporter: InMemorySpanExporter
    ):
        """Each loop iteration creates a sampling/createMessage step span."""
        call_count = 0

        def sampling_handler(
            messages: list[SamplingMessage],
            params: SamplingParams,
            ctx: RequestContext,
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
                            input={"x": 1},
                        )
                    ],
                    model="test-model",
                    stopReason="toolUse",
                )
            return CreateMessageResultWithTools(
                role="assistant",
                content=[TextContent(type="text", text="Done")],
                model="test-model",
                stopReason="endTurn",
            )

        def my_tool(x: int) -> int:
            return x * 2

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def do_sample(context: Context) -> str:
            result = await context.sample("Hi", tools=[my_tool])
            return result.text or ""

        async with Client(mcp) as client:
            await client.call_tool("do_sample", {})

        spans = trace_exporter.get_finished_spans()
        step_spans = [s for s in spans if s.name == "sampling/createMessage step"]
        assert len(step_spans) == 2

        # First step should have iteration=0
        assert step_spans[0].attributes is not None
        assert step_spans[0].attributes["fastmcp.sampling.iteration"] == 0

        # Second step should have iteration=1
        assert step_spans[1].attributes is not None
        assert step_spans[1].attributes["fastmcp.sampling.iteration"] == 1

    async def test_step_span_records_stop_reason(
        self, trace_exporter: InMemorySpanExporter
    ):
        """Step span records stop reason from the response."""

        def sampling_handler(
            messages: list[SamplingMessage],
            params: SamplingParams,
            ctx: RequestContext,
        ) -> CreateMessageResultWithTools:
            return CreateMessageResultWithTools(
                role="assistant",
                content=[TextContent(type="text", text="Done")],
                model="test-model",
                stopReason="endTurn",
            )

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def do_sample(context: Context) -> str:
            result = await context.sample("Hi")
            return result.text or ""

        async with Client(mcp) as client:
            await client.call_tool("do_sample", {})

        spans = trace_exporter.get_finished_spans()
        step_spans = [s for s in spans if s.name == "sampling/createMessage step"]
        assert len(step_spans) == 1
        assert step_spans[0].attributes is not None
        assert step_spans[0].attributes["fastmcp.sampling.stop_reason"] == "endTurn"


class TestSamplingToolExecutionSpans:
    """Verify sampling.execute_tool spans."""

    async def test_tool_execution_span(self, trace_exporter: InMemorySpanExporter):
        """Tool call within sampling creates an execute_tool span."""
        call_count = 0

        def sampling_handler(
            messages: list[SamplingMessage],
            params: SamplingParams,
            ctx: RequestContext,
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
                            name="get_weather",
                            input={"city": "Seattle"},
                        )
                    ],
                    model="test-model",
                    stopReason="toolUse",
                )
            return CreateMessageResultWithTools(
                role="assistant",
                content=[TextContent(type="text", text="Done")],
                model="test-model",
                stopReason="endTurn",
            )

        def get_weather(city: str) -> str:
            return f"Sunny in {city}"

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def do_sample(context: Context) -> str:
            result = await context.sample("weather?", tools=[get_weather])
            return result.text or ""

        async with Client(mcp) as client:
            await client.call_tool("do_sample", {})

        spans = trace_exporter.get_finished_spans()
        tool_spans = [s for s in spans if s.name == "sampling.execute_tool get_weather"]
        assert len(tool_spans) == 1
        span = tool_spans[0]
        assert span.attributes is not None
        assert span.attributes["gen_ai.tool.name"] == "get_weather"
        assert span.attributes["gen_ai.operation.name"] == "execute_tool"

    async def test_tool_execution_error_sets_status(
        self, trace_exporter: InMemorySpanExporter
    ):
        """Tool error within sampling sets error status on the span."""
        call_count = 0

        def sampling_handler(
            messages: list[SamplingMessage],
            params: SamplingParams,
            ctx: RequestContext,
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
                            name="failing_tool",
                            input={},
                        )
                    ],
                    model="test-model",
                    stopReason="toolUse",
                )
            return CreateMessageResultWithTools(
                role="assistant",
                content=[TextContent(type="text", text="Handled")],
                model="test-model",
                stopReason="endTurn",
            )

        def failing_tool() -> str:
            raise ValueError("boom")

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def do_sample(context: Context) -> str:
            result = await context.sample("test", tools=[failing_tool])
            return result.text or ""

        async with Client(mcp) as client:
            await client.call_tool("do_sample", {})

        spans = trace_exporter.get_finished_spans()
        tool_spans = [
            s for s in spans if s.name == "sampling.execute_tool failing_tool"
        ]
        assert len(tool_spans) == 1
        span = tool_spans[0]
        assert span.status.status_code == StatusCode.ERROR
        assert span.attributes is not None
        assert span.attributes["error.type"] == "ValueError"


class TestSamplingValidationEvents:
    """Verify validation failure and text response retry events."""

    async def test_validation_failure_event(self, trace_exporter: InMemorySpanExporter):
        """Validation failure adds a sampling.validation_failure event."""

        class StrictModel(BaseModel):
            count: int

        call_count = 0

        def sampling_handler(
            messages: list[SamplingMessage],
            params: SamplingParams,
            ctx: RequestContext,
        ) -> CreateMessageResultWithTools:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First attempt: invalid data
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[
                        ToolUseContent(
                            type="tool_use",
                            id="call_1",
                            name="final_response",
                            input={"count": "not_a_number"},
                        )
                    ],
                    model="test-model",
                    stopReason="toolUse",
                )
            # Second attempt: valid data
            return CreateMessageResultWithTools(
                role="assistant",
                content=[
                    ToolUseContent(
                        type="tool_use",
                        id="call_2",
                        name="final_response",
                        input={"count": 42},
                    )
                ],
                model="test-model",
                stopReason="toolUse",
            )

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def do_sample(context: Context) -> str:
            result = await context.sample("give me a count", result_type=StrictModel)
            return str(result.result.count)

        async with Client(mcp) as client:
            await client.call_tool("do_sample", {})

        spans = trace_exporter.get_finished_spans()
        sampling_spans = [s for s in spans if s.name == "sampling/createMessage"]
        assert len(sampling_spans) == 1
        span = sampling_spans[0]

        validation_events = [
            e for e in span.events if e.name == "sampling.validation_failure"
        ]
        assert len(validation_events) == 1
        assert validation_events[0].attributes is not None
        assert (
            validation_events[0].attributes["fastmcp.sampling.consecutive_failures"]
            == 1
        )

    async def test_text_response_retry_event(
        self, trace_exporter: InMemorySpanExporter
    ):
        """Text response instead of tool call adds a sampling.text_response_retry event."""

        class MyResult(BaseModel):
            value: int

        call_count = 0

        def sampling_handler(
            messages: list[SamplingMessage],
            params: SamplingParams,
            ctx: RequestContext,
        ) -> CreateMessageResultWithTools:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First attempt: text response instead of tool call
                return CreateMessageResultWithTools(
                    role="assistant",
                    content=[TextContent(type="text", text="I think 42")],
                    model="test-model",
                    stopReason="endTurn",
                )
            # Second attempt: correct tool call
            return CreateMessageResultWithTools(
                role="assistant",
                content=[
                    ToolUseContent(
                        type="tool_use",
                        id="call_1",
                        name="final_response",
                        input={"value": 42},
                    )
                ],
                model="test-model",
                stopReason="toolUse",
            )

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def do_sample(context: Context) -> str:
            result = await context.sample("give me a value", result_type=MyResult)
            return str(result.result.value)

        async with Client(mcp) as client:
            await client.call_tool("do_sample", {})

        spans = trace_exporter.get_finished_spans()
        sampling_spans = [s for s in spans if s.name == "sampling/createMessage"]
        assert len(sampling_spans) == 1
        span = sampling_spans[0]

        retry_events = [
            e for e in span.events if e.name == "sampling.text_response_retry"
        ]
        assert len(retry_events) == 1
        assert retry_events[0].attributes is not None
        assert retry_events[0].attributes["fastmcp.sampling.retry_count"] == 1
