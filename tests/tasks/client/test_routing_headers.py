"""Task-management requests mirror their task ID into the HTTP routing header."""

from typing import Any

import httpx2
import pytest
from fastmcp_tasks.client import _send_cancel, _send_get, _send_update
from mcp.shared.exceptions import MCPError
from mcp.shared.inbound import decode_header_value

from fastmcp import Client, FastMCP
from fastmcp.utilities.tests import asgi_server
from fastmcp_tasks import TasksExtension


@pytest.mark.parametrize("method", ["tasks/get", "tasks/update", "tasks/cancel"])
@pytest.mark.parametrize("task_id", ["task-123", " task-中文 "])
async def test_task_management_routing_headers(method: str, task_id: str):
    server = FastMCP("task-routing-headers")
    server.add_extension(TasksExtension())
    requests: list[httpx2.Request] = []

    async def capture(request: httpx2.Request) -> None:
        if request.headers.get("Mcp-Method") == method:
            requests.append(request)

    async with asgi_server(server) as running_server:

        def http_client(**kwargs: Any) -> httpx2.AsyncClient:
            return running_server.http_client(
                **kwargs, event_hooks={"request": [capture]}
            )

        transport = running_server.transport(httpx_client_factory=http_client)
        async with Client(transport, mode="auto") as client:
            # Unknown IDs exercise the real HTTP path without starting a worker job.
            with pytest.raises(MCPError, match="not found"):
                if method == "tasks/get":
                    await _send_get(client.session, task_id)
                elif method == "tasks/update":
                    await _send_update(client.session, task_id, {})
                else:
                    await _send_cancel(client.session, task_id)

    assert len(requests) == 1
    header = requests[0].headers.get("Mcp-Name")
    assert header is not None
    assert decode_header_value(header) == task_id
