"""Where a background task's worker span lands relative to the submitting trace."""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp_tasks import TasksExtension, call_tool_task

tracer = trace.get_tracer(__name__)


async def _run_echo_task(extension: TasksExtension) -> None:
    mcp = FastMCP("task-tracing")
    mcp.add_extension(extension)

    @mcp.tool(task=True)
    async def echo(message: str) -> str:
        return message

    async with Client(mcp, mode="auto") as client:
        with tracer.start_as_current_span("request"):
            task = await call_tool_task(client, "echo", {"message": "hi"})
            assert (await task.result()).data == "hi"


def _spans(exporter: InMemorySpanExporter) -> tuple[ReadableSpan, ReadableSpan]:
    spans = exporter.get_finished_spans()
    (request,) = [s for s in spans if s.name == "request"]
    (worker,) = [s for s in spans if s.kind == trace.SpanKind.CONSUMER]
    return request, worker


async def test_worker_span_starts_a_linked_trace_by_default(
    trace_exporter: InMemorySpanExporter,
):
    await _run_echo_task(TasksExtension())

    request, worker = _spans(trace_exporter)
    assert request.context and worker.context
    assert worker.parent is None
    assert worker.context.trace_id != request.context.trace_id
    assert [link.context.trace_id for link in worker.links] == [
        request.context.trace_id
    ]


async def test_parent_task_spans_puts_the_worker_span_in_the_request_trace(
    trace_exporter: InMemorySpanExporter,
):
    await _run_echo_task(TasksExtension(parent_task_spans=True))

    request, worker = _spans(trace_exporter)
    assert request.context and worker.context and worker.parent
    assert worker.context.trace_id == request.context.trace_id
    assert worker.parent.is_remote


@pytest.mark.parametrize(("env", "expected"), [("true", True), (None, False)])
def test_parent_task_spans_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch, env: str | None, expected: bool
):
    if env is None:
        monkeypatch.delenv("FASTMCP_DOCKET_PARENT_TASK_SPANS", raising=False)
    else:
        monkeypatch.setenv("FASTMCP_DOCKET_PARENT_TASK_SPANS", env)

    assert TasksExtension().docket_settings.parent_task_spans is expected
