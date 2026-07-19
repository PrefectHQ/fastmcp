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
from opentelemetry.sdk.trace import (
    ReadableSpan,
    Span,
    SpanLimits,
    SpanProcessor,
    TracerProvider,
)
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import Decision, Sampler, SamplingResult
from opentelemetry.trace import StatusCode
from opentelemetry.util import types as otel_types

from fastmcp import Client, Context, FastMCP
from fastmcp.client.sampling import RequestContext, SamplingMessage, SamplingParams


class OnStartRecorder(SpanProcessor):
    def __init__(self) -> None:
        self.attributes: dict[str, dict[str, object]] = {}

    def on_start(self, span: Span, parent_context: OTelContext | None = None) -> None:
        self.attributes[span.name] = dict(span.attributes or {})


class NonForwardingSampler(Sampler):
    """Samples every span but never forwards the attributes it was handed.

    See `tests/telemetry/test_span_attributes.py` for the full explanation:
    OTel's `Tracer.start_span` builds the finished span from
    `sampling_result.attributes`, not from the `attributes` kwarg passed to
    `start_as_current_span`, so a custom sampler like this one reproduces the
    regression where a non-forwarding sampler silently drops FastMCP's
    attributes.
    """

    def should_sample(
        self,
        parent_context: OTelContext | None,
        trace_id: int,
        name: str,
        kind: object = None,
        attributes: object = None,
        links: object = None,
        trace_state: object = None,
    ) -> SamplingResult:
        return SamplingResult(Decision.RECORD_AND_SAMPLE)

    def get_description(self) -> str:
        return "NonForwardingSampler"


class RedactingSampler(Sampler):
    """Forwards the attributes it receives, but replaces `mcp.method.name`.

    See `tests/telemetry/test_span_attributes.py` for the full explanation:
    a sampler can legitimately keep an attribute FastMCP set while replacing
    its value (e.g. redacting the method name for privacy), and that decision
    must survive the restore step.
    """

    def should_sample(
        self,
        parent_context: OTelContext | None,
        trace_id: int,
        name: str,
        kind: object = None,
        attributes: otel_types.Attributes = None,
        links: object = None,
        trace_state: object = None,
    ) -> SamplingResult:
        forwarded = dict(attributes or {})
        forwarded["mcp.method.name"] = "REDACTED"
        return SamplingResult(Decision.RECORD_AND_SAMPLE, attributes=forwarded)

    def get_description(self) -> str:
        return "RedactingSampler"


class AttributeAddingSampler(Sampler):
    """Forwards the attributes it receives unchanged and adds its own."""

    def should_sample(
        self,
        parent_context: OTelContext | None,
        trace_id: int,
        name: str,
        kind: object = None,
        attributes: otel_types.Attributes = None,
        links: object = None,
        trace_state: object = None,
    ) -> SamplingResult:
        forwarded = dict(attributes or {})
        forwarded["sampling.policy"] = "always_on"
        return SamplingResult(Decision.RECORD_AND_SAMPLE, attributes=forwarded)

    def get_description(self) -> str:
        return "AttributeAddingSampler"


class FilteringSampler(Sampler):
    """Discards every attribute it receives and substitutes its own.

    See `tests/telemetry/test_span_attributes.py` for the full explanation:
    a sampler can legitimately supply only its own attributes (e.g. to strip
    component names or resource URIs for privacy or cardinality control),
    and the restore step must not reintroduce FastMCP's attributes in that
    case — doing so would defeat the filter.
    """

    def should_sample(
        self,
        parent_context: OTelContext | None,
        trace_id: int,
        name: str,
        kind: object = None,
        attributes: otel_types.Attributes = None,
        links: object = None,
        trace_state: object = None,
    ) -> SamplingResult:
        return SamplingResult(
            Decision.RECORD_AND_SAMPLE, attributes={"sampling.policy": "filtered"}
        )

    def get_description(self) -> str:
        return "FilteringSampler"


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


class TestAttributesSurviveANonForwardingSampler:
    """Regression: `sampling create_message` and `sampling tool ...` spans
    must keep FastMCP's attributes even when the configured Sampler doesn't
    forward the `attributes` it was handed to its `SamplingResult`.
    """

    @pytest.fixture
    def non_forwarding_recorder(
        self,
        monkeypatch: pytest.MonkeyPatch,
        trace_exporter: InMemorySpanExporter,
    ) -> OnStartRecorder:
        recorder = OnStartRecorder()
        provider = TracerProvider(sampler=NonForwardingSampler())
        provider.add_span_processor(recorder)
        provider.add_span_processor(SimpleSpanProcessor(trace_exporter))
        tracer = provider.get_tracer("test")
        monkeypatch.setattr("fastmcp.server.sampling.run.get_tracer", lambda: tracer)
        return recorder

    async def test_create_message_span_keeps_attributes(
        self,
        trace_exporter: InMemorySpanExporter,
        non_forwarding_recorder: OnStartRecorder,
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
        # The sampler never forwards attributes, so on_start legitimately sees
        # none — this documents that limitation rather than asserting around it.
        assert non_forwarding_recorder.attributes["sampling create_message"] == {}

    async def test_sampling_tool_span_keeps_attributes(
        self,
        trace_exporter: InMemorySpanExporter,
        non_forwarding_recorder: OnStartRecorder,
    ):
        from mcp_types import CreateMessageResultWithTools, ToolUseContent

        def echo_tool(text: str) -> str:
            return text

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
                            name="echo_tool",
                            input={"text": "hi"},
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
            result = await context.sample(messages="go", tools=[echo_tool])
            return result.text or ""

        async with Client(mcp) as client:
            await client.call_tool("driver", {})

        spans = _spans_named(trace_exporter, "sampling tool echo_tool")
        assert len(spans) == 1
        span = spans[0]
        assert span.attributes is not None
        assert span.attributes["gen_ai.tool.name"] == "echo_tool"
        assert span.attributes["fastmcp.tool.use_id"] == "call_1"
        # The sampler never forwards attributes, so on_start legitimately sees
        # none — this documents that limitation rather than asserting around it.
        assert non_forwarding_recorder.attributes["sampling tool echo_tool"] == {}


class TestAttributeRestoreRespectsSampler:
    """Restoring missing attributes must not clobber attributes a sampler
    deliberately kept and modified, must coexist with attributes a sampler
    adds of its own, and must not fire at all when a sampler supplies only
    its own attributes and drops FastMCP's entirely."""

    @pytest.fixture
    def redacting_tracer(
        self, monkeypatch: pytest.MonkeyPatch, trace_exporter: InMemorySpanExporter
    ) -> None:
        provider = TracerProvider(sampler=RedactingSampler())
        provider.add_span_processor(SimpleSpanProcessor(trace_exporter))
        tracer = provider.get_tracer("test")
        monkeypatch.setattr("fastmcp.server.sampling.run.get_tracer", lambda: tracer)

    @pytest.fixture
    def attribute_adding_tracer(
        self, monkeypatch: pytest.MonkeyPatch, trace_exporter: InMemorySpanExporter
    ) -> None:
        provider = TracerProvider(sampler=AttributeAddingSampler())
        provider.add_span_processor(SimpleSpanProcessor(trace_exporter))
        tracer = provider.get_tracer("test")
        monkeypatch.setattr("fastmcp.server.sampling.run.get_tracer", lambda: tracer)

    @pytest.fixture
    def filtering_tracer(
        self, monkeypatch: pytest.MonkeyPatch, trace_exporter: InMemorySpanExporter
    ) -> None:
        provider = TracerProvider(sampler=FilteringSampler())
        provider.add_span_processor(SimpleSpanProcessor(trace_exporter))
        tracer = provider.get_tracer("test")
        monkeypatch.setattr("fastmcp.server.sampling.run.get_tracer", lambda: tracer)

    async def test_create_message_span_keeps_redacted_value(
        self,
        trace_exporter: InMemorySpanExporter,
        redacting_tracer: None,
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
        # The redaction survives — it must not be overwritten by a restore.
        assert span.attributes["mcp.method.name"] == "REDACTED"
        # Attributes the sampler didn't touch are still present.
        assert span.attributes["fastmcp.server.name"] == "sampling-server"

    async def test_create_message_span_keeps_sampler_added_attribute(
        self,
        trace_exporter: InMemorySpanExporter,
        attribute_adding_tracer: None,
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
        assert span.attributes["sampling.policy"] == "always_on"
        assert span.attributes["mcp.method.name"] == "sampling/createMessage"
        assert span.attributes["fastmcp.server.name"] == "sampling-server"

    async def test_tool_span_keeps_redacted_value(
        self,
        trace_exporter: InMemorySpanExporter,
        redacting_tracer: None,
    ):
        from mcp_types import CreateMessageResultWithTools, ToolUseContent

        def echo_tool(text: str) -> str:
            return text

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
                            name="echo_tool",
                            input={"text": "hi"},
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
            result = await context.sample(messages="go", tools=[echo_tool])
            return result.text or ""

        async with Client(mcp) as client:
            await client.call_tool("driver", {})

        spans = _spans_named(trace_exporter, "sampling tool echo_tool")
        assert len(spans) == 1
        span = spans[0]
        assert span.attributes is not None
        # RedactingSampler only touches mcp.method.name, which this span
        # doesn't set — its attributes are forwarded unchanged, confirming
        # the restore step doesn't disturb them either.
        assert span.attributes["gen_ai.tool.name"] == "echo_tool"
        assert span.attributes["fastmcp.tool.use_id"] == "call_1"

    async def test_tool_span_keeps_sampler_added_attribute(
        self,
        trace_exporter: InMemorySpanExporter,
        attribute_adding_tracer: None,
    ):
        from mcp_types import CreateMessageResultWithTools, ToolUseContent

        def echo_tool(text: str) -> str:
            return text

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
                            name="echo_tool",
                            input={"text": "hi"},
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
            result = await context.sample(messages="go", tools=[echo_tool])
            return result.text or ""

        async with Client(mcp) as client:
            await client.call_tool("driver", {})

        spans = _spans_named(trace_exporter, "sampling tool echo_tool")
        assert len(spans) == 1
        span = spans[0]
        assert span.attributes is not None
        assert span.attributes["sampling.policy"] == "always_on"
        assert span.attributes["gen_ai.tool.name"] == "echo_tool"
        assert span.attributes["fastmcp.tool.use_id"] == "call_1"

    async def test_create_message_span_filtered_attributes_are_not_restored(
        self,
        trace_exporter: InMemorySpanExporter,
        filtering_tracer: None,
    ):
        """Regression: a sampler that intentionally supplies only its own
        attributes must not have FastMCP's attributes restored on top."""

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
        assert dict(span.attributes or {}) == {"sampling.policy": "filtered"}

    async def test_tool_span_filtered_attributes_are_not_restored(
        self,
        trace_exporter: InMemorySpanExporter,
        filtering_tracer: None,
    ):
        """Regression: a sampler that intentionally supplies only its own
        attributes must not have FastMCP's attributes restored on top."""
        from mcp_types import CreateMessageResultWithTools, ToolUseContent

        def echo_tool(text: str) -> str:
            return text

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
                            name="echo_tool",
                            input={"text": "hi"},
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
            result = await context.sample(messages="go", tools=[echo_tool])
            return result.text or ""

        async with Client(mcp) as client:
            await client.call_tool("driver", {})

        spans = _spans_named(trace_exporter, "sampling tool echo_tool")
        assert len(spans) == 1
        span = spans[0]
        assert dict(span.attributes or {}) == {"sampling.policy": "filtered"}


class TestRestoreDoesNotChurnAttributeLimitEvictions:
    """Regression: under a low `OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT`, the restore
    step in `fastmcp.server.sampling.run` must not reinsert an attribute the
    SDK's bounded attribute map already evicted.

    See `tests/telemetry/test_span_attributes.py::
    test_restore_does_not_churn_sdk_attribute_limit_evictions` for the full
    explanation: an evicted key looks identical to a sampler-omitted one from
    inside the restore helper, so reinserting it churns which attributes the
    SDK ultimately retains and inflates `dropped_attributes` beyond what the
    limit alone already cost. This proves the same discriminator holds
    end-to-end through the real sampling call path, not just at the helper
    level.
    """

    @staticmethod
    async def _run_create_message(
        span_limits: SpanLimits, *, stub_restore: bool
    ) -> ReadableSpan:
        with pytest.MonkeyPatch.context() as mp:
            exporter = InMemorySpanExporter()
            provider = TracerProvider(span_limits=span_limits)
            provider.add_span_processor(SimpleSpanProcessor(exporter))
            tracer = provider.get_tracer("test")
            mp.setattr("fastmcp.server.sampling.run.get_tracer", lambda: tracer)
            if stub_restore:
                mp.setattr(
                    "fastmcp.server.sampling.run.restore_dropped_attributes",
                    lambda span, attrs: None,
                )

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

            spans = _spans_named(exporter, "sampling create_message")
            assert len(spans) == 1
            return spans[0]

    @staticmethod
    async def _run_tool_span(
        span_limits: SpanLimits, *, stub_restore: bool
    ) -> ReadableSpan:
        from mcp_types import CreateMessageResultWithTools, ToolUseContent

        with pytest.MonkeyPatch.context() as mp:
            exporter = InMemorySpanExporter()
            provider = TracerProvider(span_limits=span_limits)
            provider.add_span_processor(SimpleSpanProcessor(exporter))
            tracer = provider.get_tracer("test")
            mp.setattr("fastmcp.server.sampling.run.get_tracer", lambda: tracer)
            if stub_restore:
                mp.setattr(
                    "fastmcp.server.sampling.run.restore_dropped_attributes",
                    lambda span, attrs: None,
                )

            def echo_tool(text: str) -> str:
                return text

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
                                name="echo_tool",
                                input={"text": "hi"},
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
                result = await context.sample(messages="go", tools=[echo_tool])
                return result.text or ""

            async with Client(mcp) as client:
                await client.call_tool("driver", {})

            spans = _spans_named(exporter, "sampling tool echo_tool")
            assert len(spans) == 1
            return spans[0]

    async def test_create_message_span(self, monkeypatch: pytest.MonkeyPatch):
        # Two attributes on this span (`mcp.method.name`, `fastmcp.server.name`)
        # — a limit of 1 guarantees eviction.
        monkeypatch.setenv("OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT", "1")
        span_limits = SpanLimits()
        assert span_limits.max_span_attributes == 1

        baseline = await self._run_create_message(span_limits, stub_restore=True)
        # The whole test is moot if the limit didn't actually bind.
        assert baseline.dropped_attributes > 0

        with_restore = await self._run_create_message(span_limits, stub_restore=False)

        assert dict(with_restore.attributes or {}) == dict(baseline.attributes or {})
        assert with_restore.dropped_attributes == baseline.dropped_attributes

    async def test_tool_span(self, monkeypatch: pytest.MonkeyPatch):
        # Two attributes on this span (`gen_ai.tool.name`, `fastmcp.tool.use_id`)
        # — a limit of 1 guarantees eviction.
        monkeypatch.setenv("OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT", "1")
        span_limits = SpanLimits()
        assert span_limits.max_span_attributes == 1

        baseline = await self._run_tool_span(span_limits, stub_restore=True)
        # The whole test is moot if the limit didn't actually bind.
        assert baseline.dropped_attributes > 0

        with_restore = await self._run_tool_span(span_limits, stub_restore=False)

        assert dict(with_restore.attributes or {}) == dict(baseline.attributes or {})
        assert with_restore.dropped_attributes == baseline.dropped_attributes
