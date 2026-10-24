"""Failures produce one exception event per span, including reused server spans."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Literal

import pytest
from mcp import MCPError
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Span, SpanKind, StatusCode

from fastmcp import Client, FastMCP
from fastmcp.client.telemetry import client_span
from fastmcp.exceptions import PromptError, ResourceError, ToolError
from fastmcp.server.telemetry import delegate_span, seam_span, server_span


@pytest.mark.parametrize(
    "span_factory",
    [
        pytest.param(lambda: client_span("test", "tools/call", "fail"), id="client"),
        pytest.param(
            lambda: server_span("test", "tools/call", "server", "tool", "fail"),
            id="server",
        ),
        pytest.param(lambda: seam_span("tools/call", "server"), id="seam"),
        pytest.param(lambda: delegate_span("fail", "provider", "fail"), id="delegate"),
    ],
)
def test_span_records_exception_once(
    trace_exporter: InMemorySpanExporter,
    span_factory: Callable[[], AbstractContextManager[Span]],
) -> None:
    error = ValueError("one failure")
    with pytest.raises(ValueError) as caught, span_factory():
        raise error

    assert caught.value is error
    (span,) = trace_exporter.get_finished_spans()
    (event,) = span.events
    assert span.status.status_code == StatusCode.ERROR
    assert span.status.description == "one failure"
    assert span.attributes is not None
    assert span.attributes["error.type"] == "ValueError"
    assert event.name == "exception"
    assert event.attributes is not None
    assert event.attributes["exception.type"] == "ValueError"
    assert event.attributes["exception.message"] == "one failure"
    stacktrace = event.attributes["exception.stacktrace"]
    assert isinstance(stacktrace, str)
    assert "raise error" in stacktrace


@pytest.mark.parametrize("operation", ["tool", "resource", "template", "prompt"])
@pytest.mark.parametrize("dispatch", ["direct", "client", "mounted"])
async def test_component_failure_records_exception_once_per_span(
    trace_exporter: InMemorySpanExporter,
    operation: Literal["tool", "resource", "template", "prompt"],
    dispatch: Literal["direct", "client", "mounted"],
) -> None:
    backend = FastMCP("backend")

    @backend.tool
    async def fail_tool() -> str:
        raise ValueError("one failure")

    @backend.resource("data://fail")
    async def fail_resource() -> str:
        raise ValueError("one failure")

    @backend.resource("data://items/{item_id}")
    async def fail_template(item_id: str) -> str:
        raise ValueError("one failure")

    @backend.prompt
    async def fail_prompt() -> str:
        raise ValueError("one failure")

    server = backend
    prefix = ""
    if dispatch == "mounted":
        server = FastMCP("parent")
        server.mount(backend, namespace="child")
        prefix = "child_"

    resource_uri = "data://child/fail" if prefix else "data://fail"
    template_uri = "data://child/items/1" if prefix else "data://items/1"
    if dispatch == "direct":
        if operation == "tool":
            with pytest.raises(ToolError, match="one failure"):
                await server.call_tool("fail_tool", {})
        elif operation in ("resource", "template"):
            uri = resource_uri if operation == "resource" else template_uri
            with pytest.raises(ResourceError, match="one failure"):
                await server.read_resource(uri)
        else:
            with pytest.raises(PromptError, match="one failure"):
                await server.render_prompt("fail_prompt", {})
    else:
        async with Client(server) as client:
            if operation == "tool":
                with pytest.raises(ToolError, match="one failure"):
                    await client.call_tool(f"{prefix}fail_tool")
            elif operation in ("resource", "template"):
                uri = resource_uri if operation == "resource" else template_uri
                with pytest.raises(MCPError, match="one failure"):
                    await client.read_resource(uri)
            else:
                with pytest.raises(MCPError, match="one failure"):
                    await client.get_prompt(f"{prefix}fail_prompt")

    method = {
        "tool": "tools/call",
        "resource": "resources/read",
        "template": "resources/read",
        "prompt": "prompts/get",
    }[operation]
    spans = [
        span
        for span in trace_exporter.get_finished_spans()
        if span.instrumentation_scope is not None
        and span.instrumentation_scope.name == "fastmcp"
        and span.attributes is not None
        and span.attributes.get("mcp.method.name") == method
    ]
    assert len(spans) == {"direct": 1, "client": 2, "mounted": 4}[dispatch]
    for span in spans:
        assert span.status.status_code == StatusCode.ERROR
        assert span.status.description is not None
        assert "one failure" in span.status.description
        assert span.attributes is not None
        assert "error.type" in span.attributes
        if operation == "tool" and span.kind == SpanKind.CLIENT:
            # isError is a result, not an exception raised inside the CLIENT span.
            assert not span.events
        else:
            (event,) = span.events
            assert event.name == "exception"
            assert event.attributes is not None
            message = event.attributes["exception.message"]
            assert isinstance(message, str)
            assert "one failure" in message
