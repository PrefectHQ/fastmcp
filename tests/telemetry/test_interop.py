"""Tests for FastMCP telemetry interoperability modes."""

from __future__ import annotations

import json
from typing import Any, cast

import httpx
from mcp.server.lowlevel.server import request_ctx
from opentelemetry import baggage, trace
from opentelemetry import context as otel_context
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from starlette.middleware import Middleware

from fastmcp import Client, FastMCP
from fastmcp.client.telemetry import client_span
from fastmcp.server.telemetry import server_span
from fastmcp.telemetry import inject_trace_context, suppress_fastmcp_telemetry
from fastmcp.utilities.tests import temporary_settings


class DummyReqCtx:
    """Minimal request context for server telemetry tests."""

    def __init__(self, meta: dict[str, str]):
        self.meta = meta
        self.request = None


class AmbientHTTPSpanMiddleware:
    """Minimal ASGI middleware that simulates outer HTTP instrumentation."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        tracer = trace.get_tracer("ambient-http")
        with tracer.start_as_current_span("ambient-http-request"):
            await self.app(scope, receive, send)


def parse_sse_response(body: str) -> dict[str, Any]:
    """Extract the first SSE data payload from a streamable HTTP response."""
    for line in body.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise AssertionError(f"Missing SSE data payload in response: {body!r}")


class TestClientInteropMode:
    async def test_propagation_only_mode_preserves_outer_client_span(
        self, trace_exporter: InMemorySpanExporter
    ):
        with temporary_settings(telemetry_mode="propagation_only"):
            tracer = trace.get_tracer("external")
            with tracer.start_as_current_span("external-client-parent") as parent_span:
                with client_span(
                    "tools/call weather",
                    "tools/call",
                    "weather",
                    tool_name="weather",
                ) as span:
                    meta = inject_trace_context()
                    assert not span.is_recording()

            spans = trace_exporter.get_finished_spans()
            assert [span.name for span in spans] == ["external-client-parent"]
            assert meta is not None
            assert meta["traceparent"].split("-")[2] == format(
                parent_span.get_span_context().span_id, "016x"
            )

    async def test_context_manager_suppresses_only_fastmcp_spans(
        self, trace_exporter: InMemorySpanExporter
    ):
        tracer = trace.get_tracer("external")
        with tracer.start_as_current_span("external-client-parent") as parent_span:
            with suppress_fastmcp_telemetry():
                with client_span(
                    "tools/call weather",
                    "tools/call",
                    "weather",
                    tool_name="weather",
                ) as span:
                    meta = inject_trace_context()
                    assert not span.is_recording()

        spans = trace_exporter.get_finished_spans()
        assert [span.name for span in spans] == ["external-client-parent"]
        assert meta is not None
        assert meta["traceparent"].split("-")[2] == format(
            parent_span.get_span_context().span_id, "016x"
        )

    async def test_end_to_end_propagation_only_suppresses_native_spans(
        self, trace_exporter: InMemorySpanExporter
    ):
        child = FastMCP("child-server")

        @child.tool()
        def child_tool() -> str:
            return "child result"

        parent = FastMCP("parent-server")
        parent.mount(child, namespace="child")

        with temporary_settings(telemetry_mode="propagation_only"):
            tracer = trace.get_tracer("external")
            with tracer.start_as_current_span("external-request"):
                client = Client(parent)
                async with client:
                    result = await client.call_tool("child_child_tool", {})
                    assert "child result" in str(result)

        spans = trace_exporter.get_finished_spans()
        assert [span.name for span in spans] == ["external-request"]


class TestServerInteropMode:
    async def test_streamable_http_third_party_client_uses_meta_parent_and_baggage(
        self,
        trace_exporter: InMemorySpanExporter,
    ):
        child = FastMCP("child-server")

        @child.tool()
        def tenant() -> str:
            tenant_name = baggage.get_baggage("tenant")
            return tenant_name if isinstance(tenant_name, str) else "missing"

        parent = FastMCP("parent-server")
        parent.mount(child, namespace="child")

        app = parent.http_app(
            transport="http",
            path="/mcp",
            middleware=[Middleware(AmbientHTTPSpanMiddleware)],
        )
        headers = {
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
        }

        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                init_response = await client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {},
                            "clientInfo": {
                                "name": "opaque-client",
                                "version": "0.1.0",
                            },
                        },
                    },
                )
                session_id = init_response.headers["mcp-session-id"]
                await client.post(
                    "/mcp",
                    headers={**headers, "mcp-session-id": session_id},
                    json={
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                        "params": {},
                    },
                )

                trace_exporter.clear()
                tracer = trace.get_tracer("opaque-client")
                baggage_token = otel_context.attach(
                    baggage.set_baggage("tenant", "acme")
                )
                try:
                    with tracer.start_as_current_span(
                        "opaque-client-root"
                    ) as client_span_export:
                        meta = inject_trace_context()
                        tool_response = await client.post(
                            "/mcp",
                            headers={**headers, "mcp-session-id": session_id},
                            json={
                                "jsonrpc": "2.0",
                                "id": 2,
                                "method": "tools/call",
                                "params": {
                                    "name": "child_tenant",
                                    "arguments": {},
                                    "_meta": meta,
                                },
                            },
                        )
                finally:
                    otel_context.detach(baggage_token)

        payload = parse_sse_response(tool_response.text)
        assert payload["result"]["content"][0]["text"] == "acme"

        spans = {
            span.name: span
            for span in trace_exporter.get_finished_spans()
            if span.name
            in {
                "ambient-http-request",
                "opaque-client-root",
                "tools/call child_tenant",
                "delegate tenant",
                "tools/call tenant",
            }
        }
        parent_span_export = spans["tools/call child_tenant"]
        delegate_span_export = spans["delegate tenant"]
        child_span_export = spans["tools/call tenant"]
        ambient_span_export = spans["ambient-http-request"]

        assert parent_span_export.parent is not None
        assert (
            parent_span_export.parent.span_id
            == client_span_export.get_span_context().span_id
        )
        assert any(
            link.context.span_id == ambient_span_export.get_span_context().span_id
            for link in parent_span_export.links
        )
        assert child_span_export.parent is not None
        assert (
            child_span_export.parent.span_id
            == client_span_export.get_span_context().span_id
        )
        assert delegate_span_export.parent is not None
        assert (
            delegate_span_export.parent.span_id
            == parent_span_export.get_span_context().span_id
        )
        assert any(
            link.context.span_id == ambient_span_export.get_span_context().span_id
            for link in child_span_export.links
        )

    async def test_server_span_makes_propagated_baggage_current(
        self,
        monkeypatch,
        trace_exporter: InMemorySpanExporter,
    ):
        import fastmcp.server.telemetry as server_telemetry

        monkeypatch.setattr(server_telemetry, "get_auth_span_attributes", lambda: {})
        monkeypatch.setattr(server_telemetry, "get_session_span_attributes", lambda: {})

        tracer = trace.get_tracer("external")
        baggage_token = otel_context.attach(baggage.set_baggage("tenant", "acme"))
        try:
            with tracer.start_as_current_span("external-client-parent"):
                meta = inject_trace_context()
        finally:
            otel_context.detach(baggage_token)

        req_token = request_ctx.set(cast(Any, DummyReqCtx(meta or {})))
        try:
            with server_span(
                "tools/call weather",
                "tools/call",
                "test-server",
                "tool",
                "weather",
                tool_name="weather",
            ):
                assert baggage.get_baggage("tenant") == "acme"
        finally:
            request_ctx.reset(req_token)

    async def test_server_span_with_baggage_only_meta_keeps_ambient_parent(
        self,
        monkeypatch,
        trace_exporter: InMemorySpanExporter,
    ):
        import fastmcp.server.telemetry as server_telemetry

        monkeypatch.setattr(server_telemetry, "get_auth_span_attributes", lambda: {})
        monkeypatch.setattr(server_telemetry, "get_session_span_attributes", lambda: {})

        with trace.get_tracer("external").start_as_current_span(
            "ambient-http-request"
        ) as ambient_span:
            req_token = request_ctx.set(
                cast(Any, DummyReqCtx({"baggage": "userId=alice"}))
            )
            try:
                with server_span(
                    "tools/call weather",
                    "tools/call",
                    "test-server",
                    "tool",
                    "weather",
                    tool_name="weather",
                ):
                    pass
            finally:
                request_ctx.reset(req_token)

        spans = {
            span.name: span
            for span in trace_exporter.get_finished_spans()
            if span.name in {"ambient-http-request", "tools/call weather"}
        }
        server_span_export = spans["tools/call weather"]

        assert server_span_export.parent is not None
        assert (
            server_span_export.parent.span_id == ambient_span.get_span_context().span_id
        )
        assert server_span_export.links == ()

    async def test_server_span_uses_meta_parent_and_links_ambient_context(
        self,
        monkeypatch,
        trace_exporter: InMemorySpanExporter,
    ):
        import fastmcp.server.telemetry as server_telemetry

        monkeypatch.setattr(server_telemetry, "get_auth_span_attributes", lambda: {})
        monkeypatch.setattr(server_telemetry, "get_session_span_attributes", lambda: {})

        tracer = trace.get_tracer("external")

        remote_parent = tracer.start_span("external-client-parent")
        token = otel_context.attach(trace.set_span_in_context(remote_parent))
        try:
            meta = inject_trace_context()
        finally:
            otel_context.detach(token)

        with tracer.start_as_current_span("ambient-http-request") as ambient_span:
            req_token = request_ctx.set(cast(Any, DummyReqCtx(meta or {})))
            try:
                with server_span(
                    "tools/call weather",
                    "tools/call",
                    "test-server",
                    "tool",
                    "weather",
                    tool_name="weather",
                ):
                    pass
            finally:
                request_ctx.reset(req_token)

        remote_parent.end()

        spans = {
            span.name: span
            for span in trace_exporter.get_finished_spans()
            if span.name
            in {"external-client-parent", "ambient-http-request", "tools/call weather"}
        }
        server_span_export = spans["tools/call weather"]

        assert server_span_export.parent is not None
        assert (
            server_span_export.parent.span_id
            == remote_parent.get_span_context().span_id
        )
        assert any(
            link.context.span_id == ambient_span.get_span_context().span_id
            for link in server_span_export.links
        )
