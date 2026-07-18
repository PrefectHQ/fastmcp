"""Tracing coverage for sampling create_message and tool-execution spans.

Regression focus: the `sampling create_message` span is created with
`record_exception=False, set_status_on_exception=False` and records the
exception manually in its `except` block. A failed sampling call must
therefore produce exactly ONE exception event, not two.
"""

from __future__ import annotations

import pytest
from mcp_types import TextContent
from opentelemetry.context import Context as OTelContext
from opentelemetry.sdk.trace import Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from fastmcp import Client, Context, FastMCP
from fastmcp.client.sampling import RequestContext, SamplingMessage, SamplingParams


class OnStartRecorder(SpanProcessor):
    def __init__(self) -> None:
        self.attributes: dict[str, dict[str, object]] = {}

    def on_start(self, span: Span, parent_context: OTelContext | None = None) -> None:
        self.attributes[span.name] = dict(span.attributes or {})


@pytest.fixture
def on_start_recorder(
    monkeypatch: pytest.MonkeyPatch,
    trace_exporter: InMemorySpanExporter,
) -> OnStartRecorder:
    recorder = OnStartRecorder()
    provider = TracerProvider()
    provider.add_span_processor(recorder)
    provider.add_span_processor(SimpleSpanProcessor(trace_exporter))
    tracer = provider.get_tracer("test")
    monkeypatch.setattr("fastmcp.server.sampling.run.get_tracer", lambda: tracer)
    return recorder


def _spans_named(exporter: InMemorySpanExporter, name: str):
    return [s for s in exporter.get_finished_spans() if s.name == name]


def _exception_events(span):
    return [e for e in span.events if e.name == "exception"]


class TestSamplingCreateMessageSpan:
    async def test_success_creates_span_with_attributes(
        self,
        trace_exporter: InMemorySpanExporter,
        on_start_recorder: OnStartRecorder,
    ):
        def sampling_handler(
            messages: list[SamplingMessage],
            params: SamplingParams,
            ctx: RequestContext,
        ) -> str:
            return "sampled-text"

        mcp = FastMCP("sampling-server")

        @mcp.tool
        async def ask(question: str, context: Context) -> str:
            result = await context.sample(messages=question)
            return result.text or ""

        async with Client(mcp, sampling_handler=sampling_handler) as client:
            await client.call_tool("ask", {"question": "hi"})

        spans = _spans_named(trace_exporter, "sampling create_message")
        assert len(spans) == 1
        span = spans[0]
        assert span.attributes is not None
        assert span.attributes["mcp.method.name"] == "sampling/createMessage"
        assert span.attributes["fastmcp.server.name"] == "sampling-server"
        assert on_start_recorder.attributes["sampling create_message"] == {
            "mcp.method.name": "sampling/createMessage",
            "fastmcp.server.name": "sampling-server",
        }
        # Success path must not record any exception.
        assert _exception_events(span) == []
        assert span.status.status_code != StatusCode.ERROR

    async def test_failure_records_exception_exactly_once(
        self, trace_exporter: InMemorySpanExporter
    ):
        """Regression: span created with record_exception=False so the manual
        record_exception in the except block fires exactly once (no duplicate
        exception events from OTel auto-recording on `with` exit)."""

        def sampling_handler(
            messages: list[SamplingMessage],
            params: SamplingParams,
            ctx: RequestContext,
        ) -> str:
            raise RuntimeError("sampling boom")

        mcp = FastMCP("sampling-server")

        @mcp.tool
        async def ask(question: str, context: Context) -> str:
            result = await context.sample(messages=question)
            return result.text or ""

        with pytest.raises(Exception):
            async with Client(mcp, sampling_handler=sampling_handler) as client:
                await client.call_tool("ask", {"question": "hi"})

        spans = _spans_named(trace_exporter, "sampling create_message")
        assert len(spans) == 1
        span = spans[0]
        assert span.status.status_code == StatusCode.ERROR
        assert span.attributes is not None
        assert "error.type" in span.attributes
        # The whole point of the fix: exactly one exception event.
        assert len(_exception_events(span)) == 1


class TestSamplingToolSpan:
    async def test_tool_error_span_records_exception_once(
        self,
        trace_exporter: InMemorySpanExporter,
        on_start_recorder: OnStartRecorder,
    ):
        from mcp_types import CreateMessageResultWithTools, ToolUseContent

        call_count = 0

        def boom_tool() -> str:
            raise ValueError("tool exploded")

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
                            name="boom_tool",
                            input={},
                        )
                    ],
                    model="test-model",
                    stop_reason="toolUse",
                )
            return CreateMessageResultWithTools(
                role="assistant",
                content=[TextContent(type="text", text="done")],
                model="test-model",
                stop_reason="endTurn",
            )

        mcp = FastMCP(sampling_handler=sampling_handler)

        @mcp.tool
        async def driver(context: Context) -> str:
            result = await context.sample(messages="go", tools=[boom_tool])
            return result.text or ""

        async with Client(mcp) as client:
            await client.call_tool("driver", {})

        spans = _spans_named(trace_exporter, "sampling tool boom_tool")
        assert len(spans) == 1
        span = spans[0]
        assert span.status.status_code == StatusCode.ERROR
        assert span.attributes is not None
        assert span.attributes["gen_ai.tool.name"] == "boom_tool"
        assert on_start_recorder.attributes["sampling tool boom_tool"] == {
            "gen_ai.tool.name": "boom_tool",
            "fastmcp.tool.use_id": "call_1",
        }
        assert "error.type" in span.attributes
        # Tool spans catch-and-convert (no re-raise), so OTel auto-recording
        # never fires; the manual record_exception must fire exactly once.
        assert len(_exception_events(span)) == 1
