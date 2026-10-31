"""Tests for server-level OpenTelemetry tracing."""

from __future__ import annotations

from contextlib import suppress
from unittest.mock import patch

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Span, SpanKind, StatusCode

import fastmcp
from fastmcp import Client, Context, FastMCP
from fastmcp.exceptions import NotFoundError, ToolError
from fastmcp.server.auth import AccessToken
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext


class TestToolTracing:
    async def test_call_tool_creates_span(self, trace_exporter: InMemorySpanExporter):
        mcp = FastMCP("test-server")

        @mcp.tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        result = await mcp.call_tool("greet", {"name": "World"})
        assert "Hello, World!" in str(result)

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.name == "tools/call greet"
        assert span.kind == SpanKind.SERVER
        assert span.attributes is not None
        # Standard MCP semantic conventions
        assert span.attributes["mcp.method.name"] == "tools/call"
        # gen_ai semantic conventions
        assert span.attributes["gen_ai.tool.name"] == "greet"
        # RPC attributes must NOT be present
        assert "rpc.system" not in span.attributes
        assert "rpc.service" not in span.attributes
        assert "rpc.method" not in span.attributes
        # FastMCP-specific attributes
        assert span.attributes["fastmcp.server.name"] == "test-server"
        assert span.attributes["fastmcp.component.type"] == "tool"
        assert span.attributes["fastmcp.component.key"] == "tool:greet@"

    async def test_call_tool_with_error_sets_status(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        @mcp.tool()
        def failing_tool() -> str:
            raise ValueError("Something went wrong")

        with pytest.raises(ToolError):
            await mcp.call_tool("failing_tool", {})

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.name == "tools/call failing_tool"
        assert span.status.status_code == StatusCode.ERROR
        assert span.status.description is not None
        assert "Something went wrong" in span.status.description
        assert span.attributes is not None
        assert span.attributes["error.type"] == "tool_error"
        assert len(span.events) > 0  # Exception recorded

    async def test_call_nonexistent_tool_sets_error(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        with pytest.raises(NotFoundError):
            await mcp.call_tool("nonexistent", {})

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.name == "tools/call nonexistent"
        assert span.status.status_code == StatusCode.ERROR
        assert span.attributes is not None
        # NotFoundError is not a ToolError, so uses class name as fallback
        assert span.attributes["error.type"] == "NotFoundError"


class TestResourceTracing:
    async def test_read_resource_creates_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        @mcp.resource("config://app")
        def get_config() -> str:
            return "app_config_data"

        result = await mcp.read_resource("config://app")
        assert "app_config_data" in str(result)

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.name == "resources/read"
        assert span.kind == SpanKind.SERVER
        assert span.attributes is not None
        # Standard MCP semantic conventions
        assert span.attributes["mcp.method.name"] == "resources/read"
        assert span.attributes["mcp.resource.uri"] == "config://app"
        # RPC attributes must NOT be present
        assert "rpc.system" not in span.attributes
        assert "rpc.service" not in span.attributes
        assert "rpc.method" not in span.attributes
        # FastMCP-specific attributes
        assert span.attributes["fastmcp.server.name"] == "test-server"
        assert span.attributes["fastmcp.component.type"] == "resource"
        assert span.attributes["fastmcp.component.key"] == "resource:config://app@"

    async def test_read_resource_template_creates_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        @mcp.resource("users://{user_id}/profile")
        def get_user_profile(user_id: str) -> str:
            return f"profile for {user_id}"

        result = await mcp.read_resource("users://123/profile")
        assert "profile for 123" in str(result)

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.name == "resources/read"
        assert span.kind == SpanKind.SERVER
        assert span.attributes is not None
        # Standard MCP semantic conventions
        assert span.attributes["mcp.method.name"] == "resources/read"
        assert span.attributes["mcp.resource.uri"] == "users://123/profile"
        # RPC attributes must NOT be present
        assert "rpc.system" not in span.attributes
        assert "rpc.method" not in span.attributes
        # Template component type is set by get_span_attributes
        assert span.attributes["fastmcp.component.type"] == "resource_template"
        assert (
            span.attributes["fastmcp.component.key"]
            == "template:users://{user_id}/profile@"
        )

    async def test_read_nonexistent_resource_sets_error(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        with pytest.raises(NotFoundError):
            await mcp.read_resource("nonexistent://resource")

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.name == "resources/read"
        assert span.status.status_code == StatusCode.ERROR


class TestPromptTracing:
    async def test_render_prompt_creates_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        @mcp.prompt()
        def greeting(name: str) -> str:
            return f"Hello, {name}!"

        result = await mcp.render_prompt("greeting", {"name": "World"})
        assert "Hello, World!" in str(result)

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.name == "prompts/get greeting"
        assert span.kind == SpanKind.SERVER
        assert span.attributes is not None
        # Standard MCP semantic conventions
        assert span.attributes["mcp.method.name"] == "prompts/get"
        # gen_ai semantic conventions
        assert span.attributes["gen_ai.prompt.name"] == "greeting"
        # RPC attributes must NOT be present
        assert "rpc.system" not in span.attributes
        assert "rpc.service" not in span.attributes
        assert "rpc.method" not in span.attributes
        # FastMCP-specific attributes
        assert span.attributes["fastmcp.server.name"] == "test-server"
        assert span.attributes["fastmcp.component.type"] == "prompt"
        assert span.attributes["fastmcp.component.key"] == "prompt:greeting@"

    async def test_render_nonexistent_prompt_sets_error(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        with pytest.raises(NotFoundError):
            await mcp.render_prompt("nonexistent", {})

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.name == "prompts/get nonexistent"
        assert span.status.status_code == StatusCode.ERROR


class TestAuthAttributesOnSpans:
    async def test_tool_span_includes_auth_attributes_when_authenticated(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        @mcp.tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        test_token = AccessToken(
            token="test-token",
            client_id="test-client-123",
            scopes=["read", "write"],
        )

        with patch(
            "fastmcp.server.dependencies.get_access_token", return_value=test_token
        ):
            await mcp.call_tool("greet", {"name": "World"})

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.attributes is not None
        assert span.attributes["enduser.id"] == "test-client-123"
        assert span.attributes["enduser.scope"] == "read write"

    async def test_resource_span_includes_auth_attributes_when_authenticated(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        @mcp.resource("config://app")
        def get_config() -> str:
            return "config_data"

        test_token = AccessToken(
            token="test-token",
            client_id="user-456",
            scopes=["config:read"],
        )

        with patch(
            "fastmcp.server.dependencies.get_access_token", return_value=test_token
        ):
            await mcp.read_resource("config://app")

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.attributes is not None
        assert span.attributes["enduser.id"] == "user-456"
        assert span.attributes["enduser.scope"] == "config:read"

    async def test_prompt_span_includes_auth_attributes_when_authenticated(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        @mcp.prompt()
        def greeting(name: str) -> str:
            return f"Hello, {name}!"

        test_token = AccessToken(
            token="test-token",
            client_id="prompt-user",
            scopes=["prompts"],
        )

        with patch(
            "fastmcp.server.dependencies.get_access_token", return_value=test_token
        ):
            await mcp.render_prompt("greeting", {"name": "World"})

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.attributes is not None
        assert span.attributes["enduser.id"] == "prompt-user"
        assert span.attributes["enduser.scope"] == "prompts"

    async def test_span_omits_auth_attributes_when_not_authenticated(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        @mcp.tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        # No mock - get_access_token returns None by default (no auth context)
        await mcp.call_tool("greet", {"name": "World"})

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.attributes is not None
        # Auth attributes should not be present
        assert "enduser.id" not in span.attributes
        assert "enduser.scope" not in span.attributes

    async def test_span_omits_scope_when_no_scopes(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        @mcp.tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        test_token = AccessToken(
            token="test-token",
            client_id="client-no-scopes",
            scopes=[],  # Empty scopes
        )

        with patch(
            "fastmcp.server.dependencies.get_access_token", return_value=test_token
        ):
            await mcp.call_tool("greet", {"name": "World"})

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.attributes is not None
        assert span.attributes["enduser.id"] == "client-no-scopes"
        # Scope attribute should not be present when scopes list is empty
        assert "enduser.scope" not in span.attributes


class TestSingleServerSpan:
    """Full-dispatch tests guarding against duplicate SERVER spans.

    The SDK seeds its own `OpenTelemetryMiddleware` into every lowlevel server,
    which would emit a second SERVER span per request alongside FastMCP's. These
    tests exercise the real request path (through a `Client`) so a regression
    that re-enables the SDK middleware would surface as an extra SERVER span.
    Note the in-process `mcp.call_tool` tests above bypass the dispatcher and so
    cannot catch this.
    """

    async def test_tool_call_emits_single_server_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        @mcp.tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        async with Client(mcp) as client:
            await client.call_tool("greet", {"name": "World"})

        spans = trace_exporter.get_finished_spans()
        # Exactly one SERVER span for the tools/call request. Match by method name
        # (not just the "tools/call greet" name) so a seam-level span accidentally
        # opened for this high-level method — which would be named "tools/call" —
        # is also counted and would trip the assertion.
        tool_call_server_spans = [
            s
            for s in spans
            if s.kind == SpanKind.SERVER
            and s.attributes is not None
            and s.attributes.get("mcp.method.name") == "tools/call"
        ]
        assert len(tool_call_server_spans) == 1
        span = tool_call_server_spans[0]
        assert span.name == "tools/call greet"
        # The single span carries the rich component attributes that the
        # high-level path enriches the seam span with (attribute parity: the
        # enrichment must not lose the component context by opening a second
        # span or by leaving the seam span bare).
        assert span.attributes is not None
        assert span.attributes["fastmcp.component.key"] == "tool:greet@"
        assert span.attributes["gen_ai.tool.name"] == "greet"
        assert span.attributes["fastmcp.component.type"] == "tool"

    async def test_server_span_shares_client_trace(
        self, trace_exporter: InMemorySpanExporter
    ):
        """FastMCP extracts inbound W3C trace context from `_meta`, so the single
        SERVER span must still join the client's distributed trace."""
        mcp = FastMCP("test-server")

        @mcp.tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        async with Client(mcp) as client:
            await client.call_tool("greet", {"name": "World"})

        spans = trace_exporter.get_finished_spans()
        server_span = next(
            s
            for s in spans
            if s.kind == SpanKind.SERVER and s.name == "tools/call greet"
        )
        client_span = next(
            s
            for s in spans
            if s.kind == SpanKind.CLIENT and s.name == "MCP send tools/call greet"
        )
        assert server_span.context is not None
        assert client_span.context is not None
        assert server_span.context.trace_id == client_span.context.trace_id
        assert server_span.parent is not None


class TestSeamServerSpan:
    """SERVER spans for methods outside the high-level tool/resource/prompt path.

    FastMCP's rich SERVER spans are created deep in the high-level path, so
    methods like `logging/setLevel`, `tasks/*`, `ping`, and `initialize` — which
    never reach that code — would have no span at all once the SDK's
    `OpenTelemetryMiddleware` is removed. The FastMCP middleware seam
    (`FastMCPServerMiddleware`) emits a span for exactly those methods, so every
    request method carries a SERVER span again.
    """

    async def test_set_logging_level_emits_seam_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        # `logging/setLevel` was dropped from the modern protocol version
        # (SEP-2577), so exercising it needs the older protocol.
        async with Client(mcp, mode="legacy") as client:
            await client.set_logging_level("info")

        spans = trace_exporter.get_finished_spans()
        seam_spans = [
            s
            for s in spans
            if s.kind == SpanKind.SERVER and s.name == "logging/setLevel"
        ]
        assert len(seam_spans) == 1
        span = seam_spans[0]
        assert span.attributes is not None
        assert span.attributes["mcp.method.name"] == "logging/setLevel"
        assert span.attributes["fastmcp.server.name"] == "test-server"

    async def test_seam_method_emits_single_server_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        """A seam-spanned method must produce exactly one SERVER span, not two."""
        mcp = FastMCP("test-server")

        # `logging/setLevel` only exists on the older protocol; see the pin
        # note in `test_set_logging_level_emits_seam_span` above.
        async with Client(mcp, mode="legacy") as client:
            await client.set_logging_level("info")

        spans = trace_exporter.get_finished_spans()
        set_level_server_spans = [
            s
            for s in spans
            if s.kind == SpanKind.SERVER
            and s.attributes is not None
            and s.attributes.get("mcp.method.name") == "logging/setLevel"
        ]
        assert len(set_level_server_spans) == 1


class TestFailurePathServerSpan:
    """A tools/call rejected before the high-level handler must still be traced.

    The rich SERVER span for tools/call is created deep in the high-level path,
    after FastMCP middleware runs. A request rejected *before* that point (a
    raising middleware, a not-found tool) never reaches it, so without the seam
    span such failures would produce no SERVER span at all — the failure-path
    observability regression this guards against. The seam span opened by
    `FastMCPServerMiddleware` wraps the whole request, so every tools/call
    carries a SERVER span with `mcp.method.name=tools/call`, and an exception
    that propagates past the handler marks that span's status as an error.
    """

    async def test_middleware_error_before_handler_emits_error_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        """An unexpected error raised in FastMCP middleware, before the
        high-level path, propagates to the seam span, which records it as an
        error — where previously this method produced no SERVER span at all."""

        class RaisingMiddleware(Middleware):
            async def on_call_tool(
                self,
                context: MiddlewareContext,
                call_next: CallNext,
            ):
                raise RuntimeError("rejected before handler")

        mcp = FastMCP("test-server", middleware=[RaisingMiddleware()])

        @mcp.tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        async with Client(mcp) as client:
            with pytest.raises(Exception):
                await client.call_tool("greet", {"name": "World"})

        spans = trace_exporter.get_finished_spans()
        tool_call_server_spans = [
            s
            for s in spans
            if s.kind == SpanKind.SERVER
            and s.attributes is not None
            and s.attributes.get("mcp.method.name") == "tools/call"
        ]
        assert len(tool_call_server_spans) == 1
        span = tool_call_server_spans[0]
        assert span.status.status_code == StatusCode.ERROR
        assert span.attributes is not None
        assert span.attributes["mcp.method.name"] == "tools/call"
        assert span.attributes["error.type"] == "RuntimeError"
        assert len(span.events) > 0  # exception recorded

    async def test_tool_visible_rejection_before_handler_still_spans(
        self, trace_exporter: InMemorySpanExporter
    ):
        """A tool-visible error raised in middleware is returned as an error
        result (correct MCP semantics, so the span status is unset), but the
        seam span still guarantees exactly one SERVER span for the tools/call —
        the request is no longer invisible to tracing."""

        class RejectingMiddleware(Middleware):
            async def on_call_tool(
                self,
                context: MiddlewareContext,
                call_next: CallNext,
            ):
                raise ToolError("rejected before handler")

        mcp = FastMCP("test-server", middleware=[RejectingMiddleware()])

        @mcp.tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        async with Client(mcp) as client:
            with pytest.raises(ToolError):
                await client.call_tool("greet", {"name": "World"})

        spans = trace_exporter.get_finished_spans()
        tool_call_server_spans = [
            s
            for s in spans
            if s.kind == SpanKind.SERVER
            and s.attributes is not None
            and s.attributes.get("mcp.method.name") == "tools/call"
        ]
        assert len(tool_call_server_spans) == 1
        assert (
            tool_call_server_spans[0].attributes is not None
            and tool_call_server_spans[0].attributes["mcp.method.name"] == "tools/call"
        )


class TestTelemetryEnabledByDefault:
    """Instrumentation is on by default and controllable via the off-switch.

    FastMCP uses only the OpenTelemetry API, so spans are created unconditionally
    and light up when an SDK is configured. `FASTMCP_TELEMETRY_MODE=off`
    (`fastmcp.settings.telemetry_mode`) turns span creation off entirely, so no
    FastMCP spans are exported even with an SDK configured.
    """

    async def test_spans_fire_by_default(self, trace_exporter: InMemorySpanExporter):
        """No opt-in required: a tool call produces a span out of the box."""
        assert fastmcp.settings.telemetry_mode == "native"

        mcp = FastMCP("test-server")

        @mcp.tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        await mcp.call_tool("greet", {"name": "World"})

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "tools/call greet"

    async def test_off_switch_suppresses_spans(
        self,
        trace_exporter: InMemorySpanExporter,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """With telemetry disabled, no spans are created even with an SDK
        configured (the exporter fixture installs one)."""
        monkeypatch.setattr(fastmcp.settings, "telemetry_mode", "off")

        mcp = FastMCP("test-server")

        @mcp.tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        result = await mcp.call_tool("greet", {"name": "World"})
        assert "Hello, World!" in str(result)

        spans = trace_exporter.get_finished_spans()
        assert len(spans) == 0

    async def test_off_switch_suppresses_spans_via_client(
        self,
        trace_exporter: InMemorySpanExporter,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """The off-switch suppresses every FastMCP span on the full request path
        (seam SERVER span and FastMCP CLIENT span alike).

        The SDK's own low-level `mcp-python-sdk` CLIENT spans ("MCP send ...")
        are governed by the user's OpenTelemetry SDK, not FastMCP's off-switch,
        so they may still appear — the assertion filters them out.
        """
        monkeypatch.setattr(fastmcp.settings, "telemetry_mode", "off")

        mcp = FastMCP("test-server")

        @mcp.tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        async with Client(mcp) as client:
            await client.call_tool("greet", {"name": "World"})

        spans = trace_exporter.get_finished_spans()
        fastmcp_spans = [
            s
            for s in spans
            if s.instrumentation_scope is not None
            and s.instrumentation_scope.name == "fastmcp"
        ]
        assert fastmcp_spans == []
        # In particular, no FastMCP SERVER span (FastMCP owns all SERVER spans).
        assert [s for s in spans if s.kind == SpanKind.SERVER] == []

    async def test_off_switch_leaves_enclosing_span_current(
        self,
        trace_exporter: InMemorySpanExporter,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Disabling telemetry must be a transparent pass-through.

        The stock OpenTelemetry `NoOpTracer.start_as_current_span` attaches a
        `NonRecordingSpan` as the current span, which would hijack the trace
        context from an enclosing application span. With FastMCP's off-switch,
        `trace.get_current_span()` inside a handler must still return the
        caller's enclosing span, and attributes written there must land on it.
        """
        monkeypatch.setattr(fastmcp.settings, "telemetry_mode", "off")

        tracer = trace.get_tracer("test-enclosing")
        captured: dict[str, Span] = {}

        mcp = FastMCP("test-server")

        @mcp.tool()
        def annotate() -> str:
            current = trace.get_current_span()
            captured["current"] = current
            current.set_attribute("tool.touched", True)
            return "ok"

        with tracer.start_as_current_span("enclosing-app-span") as enclosing:
            await mcp.call_tool("annotate", {})
            # The tool ran with the enclosing span still current — FastMCP did
            # not attach a replacement (non-recording) span.
            assert captured["current"] is enclosing
            assert enclosing.is_recording()

        spans = trace_exporter.get_finished_spans()
        app_spans = [s for s in spans if s.name == "enclosing-app-span"]
        assert len(app_spans) == 1
        assert app_spans[0].attributes is not None
        assert app_spans[0].attributes["tool.touched"] is True
        # No FastMCP spans were exported.
        fastmcp_spans = [
            s
            for s in spans
            if s.instrumentation_scope is not None
            and s.instrumentation_scope.name == "fastmcp"
        ]
        assert fastmcp_spans == []


class TestProtocolVersionAttribute:
    """The SERVER span carries `mcp.protocol.version`, matching the SDK.

    FastMCP drops the SDK's `OpenTelemetryMiddleware` to avoid a duplicate SERVER
    span, so it re-emits the SDK's `mcp.protocol.version` attribute on its own
    span for parity.
    """

    async def test_tool_call_span_has_protocol_version(
        self, trace_exporter: InMemorySpanExporter
    ):
        mcp = FastMCP("test-server")

        @mcp.tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        async with Client(mcp) as client:
            await client.call_tool("greet", {"name": "World"})

        spans = trace_exporter.get_finished_spans()
        server_span = next(
            s
            for s in spans
            if s.kind == SpanKind.SERVER
            and s.attributes is not None
            and s.attributes.get("mcp.method.name") == "tools/call"
        )
        assert server_span.attributes is not None
        version = server_span.attributes.get("mcp.protocol.version")
        assert isinstance(version, str)
        assert version

    async def test_seam_span_has_protocol_version(
        self, trace_exporter: InMemorySpanExporter
    ):
        """Seam-only methods (never reaching the high-level path) also carry the
        protocol version.

        Pinned to legacy: `logging/setLevel` is a handshake-era seam method the
        modern (2026-07-28) protocol drops, so the span exists only on legacy.
        """
        mcp = FastMCP("test-server")

        async with Client(mcp, mode="legacy") as client:
            await client.set_logging_level("info")

        spans = trace_exporter.get_finished_spans()
        seam_span = next(
            s
            for s in spans
            if s.kind == SpanKind.SERVER and s.name == "logging/setLevel"
        )
        assert seam_span.attributes is not None
        version = seam_span.attributes.get("mcp.protocol.version")
        assert isinstance(version, str)
        assert version


class TestNestedCallToolSpans:
    """A tool that calls ctx.fastmcp.call_tool() must produce a span for each call.

    Before the fix, the outer tool's seam span remained the active seam span
    inside the tool body, so the nested call_tool's server_span enriched the
    outer span instead of opening a new child span. The outer span ended up
    named after the inner tool and the outer tool had no span at all.

    Issue: https://github.com/jlowin/fastmcp/issues/6009
    """

    async def test_nested_call_tool_emits_both_spans(
        self, trace_exporter: InMemorySpanExporter
    ):
        """Outer tool calling ctx.fastmcp.call_tool() produces two SERVER spans."""
        mcp = FastMCP("test-server")

        @mcp.tool
        def boom() -> str:
            raise ToolError("boom")

        @mcp.tool
        async def outer(ctx: Context) -> str:
            with suppress(ToolError):
                await ctx.fastmcp.call_tool("boom", {})
            return "done"

        async with Client(mcp) as client:
            await client.call_tool("outer", {})

        spans = trace_exporter.get_finished_spans()
        tool_call_server_spans = [
            s
            for s in spans
            if s.kind == SpanKind.SERVER
            and s.attributes is not None
            and s.attributes.get("mcp.method.name") == "tools/call"
        ]

        span_names = {s.name for s in tool_call_server_spans}
        assert "tools/call outer" in span_names, (
            f"Missing 'tools/call outer' span; found: {span_names}"
        )
        assert "tools/call boom" in span_names, (
            f"Missing 'tools/call boom' span; found: {span_names}"
        )
        assert len(tool_call_server_spans) == 2

    async def test_outer_span_status_unset_inner_span_status_error(
        self, trace_exporter: InMemorySpanExporter
    ):
        """Outer span is UNSET (success); inner boom span is ERROR."""
        mcp = FastMCP("test-server")

        @mcp.tool
        def boom() -> str:
            raise ToolError("boom")

        @mcp.tool
        async def outer(ctx: Context) -> str:
            with suppress(ToolError):
                await ctx.fastmcp.call_tool("boom", {})
            return "done"

        async with Client(mcp) as client:
            await client.call_tool("outer", {})

        spans = trace_exporter.get_finished_spans()
        tool_call_server_spans = {
            s.name: s
            for s in spans
            if s.kind == SpanKind.SERVER
            and s.attributes is not None
            and s.attributes.get("mcp.method.name") == "tools/call"
        }

        outer_span = tool_call_server_spans.get("tools/call outer")
        boom_span = tool_call_server_spans.get("tools/call boom")
        assert outer_span is not None
        assert boom_span is not None

        assert outer_span.status.status_code == StatusCode.UNSET
        assert boom_span.status.status_code == StatusCode.ERROR

    async def test_nested_call_tool_span_is_child_of_outer(
        self, trace_exporter: InMemorySpanExporter
    ):
        """The inner tool's span must be a descendant of the outer tool's span."""
        mcp = FastMCP("test-server")

        @mcp.tool
        def inner() -> str:
            return "inner result"

        @mcp.tool
        async def outer(ctx: Context) -> str:
            await ctx.fastmcp.call_tool("inner", {})
            return "outer result"

        async with Client(mcp) as client:
            await client.call_tool("outer", {})

        spans = trace_exporter.get_finished_spans()
        tool_call_server_spans = {
            s.name: s
            for s in spans
            if s.kind == SpanKind.SERVER
            and s.attributes is not None
            and s.attributes.get("mcp.method.name") == "tools/call"
        }

        outer_span = tool_call_server_spans.get("tools/call outer")
        inner_span = tool_call_server_spans.get("tools/call inner")
        assert outer_span is not None
        assert inner_span is not None

        assert outer_span.context is not None
        assert inner_span.context is not None
        # All spans share a trace
        assert outer_span.context.trace_id == inner_span.context.trace_id
        # The inner span descends from the outer span
        assert inner_span.parent is not None
        assert inner_span.parent.span_id == outer_span.context.span_id

        # Each span carries component attributes for its own tool, not the
        # other's. Before the fix the seam span was mutated in-place so the
        # outer span ended up with the inner tool's component attrs.
        assert outer_span.attributes is not None
        assert inner_span.attributes is not None
        assert outer_span.attributes.get("fastmcp.component.key") == "tool:outer@"
        assert outer_span.attributes.get("gen_ai.tool.name") == "outer"
        assert inner_span.attributes.get("fastmcp.component.key") == "tool:inner@"
        assert inner_span.attributes.get("gen_ai.tool.name") == "inner"

    async def test_nested_call_tool_ignores_inbound_meta_traceparent(
        self, trace_exporter: InMemorySpanExporter
    ):
        """Inner span must be a child of the outer span even when the outer
        request carries an inbound _meta traceparent (W3C distributed trace).

        Regression guard: extract_trace_context already skips the _meta
        traceparent when a valid span is active (so a live outer span wins over
        the request's remote-caller context). If that guard were removed, the
        non-seam server_span path would re-read _meta on the nested call and
        parent the inner span to the remote caller, turning nested spans into
        siblings.
        """
        mcp = FastMCP("test-server")

        @mcp.tool
        def inner() -> str:
            return "inner result"

        @mcp.tool
        async def outer(ctx: Context) -> str:
            await ctx.fastmcp.call_tool("inner", {})
            return "outer result"

        # Call via a Client so the client-telemetry layer injects a real
        # traceparent into _meta, mirroring the distributed-trace case where
        # an upstream caller's trace context arrives on the request.
        async with Client(mcp) as client:
            await client.call_tool("outer", {})

        spans = trace_exporter.get_finished_spans()
        tool_call_server_spans = {
            s.name: s
            for s in spans
            if s.kind == SpanKind.SERVER
            and s.attributes is not None
            and s.attributes.get("mcp.method.name") == "tools/call"
        }

        outer_span = tool_call_server_spans.get("tools/call outer")
        inner_span = tool_call_server_spans.get("tools/call inner")
        assert outer_span is not None
        assert inner_span is not None

        assert outer_span.context is not None
        assert inner_span.context is not None

        # The inner span must be a child of the outer span, not a sibling
        # parented directly to the inbound traceparent span. Without the
        # extract_trace_context guard (is_valid check), this would fail
        # because _get_parent_trace_context() would re-read _meta and return
        # the client send span as the parent.
        assert inner_span.parent is not None
        assert inner_span.parent.span_id == outer_span.context.span_id

        # The outer span's parent is the client send span (from _meta), not
        # the inner span -- confirm the hierarchy is outer -> inner, not flat.
        assert outer_span.parent is not None
        assert outer_span.parent.span_id != inner_span.context.span_id
