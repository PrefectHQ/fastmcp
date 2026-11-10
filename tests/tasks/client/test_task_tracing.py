"""Client OpenTelemetry tracing for the task management wire calls.

Task submission and the `tasks/get`/`update`/`cancel` polling requests each get
a FastMCP client span and propagate trace context, so a tasked call is traced
end to end the same way a synchronous one is — its server spans nest under the
client spans rather than starting fresh trace roots.
"""

from __future__ import annotations

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from fastmcp import Client, FastMCP
from fastmcp_tasks import TasksExtension


async def test_tasked_call_creates_client_spans(trace_exporter: InMemorySpanExporter):
    mcp = FastMCP("traced-tasks")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def double(n: int) -> int:
        return n * 2

    async with Client(mcp, mode="auto") as client:
        result = await client.call_tool("double", {"n": 21})
        assert result.data == 42

    names = [s.name for s in trace_exporter.get_finished_spans()]
    # The tasked submission and at least one poll each produced a client span.
    assert "tools/call double" in names
    assert "tasks/get" in names
