"""An in-process, full-duplex HTTP transport for driving ASGI applications from httpx.

Ported from the MCP Python SDK's test suite (`tests/interaction/transports/_bridge.py`,
MIT licensed).

`httpx2.ASGITransport` runs the application to completion and only then hands the buffered
response to the caller, so a server that streams its response — as the streamable HTTP
transport's SSE responses do — can never converse with the client mid-request: a
server-initiated request nested inside a still-open call deadlocks.
`StreamingASGITransport` removes that limitation by running the application as a background
task and forwarding every `http.response.body` chunk to the client the moment it is sent.
Everything happens on the one event loop: no sockets, no threads, no sleeps.

The behavioural contract:

- The request body is buffered before the application is invoked (MCP requests are small
  JSON documents); the response streams chunk by chunk.
- Closing the response — or the whole client — delivers `http.disconnect` to the
  application, exactly as a real server sees when its peer goes away.
- An exception the application raises before sending `http.response.start` fails the
  originating request with that same exception. After the response has started, a failure
  is visible to the client only through the response itself (status code, truncated body) —
  the same signal a real server over a real socket would give.

The transport owns an anyio task group for the application tasks; it is opened and closed by
`httpx2.AsyncClient`'s own context manager, so the client must be used as a context manager.
Closing the transport cancels every running application task by default; set
`cancel_on_close=False` to wait for the application's own disconnect handling instead, which
is what the legacy SSE transport relies on for resource cleanup.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import TracebackType

import anyio
import anyio.abc
import httpx2
from anyio.streams.memory import MemoryObjectReceiveStream
from starlette.types import ASGIApp, Message, Scope


class _StreamingResponseBody(httpx2.AsyncByteStream):
    """A response body that yields chunks as the application produces them.

    Closing it tells the application the client has gone away (`http.disconnect`),
    mirroring a peer that drops the connection mid-response.
    """

    def __init__(
        self,
        chunks: MemoryObjectReceiveStream[bytes],
        client_disconnected: anyio.Event,
    ) -> None:
        self._chunks = chunks
        self._client_disconnected = client_disconnected

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self._client_disconnected.set()
        await self._chunks.aclose()


class StreamingASGITransport(httpx2.AsyncBaseTransport):
    """Drive an ASGI application in-process, streaming each response as it is produced.

    This is an `httpx2` transport, so it plugs into anything that accepts an
    `httpx2.AsyncClient` — including FastMCP's client transports via their
    `httpx_client_factory` argument.

    Args:
        app: The ASGI application to drive (e.g. `FastMCP.http_app()`).
        cancel_on_close: When True (the default), closing the transport cancels every
            application task still running, so harness teardown can never hang. Set to
            False to wait for the application's own disconnect handling to complete
            instead, which the legacy SSE server transport relies on for cleanup.

    Example:
        Drive a FastMCP server's real HTTP app with no sockets:
        ```python
        import httpx2
        from fastmcp import FastMCP
        from fastmcp.utilities.asgi_transport import StreamingASGITransport

        mcp = FastMCP("test")
        app = mcp.http_app(transport="http")

        async with app.router.lifespan_context(app):
            transport = StreamingASGITransport(app)
            async with httpx2.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                response = await client.get("/mcp")
        ```
    """

    _task_group: anyio.abc.TaskGroup

    def __init__(self, app: ASGIApp, *, cancel_on_close: bool = True) -> None:
        self._app = app
        self._cancel_on_close = cancel_on_close

    async def __aenter__(self) -> StreamingASGITransport:
        self._task_group = anyio.create_task_group()
        await self._task_group.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        # httpx closes every streamed response before closing the transport, so by now each
        # application task has been delivered `http.disconnect`. Either cancel immediately,
        # or wait for the application's own disconnect handling to unwind.
        if self._cancel_on_close:
            self._task_group.cancel_scope.cancel()
        await self._task_group.__aexit__(exc_type, exc_value, traceback)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if not isinstance(request.stream, httpx2.AsyncByteStream):
            raise TypeError(
                "StreamingASGITransport requires an async request stream; "
                f"got {type(request.stream).__name__}."
            )
        request_body = b"".join([chunk async for chunk in request.stream])

        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": request.method,
            "scheme": request.url.scheme,
            "path": request.url.path,
            "raw_path": request.url.raw_path.split(b"?", maxsplit=1)[0],
            "query_string": request.url.query,
            "root_path": "",
            "headers": [(name.lower(), value) for name, value in request.headers.raw],
            "server": (request.url.host, request.url.port),
            "client": ("127.0.0.1", 1234),
        }

        request_delivered = False
        start_received = False
        client_disconnected = anyio.Event()
        response_started = anyio.Event()
        response_status = 0
        response_headers: list[tuple[bytes, bytes]] = []
        application_error: Exception | None = None
        chunk_writer, chunk_reader = anyio.create_memory_object_stream[bytes](math.inf)

        async def receive_request() -> Message:
            nonlocal request_delivered
            if not request_delivered:
                request_delivered = True
                return {
                    "type": "http.request",
                    "body": request_body,
                    "more_body": False,
                }
            await client_disconnected.wait()
            return {"type": "http.disconnect"}

        async def send_response(message: Message) -> None:
            nonlocal response_status, response_headers, start_received
            if message["type"] == "http.response.start":
                start_received = True
                response_status = message["status"]
                response_headers = list(message.get("headers", []))
                response_started.set()
                return
            if message["type"] != "http.response.body":
                raise RuntimeError(f"Unexpected ASGI message type: {message['type']}")
            body: bytes = message.get("body", b"")
            if body:
                await chunk_writer.send(body)
            if not message.get("more_body", False):
                await chunk_writer.aclose()

        async def run_application() -> None:
            nonlocal application_error
            try:
                await self._app(scope, receive_request, send_response)
            except Exception as exc:
                # The bridge is the application's outermost boundary: a crash must fail the
                # originating request (or show up in the already-started response), never
                # tear down the task group shared with every other in-flight request.
                application_error = exc
            finally:
                response_started.set()
                await chunk_writer.aclose()

        self._task_group.start_soon(run_application)
        try:
            await response_started.wait()
            # Only a failure *before* the start message can fail the request. Once the
            # response has started the client sees the failure as a truncated body, which
            # is the same signal a real server over a real socket would give.
            if application_error is not None and not start_received:
                raise application_error
        except BaseException:
            # No response will be built, so close the reader the response body would have
            # owned and tell the application its peer has gone away.
            client_disconnected.set()
            await chunk_reader.aclose()
            raise
        return httpx2.Response(
            status_code=response_status,
            headers=response_headers,
            stream=_StreamingResponseBody(chunk_reader, client_disconnected),
            request=request,
        )


@asynccontextmanager
async def run_asgi_lifespan(app: ASGIApp) -> AsyncIterator[None]:
    """Run an ASGI application's lifespan, driving the protocol as a real server does.

    The application's lifespan runs inside a dedicated task for the whole duration of
    the context. This matters because a lifespan typically owns cancel scopes and task
    groups — anyio requires those to be exited by the task that entered them, which
    rules out entering the lifespan on one task and leaving it on another (as a pytest
    fixture's setup and teardown phases may do).

    Args:
        app: The ASGI application whose lifespan should run.

    Raises:
        RuntimeError: If the application reports `lifespan.startup.failed`, or reports
            `lifespan.shutdown.failed` (or crashes during shutdown) while the context
            body itself completed successfully. A failure inside the body takes
            precedence and propagates unchanged.
    """
    receive_queue: asyncio.Queue[Message] = asyncio.Queue()
    startup_complete: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    shutdown_complete: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def receive() -> Message:
        return await receive_queue.get()

    async def send(message: Message) -> None:
        if message["type"] == "lifespan.startup.complete":
            if not startup_complete.done():
                startup_complete.set_result(None)
        elif message["type"] == "lifespan.startup.failed":
            if not startup_complete.done():
                startup_complete.set_exception(
                    RuntimeError(
                        f"ASGI application startup failed: {message.get('message', '')}"
                    )
                )
        elif message["type"] == "lifespan.shutdown.complete":
            if not shutdown_complete.done():
                shutdown_complete.set_result(None)
        elif message["type"] == "lifespan.shutdown.failed":
            if not shutdown_complete.done():
                shutdown_complete.set_exception(
                    RuntimeError(
                        "ASGI application shutdown failed: "
                        f"{message.get('message', '')}"
                    )
                )

    async def run_lifespan() -> None:
        scope: Scope = {"type": "lifespan", "asgi": {"version": "3.0"}}
        try:
            await app(scope, receive, send)
        except BaseException as exc:
            # The app died without completing the handshake; surface that to whichever
            # side is still waiting rather than hanging.
            if not startup_complete.done():
                startup_complete.set_exception(exc)
            if not shutdown_complete.done():
                shutdown_complete.set_exception(exc)
            raise
        else:
            if not startup_complete.done():
                startup_complete.set_exception(
                    RuntimeError("ASGI application exited before completing startup")
                )
            if not shutdown_complete.done():
                shutdown_complete.set_result(None)

    task = asyncio.create_task(run_lifespan())
    await receive_queue.put({"type": "lifespan.startup"})
    try:
        await startup_complete
    except BaseException:
        task.cancel()
        with anyio.CancelScope(shield=True):
            await asyncio.gather(task, return_exceptions=True)
        raise

    body_failed = False
    try:
        yield
    except BaseException:
        body_failed = True
        raise
    finally:
        await receive_queue.put({"type": "lifespan.shutdown"})
        with anyio.CancelScope(shield=True):
            results = await asyncio.gather(
                shutdown_complete, task, return_exceptions=True
            )
        # A harness must surface a broken teardown rather than swallow it — but never at
        # the cost of masking the failure the body already raised, which is the one the
        # caller actually needs to see.
        if not body_failed:
            for result in results:
                if isinstance(result, BaseException) and not isinstance(
                    result, asyncio.CancelledError
                ):
                    raise result
