"""Tests for provider-level OpenTelemetry tracing."""

from __future__ import annotations

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from fastmcp import Client, Context, FastMCP
from fastmcp.server.providers.proxy import FastMCPProxy, ProxyClient
from fastmcp.telemetry import TRACE_PARENT_KEY


class TestFastMCPProviderTracing:
    """Tests for FastMCPProvider delegation tracing."""

    async def test_mounted_tool_creates_delegate_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        # Create a child server with a tool
        child = FastMCP("child-server")

        @child.tool()
        def child_tool() -> str:
            return "child result"

        # Create parent server and mount child with namespace
        parent = FastMCP("parent-server")
        parent.mount(child, namespace="child")

        # Call the tool through the parent (namespace uses underscore, not slash)
        result = await parent.call_tool("child_child_tool", {})
        assert "child result" in str(result)

        # Check spans: should have parent tool span, delegate span, and child tool span
        spans = trace_exporter.get_finished_spans()
        span_names = [s.name for s in spans]

        # Parent server creates "tools/call child_child_tool"
        assert "tools/call child_child_tool" in span_names
        # FastMCPProvider creates "delegate child_tool"
        assert "delegate child_tool" in span_names
        # Child server creates "tools/call child_tool"
        assert "tools/call child_tool" in span_names

        # Verify delegate span has correct attributes
        delegate_span = next(s for s in spans if s.name == "delegate child_tool")
        assert delegate_span.attributes is not None
        assert delegate_span.attributes["fastmcp.provider.type"] == "FastMCPProvider"
        assert delegate_span.attributes["fastmcp.component.key"] == "child_tool"

    async def test_mounted_resource_creates_delegate_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        # Create a child server with a resource
        child = FastMCP("child-server")

        @child.resource("data://config")
        def child_config() -> str:
            return "config data"

        # Create parent server and mount child with namespace
        parent = FastMCP("parent-server")
        parent.mount(child, namespace="child")

        # Read the resource through the parent (namespace is in the URI path)
        result = await parent.read_resource("data://child/config")
        assert "config data" in str(result)

        spans = trace_exporter.get_finished_spans()
        span_names = [s.name for s in spans]

        # Should have delegate span for resource
        assert any(
            "delegate" in name and "data://config" in name for name in span_names
        )

    async def test_mounted_prompt_creates_delegate_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        # Create a child server with a prompt
        child = FastMCP("child-server")

        @child.prompt()
        def child_prompt() -> str:
            return "Hello from child!"

        # Create parent server and mount child with namespace
        parent = FastMCP("parent-server")
        parent.mount(child, namespace="child")

        # Render the prompt through the parent (namespace uses underscore)
        result = await parent.render_prompt("child_child_prompt", {})
        assert "Hello from child!" in str(result)

        spans = trace_exporter.get_finished_spans()
        span_names = [s.name for s in spans]

        # Should have delegate span for prompt
        assert "delegate child_prompt" in span_names

        # Verify delegate span has correct attributes
        delegate_span = next(s for s in spans if s.name == "delegate child_prompt")
        assert delegate_span.attributes is not None
        assert delegate_span.attributes["fastmcp.provider.type"] == "FastMCPProvider"


class TestProviderSpanHierarchy:
    """Tests for span parent-child relationships in mounted servers."""

    async def test_delegate_span_is_child_of_server_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        # Create nested server structure
        child = FastMCP("child")

        @child.tool()
        def greet() -> str:
            return "Hello"

        parent = FastMCP("parent")
        parent.mount(child, namespace="ns")

        await parent.call_tool("ns_greet", {})

        spans = trace_exporter.get_finished_spans()

        # Find the spans
        parent_span = next(s for s in spans if s.name == "tools/call ns_greet")
        delegate_span = next(s for s in spans if s.name == "delegate greet")
        child_span = next(s for s in spans if s.name == "tools/call greet")

        # Verify parent-child relationships
        assert delegate_span.parent is not None
        assert child_span.parent is not None
        assert delegate_span.parent.span_id == parent_span.context.span_id
        assert child_span.parent.span_id == delegate_span.context.span_id


class TestModernProxyTracePropagation:
    """A modern proxy relays resources and prompts through the low-level
    session so a backend guard's ask can surface (SEP-2322). That path skips
    the high-level client's trace injection, so the relay must stamp the
    outgoing `_meta` itself — otherwise every modern proxy read breaks the
    distributed trace, not only the guard rounds."""

    @staticmethod
    def _backend(seen: dict[str, dict]) -> FastMCP:
        backend = FastMCP("trace-backend")

        def record(kind: str, ctx: Context) -> None:
            rc = ctx.request_context
            seen[kind] = dict(rc.meta) if rc is not None and rc.meta else {}

        @backend.resource("data://x")
        async def concrete(ctx: Context) -> str:
            record("resource", ctx)
            return "ok"

        @backend.resource("data://{part}/y")
        async def templated(part: str, ctx: Context) -> str:
            record("template", ctx)
            return "ok"

        @backend.prompt
        async def greet(ctx: Context) -> str:
            record("prompt", ctx)
            return "ok"

        return backend

    async def test_traceparent_reaches_backend(
        self, trace_exporter: InMemorySpanExporter
    ):
        seen: dict[str, dict] = {}
        proxy = FastMCPProxy(
            client_factory=lambda: ProxyClient(self._backend(seen), mode="auto")
        )

        async with Client(proxy, mode="auto") as client:
            await client.read_resource("data://x")
            await client.read_resource("data://p/y")
            await client.get_prompt("greet")

        assert TRACE_PARENT_KEY in seen["resource"]
        assert TRACE_PARENT_KEY in seen["template"]
        assert TRACE_PARENT_KEY in seen["prompt"]


class TestOneServerSpanPerRequest:
    """A request crosses one server boundary, however deep the mounts go."""

    @staticmethod
    def _server_spans(exporter: InMemorySpanExporter) -> list:
        return [
            s
            for s in exporter.get_finished_spans()
            if s.kind == SpanKind.SERVER
            and s.attributes is not None
            and s.attributes.get("mcp.method.name") == "tools/call"
        ]

    async def test_mounted_call_over_a_client(
        self, trace_exporter: InMemorySpanExporter
    ):
        child = FastMCP("child")

        @child.tool
        def hello() -> str:
            return "world"

        parent = FastMCP("parent")
        parent.mount(child, namespace="sub")

        async with Client(parent) as client:
            await client.call_tool("sub_hello", {})

        spans = trace_exporter.get_finished_spans()
        (server,) = self._server_spans(trace_exporter)
        assert server.name == "tools/call sub_hello"
        delegate = next(s for s in spans if s.name == "delegate hello")
        child_span = next(s for s in spans if s.name == "tools/call hello")
        assert child_span.kind == SpanKind.INTERNAL
        assert child_span.parent is not None
        assert child_span.parent.span_id == delegate.context.span_id

    async def test_nested_mounts_in_process(self, trace_exporter: InMemorySpanExporter):
        leaf = FastMCP("leaf")

        @leaf.tool
        def hello() -> str:
            return "world"

        middle = FastMCP("middle")
        middle.mount(leaf, namespace="leaf")
        root = FastMCP("root")
        root.mount(middle, namespace="mid")

        await root.call_tool("mid_leaf_hello", {})

        (server,) = self._server_spans(trace_exporter)
        assert server.name == "tools/call mid_leaf_hello"

    async def test_tool_calling_a_tool(self, trace_exporter: InMemorySpanExporter):
        mcp = FastMCP("nested")

        @mcp.tool
        def inner() -> str:
            return "inner"

        @mcp.tool
        async def outer(ctx: Context) -> str:
            result = await ctx.fastmcp.call_tool("inner", {})
            return str(result.structured_content)

        async with Client(mcp) as client:
            await client.call_tool("outer", {})

        (server,) = self._server_spans(trace_exporter)
        assert server.name == "tools/call outer"

    async def test_proxied_backend_keeps_its_own_server_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        backend = FastMCP("backend")

        @backend.tool
        def hello() -> str:
            return "world"

        proxy = FastMCPProxy(client_factory=lambda: ProxyClient(backend))

        async with Client(proxy) as client:
            await client.call_tool("hello", {})

        names = sorted(s.name for s in self._server_spans(trace_exporter))
        assert names == ["tools/call hello", "tools/call hello"]
        backend_span = next(
            s
            for s in self._server_spans(trace_exporter)
            if s.attributes is not None
            and s.attributes.get("fastmcp.server.name") == "backend"
        )
        assert backend_span.attributes is not None
        assert backend_span.attributes.get("fastmcp.component.key") == "tool:hello@"
