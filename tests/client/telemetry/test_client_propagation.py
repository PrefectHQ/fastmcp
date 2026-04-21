"""Tests for client trace-context propagation on initialize and list methods."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import mcp.types as mt
import pytest
from opentelemetry import baggage, trace
from opentelemetry import context as otel_context
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from fastmcp import Client, FastMCP


def build_test_server() -> FastMCP:
    """Create a server with one component of each type."""
    server = FastMCP("test-server")

    @server.tool()
    def greet(name: str = "world") -> str:
        return f"Hello, {name}!"

    @server.resource("data://config")
    def get_config() -> str:
        return "config"

    @server.resource("users://{user_id}/profile")
    def get_profile(user_id: str) -> str:
        return f"profile {user_id}"

    @server.prompt()
    def greeting() -> str:
        return "Hello!"

    return server


def get_meta_dict(request: Any) -> dict[str, Any] | None:
    """Normalize ``request.params.meta`` to a plain dict for assertions."""
    params = getattr(request, "params", None)
    meta = getattr(params, "meta", None)
    if meta is None:
        return None
    return meta.model_dump(exclude_none=True)


class TestClientPropagation:
    async def test_initialize_creates_span_and_propagates_meta(
        self,
        monkeypatch,
        trace_exporter: InMemorySpanExporter,
    ):
        server = build_test_server()
        client = Client(server, auto_initialize=False)
        tracer = trace.get_tracer("external")

        async with client:
            captured_requests: list[Any] = []
            original_send_request = client.session.send_request

            async def wrapped_send_request(request: Any, result_type: Any) -> Any:
                captured_requests.append(request.root)
                return await original_send_request(request, result_type)

            monkeypatch.setattr(client.session, "send_request", wrapped_send_request)

            baggage_token = otel_context.attach(baggage.set_baggage("tenant", "acme"))
            try:
                with tracer.start_as_current_span("external-parent"):
                    await client.initialize()
            finally:
                otel_context.detach(baggage_token)

        spans = trace_exporter.get_finished_spans()
        initialize_span = next(
            (
                span
                for span in spans
                if span.name == "initialize"
                and span.attributes is not None
                and "fastmcp.server.name" not in span.attributes
            ),
            None,
        )
        assert initialize_span is not None
        assert initialize_span.attributes is not None
        assert initialize_span.attributes["mcp.method.name"] == "initialize"

        initialize_request = next(
            request
            for request in captured_requests
            if isinstance(request, mt.InitializeRequest)
        )
        captured_meta = get_meta_dict(initialize_request)
        assert captured_meta is not None
        assert captured_meta["traceparent"].split("-")[2] == format(
            initialize_span.get_span_context().span_id,
            "016x",
        )
        assert "tenant=acme" in str(captured_meta["baggage"])

    @pytest.mark.parametrize(
        ("request_type", "operation", "expected_span_name"),
        [
            (mt.ListToolsRequest, lambda client: client.list_tools(), "tools/list"),
            (
                mt.ListResourcesRequest,
                lambda client: client.list_resources(),
                "resources/list",
            ),
            (
                mt.ListResourceTemplatesRequest,
                lambda client: client.list_resource_templates(),
                "resources/templates/list",
            ),
            (
                mt.ListPromptsRequest,
                lambda client: client.list_prompts(),
                "prompts/list",
            ),
        ],
    )
    async def test_list_methods_propagate_meta(
        self,
        request_type: type[Any],
        operation: Callable[[Client], Awaitable[Any]],
        expected_span_name: str,
        monkeypatch,
        trace_exporter: InMemorySpanExporter,
    ):
        server = build_test_server()
        client = Client(server, auto_initialize=False)
        tracer = trace.get_tracer("external")

        async with client:
            await client.initialize()
            captured_requests: list[Any] = []
            original_send_request = client.session.send_request

            async def wrapped_send_request(request: Any, result_type: Any) -> Any:
                captured_requests.append(request.root)
                return await original_send_request(request, result_type)

            monkeypatch.setattr(client.session, "send_request", wrapped_send_request)
            trace_exporter.clear()

            baggage_token = otel_context.attach(baggage.set_baggage("tenant", "acme"))
            try:
                with tracer.start_as_current_span("external-parent"):
                    await operation(client)
            finally:
                otel_context.detach(baggage_token)

        spans = trace_exporter.get_finished_spans()
        client_span = next(
            (
                span
                for span in spans
                if span.name == expected_span_name
                and span.attributes is not None
                and "fastmcp.server.name" not in span.attributes
            ),
            None,
        )
        assert client_span is not None

        request = next(
            captured_request
            for captured_request in captured_requests
            if isinstance(captured_request, request_type)
        )
        captured_meta = get_meta_dict(request)
        assert captured_meta is not None
        assert captured_meta["traceparent"].split("-")[2] == format(
            client_span.get_span_context().span_id,
            "016x",
        )
        assert "tenant=acme" in str(captured_meta["baggage"])
