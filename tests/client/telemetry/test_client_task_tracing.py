"""Tests for client OpenTelemetry tracing on task operations."""

import asyncio

from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind

from fastmcp import Client, FastMCP


def assert_propagating_client_span(
    trace_exporter: InMemorySpanExporter,
    method: str,
    component_key: str,
) -> None:
    all_spans = trace_exporter.get_finished_spans()
    spans = [span for span in all_spans if span.name == method]
    client_span = next(
        span
        for span in spans
        if span.attributes is not None and "fastmcp.server.name" not in span.attributes
    )
    server_span = next(
        span
        for span in spans
        if span.attributes is not None and "fastmcp.server.name" in span.attributes
    )

    assert client_span.kind == SpanKind.CLIENT
    assert client_span.attributes is not None
    assert client_span.attributes["mcp.method.name"] == method
    assert client_span.attributes["fastmcp.component.key"] == component_key
    assert server_span.parent is not None
    assert server_span.context.trace_id == client_span.context.trace_id

    spans_by_id = {span.context.span_id: span for span in all_spans}
    current = server_span
    while current.parent is not None:
        parent = spans_by_id.get(current.parent.span_id)
        assert parent is not None
        if parent.context.span_id == client_span.context.span_id:
            break
        current = parent
    else:
        raise AssertionError("Server span should descend from the client span")


async def test_list_tasks_creates_propagating_client_span(
    trace_exporter: InMemorySpanExporter,
):
    server = FastMCP("test-server")

    async with Client(server) as client:
        await client.list_tasks()

    assert_propagating_client_span(trace_exporter, "tasks/list", "")


async def test_task_id_operations_create_propagating_client_spans(
    trace_exporter: InMemorySpanExporter,
):
    started = asyncio.Event()
    server = FastMCP("test-server")

    @server.tool(task=True)
    async def quick_tool() -> str:
        return "done"

    @server.tool(task=True)
    async def slow_tool() -> str:
        started.set()
        await asyncio.sleep(10)
        return "done"

    async with Client(server) as client:
        completed_task = await client.call_tool("quick_tool", task=True)
        await completed_task.wait(timeout=2)
        trace_exporter.clear()

        await client.get_task_status(completed_task.task_id)
        await client.get_task_result(completed_task.task_id)

        running_task = await client.call_tool("slow_tool", task=True)
        await asyncio.wait_for(started.wait(), timeout=2)
        await client.cancel_task(running_task.task_id)

    assert_propagating_client_span(trace_exporter, "tasks/get", completed_task.task_id)
    assert_propagating_client_span(
        trace_exporter, "tasks/result", completed_task.task_id
    )
    assert_propagating_client_span(trace_exporter, "tasks/cancel", running_task.task_id)
