import json

import pytest
from docket import Docket
from fastmcp_tasks.context import _recall_snapshot, get_task_context
from mcp_types import TextContent, TextResourceContents
from starlette.requests import Request

from fastmcp.server.dependencies import get_http_request
from fastmcp.server.http import _current_http_request
from fastmcp.server.server import FastMCP
from fastmcp.utilities.tests import ASGIServer, asgi_server
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import running_task_server, submit_task, wait_for_task


@pytest.fixture
def reset_docket_memory_server():
    """Force a fresh memory:// Docket server bound to this test's event loop."""
    if hasattr(Docket, "_memory_server"):
        delattr(Docket, "_memory_server")
    yield
    if hasattr(Docket, "_memory_server"):
        delattr(Docket, "_memory_server")


def _http_request_with_headers(headers: dict[str, str]) -> Request:
    """Build a minimal Starlette HTTP request carrying the given headers."""
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": raw_headers,
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 12345),
    }
    return Request(scope)


def fastmcp_server():
    server = FastMCP()

    # Add a tool
    @server.tool
    def get_headers_tool() -> dict[str, str]:
        """Get the HTTP headers from the request."""
        request = get_http_request()

        return dict(request.headers)

    @server.resource(uri="request://headers")
    async def get_headers_resource() -> str:
        import json

        request = get_http_request()
        return json.dumps(dict(request.headers))

    # Add a prompt
    @server.prompt
    def get_headers_prompt() -> str:
        """Get the HTTP headers from the request."""
        request = get_http_request()

        return json.dumps(dict(request.headers))

    return server


@pytest.fixture
async def shttp_server():
    """Start a test server with StreamableHttp transport."""
    server = fastmcp_server()
    async with asgi_server(server, transport="http") as running_server:
        yield running_server


@pytest.fixture
async def sse_server():
    """Start a test server with SSE transport."""
    server = fastmcp_server()
    async with asgi_server(server, transport="sse") as running_server:
        yield running_server


async def test_http_headers_resource_shttp(shttp_server: ASGIServer):
    """Test getting HTTP headers from the server."""
    async with shttp_server.client(headers={"X-DEMO-HEADER": "ABC"}) as client:
        raw_result = await client.read_resource("request://headers")
        assert isinstance(raw_result[0], TextResourceContents)
        json_result = json.loads(raw_result[0].text)
        assert "x-demo-header" in json_result
        assert json_result["x-demo-header"] == "ABC"


async def test_http_headers_resource_sse(sse_server: ASGIServer):
    """Test getting HTTP headers from the server."""
    async with sse_server.client(headers={"X-DEMO-HEADER": "ABC"}) as client:
        raw_result = await client.read_resource("request://headers")
        assert isinstance(raw_result[0], TextResourceContents)
        json_result = json.loads(raw_result[0].text)
        assert "x-demo-header" in json_result
        assert json_result["x-demo-header"] == "ABC"


async def test_http_headers_tool_shttp(shttp_server: ASGIServer):
    """Test getting HTTP headers from the server."""
    async with shttp_server.client(headers={"X-DEMO-HEADER": "ABC"}) as client:
        result = await client.call_tool("get_headers_tool")
        assert "x-demo-header" in result.data
        assert result.data["x-demo-header"] == "ABC"


async def test_http_headers_tool_sse(sse_server: ASGIServer):
    async with sse_server.client(headers={"X-DEMO-HEADER": "ABC"}) as client:
        result = await client.call_tool("get_headers_tool")
        assert "x-demo-header" in result.data
        assert result.data["x-demo-header"] == "ABC"


async def test_http_headers_prompt_shttp(shttp_server: ASGIServer):
    """Test getting HTTP headers from the server."""
    async with shttp_server.client(headers={"X-DEMO-HEADER": "ABC"}) as client:
        result = await client.get_prompt("get_headers_prompt")
        assert isinstance(result.messages[0].content, TextContent)
        json_result = json.loads(result.messages[0].content.text)
        assert "x-demo-header" in json_result
        assert json_result["x-demo-header"] == "ABC"


async def test_http_headers_prompt_sse(sse_server: ASGIServer):
    """Test getting HTTP headers from the server."""
    async with sse_server.client(headers={"X-DEMO-HEADER": "ABC"}) as client:
        result = await client.get_prompt("get_headers_prompt")
        assert isinstance(result.messages[0].content, TextContent)
        json_result = json.loads(result.messages[0].content.text)
        assert "x-demo-header" in json_result
        assert json_result["x-demo-header"] == "ABC"


async def test_get_http_headers_excludes_content_type(sse_server: ASGIServer):
    """Test that get_http_headers() excludes content-type header (issue #3097).

    This prevents HTTP 415 errors when forwarding headers to downstream APIs
    that require specific Content-Type headers (e.g., application/vnd.api+json).
    """
    from fastmcp.server.dependencies import get_http_headers

    server = FastMCP()

    @server.tool
    def check_excluded_headers() -> dict[str, str]:
        """Check that problematic headers are excluded from get_http_headers()."""
        return get_http_headers()

    async with asgi_server(server, transport="sse") as running_server:
        async with running_server.client(
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Custom-Header": "should-be-included",
            }
        ) as client:
            result = await client.call_tool("check_excluded_headers")
            headers = result.data

            # These headers should be excluded
            assert "content-type" not in headers
            assert "accept" not in headers
            assert "host" not in headers
            assert "content-length" not in headers

            # Custom headers should be included
            assert "x-custom-header" in headers
            assert headers["x-custom-header"] == "should-be-included"


def _worker_snapshot_headers() -> dict[str, str]:
    """Read the HTTP headers snapshotted at task submission from inside a worker."""
    task_info = get_task_context()
    snapshot = _recall_snapshot(task_info.task_id) if task_info is not None else None
    if snapshot is None or snapshot.http_headers is None:
        return {}
    return dict(snapshot.http_headers)


async def test_background_task_can_read_snapshotted_request_headers(
    reset_docket_memory_server,
):
    """A background task worker reads the HTTP headers snapshotted at submission.

    There is no client task-submission API yet (Phase 4), so the task is driven
    in-process: an HTTP request is bound while the task is submitted, and the
    worker reads the request headers back from the restored task-context
    snapshot.
    """
    server = FastMCP()
    server.add_extension(TasksExtension())

    @server.tool(task=True)
    async def check_request_header() -> str:
        return _worker_snapshot_headers().get("x-tenant-id", "missing")

    request = _http_request_with_headers({"X-Tenant-ID": "tenant-123"})
    async with running_task_server(server):
        token = _current_http_request.set(request)
        try:
            created = await submit_task(server, "check_request_header", {})
        finally:
            _current_http_request.reset(token)

        final = await wait_for_task(server, created.task_id)

    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {"result": "tenant-123"}


async def test_background_task_snapshot_preserves_all_request_headers(
    reset_docket_memory_server,
):
    """The task snapshot preserves every request header, including authorization."""
    server = FastMCP()
    server.add_extension(TasksExtension())

    @server.tool(task=True)
    async def check_headers() -> dict[str, str]:
        headers = _worker_snapshot_headers()
        return {
            "authorization": headers.get("authorization", "missing"),
            "tenant": headers.get("x-tenant-id", "missing"),
        }

    request = _http_request_with_headers(
        {
            "Authorization": "Bearer tenant-token",
            "X-Tenant-ID": "tenant-456",
        }
    )
    async with running_task_server(server):
        token = _current_http_request.set(request)
        try:
            created = await submit_task(server, "check_headers", {})
        finally:
            _current_http_request.reset(token)

        final = await wait_for_task(server, created.task_id)

    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {
        "authorization": "Bearer tenant-token",
        "tenant": "tenant-456",
    }
