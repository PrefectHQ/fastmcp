"""`_meta` trace context parents the SERVER span; an ambient HTTP span is linked."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from mcp.shared import jsonrpc_dispatcher
from opentelemetry import propagate, trace
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from starlette.middleware import Middleware
from starlette.types import ASGIApp, Receive, Scope, Send

import fastmcp
from fastmcp import Client, FastMCP
from fastmcp.client.mixins import tools as client_tools
from fastmcp.server.dependencies import FastMCPRequestContext, fastmcp_request_ctx
from fastmcp.server.telemetry import _get_parent_trace_context
from fastmcp.utilities.tests import asgi_server

TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


class AsgiSpanMiddleware:
    """Opens an HTTP span from request headers, like OTel ASGI instrumentation."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode(): v.decode() for k, v in scope["headers"]}
        with trace.get_tracer("test").start_as_current_span(
            "HTTP POST", context=propagate.extract(headers), kind=SpanKind.SERVER
        ):
            await self.app(scope, receive, send)


def _server() -> FastMCP:
    mcp = FastMCP("test-server")

    @mcp.tool()
    def greet(name: str) -> str:
        with trace.get_tracer("test").start_as_current_span("user-work"):
            return f"Hello, {name}!"

    return mcp


def _span(spans: tuple[ReadableSpan, ...], name: str) -> ReadableSpan:
    return next(s for s in spans if s.name == name)


def _span_ids(spans: tuple[ReadableSpan, ...], name: str) -> set[int]:
    return {s.context.span_id for s in spans if s.name == name and s.context}


async def _call_over_http(**http_app_kwargs: Any) -> None:
    async with (
        asgi_server(
            _server(), middleware=[Middleware(AsgiSpanMiddleware)], **http_app_kwargs
        ) as server,
        server.client() as client,
    ):
        await client.call_tool("greet", {"name": "World"})


@pytest.mark.parametrize("stateless_http", [False, True])
@pytest.mark.parametrize("method", ["tools/call greet", "tools/list"])
async def test_meta_is_parent_and_http_span_is_linked(
    trace_exporter: InMemorySpanExporter, stateless_http: bool, method: str
):
    await _call_over_http(stateless_http=stateless_http)

    spans = trace_exporter.get_finished_spans()
    server = next(s for s in spans if s.name == method and s.kind == SpanKind.SERVER)
    client_send = _span(spans, f"MCP send {method}")
    assert server.parent is not None and client_send.context is not None
    assert server.parent.span_id == client_send.context.span_id
    assert len(server.links) == 1
    assert server.links[0].context.span_id in _span_ids(spans, "HTTP POST")
    assert server.links[0].attributes == {"fastmcp.link.kind": "http_transport"}


async def test_http_span_is_parent_without_meta(
    trace_exporter: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(client_tools, "inject_trace_context", lambda meta: meta)
    monkeypatch.setattr(jsonrpc_dispatcher, "inject_trace_context", lambda meta: None)
    await _call_over_http()

    spans = trace_exporter.get_finished_spans()
    server = _span(spans, "tools/call greet")
    assert server.parent is not None
    assert server.parent.span_id in _span_ids(spans, "HTTP POST")
    assert server.links == ()


async def test_propagation_only_parents_user_spans_under_meta(
    trace_exporter: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(fastmcp.settings, "telemetry_mode", "propagation_only")
    with trace.get_tracer("test").start_as_current_span("caller") as caller:
        await _call_over_http()

    user_work = _span(trace_exporter.get_finished_spans(), "user-work")
    assert user_work.context is not None
    assert user_work.context.trace_id == caller.get_span_context().trace_id


async def test_no_link_in_memory(trace_exporter: InMemorySpanExporter):
    async with Client(_server()) as client:
        await client.call_tool("greet", {"name": "World"})

    assert _span(trace_exporter.get_finished_spans(), "tools/call greet").links == ()


def _resolve(meta: dict[str, Any]):
    ctx = FastMCPRequestContext(
        session=MagicMock(),
        request_id="0",
        meta=meta,
        request=MagicMock(),
        protocol_version="2025-06-18",
        close_sse_stream=None,
        lifespan_context=MagicMock(),
        _srctx=MagicMock(meta=None),
    )
    token = fastmcp_request_ctx.set(ctx)
    try:
        return _get_parent_trace_context()
    finally:
        fastmcp_request_ctx.reset(token)


def test_meta_wins_when_both_are_visible(trace_exporter: InMemorySpanExporter):
    """Holds however the transport carries the ambient span into the handler."""
    with trace.get_tracer("test").start_as_current_span("ambient") as ambient:
        parent_context, links = _resolve({"traceparent": TRACEPARENT})

    assert parent_context is not None
    parent = trace.get_current_span(parent_context).get_span_context()
    assert format(parent.span_id, "016x") == "00f067aa0ba902b7"
    assert [link.context for link in links] == [ambient.get_span_context()]


def test_malformed_meta_is_ignored(trace_exporter: InMemorySpanExporter):
    with trace.get_tracer("test").start_as_current_span("ambient"):
        assert _resolve({"traceparent": "not-a-traceparent"}) == (None, [])
