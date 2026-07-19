"""Tests for the in-process ASGI bridge and `asgi_server`."""

from typing import Literal

import httpx2
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from fastmcp import Context, FastMCP
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair
from fastmcp.utilities.asgi_transport import StreamingASGITransport, run_asgi_lifespan
from fastmcp.utilities.tests import ASGIServer, asgi_server


def build_server() -> FastMCP:
    server = FastMCP("BridgeTestServer")

    @server.tool
    def greet(name: str) -> str:
        return f"Hello, {name}!"

    @server.tool
    async def elicit_name(ctx: Context) -> str:
        """Round-trips a server-initiated request while the response is still open."""
        result = await ctx.elicit("What is your name?", response_type=str)
        if result.action == "accept":
            return f"You said {result.data}"
        return "declined"

    return server


class TestStreamingASGITransport:
    async def test_forwards_chunks_as_the_app_produces_them(self):
        """The bridge must stream, not buffer: chunks arrive before the app finishes."""

        async def stream(request: Request) -> StreamingResponse:
            async def body():
                yield b"first"
                yield b"second"

            return StreamingResponse(body(), media_type="text/plain")

        app = Starlette(routes=[Route("/stream", stream)])
        async with httpx2.AsyncClient(
            transport=StreamingASGITransport(app), base_url="http://testserver"
        ) as client:
            chunks: list[bytes] = []
            async with client.stream("GET", "/stream") as response:
                async for chunk in response.aiter_bytes():
                    chunks.append(chunk)

        assert b"".join(chunks) == b"firstsecond"

    async def test_request_body_and_headers_reach_the_app(self):
        async def echo(request: Request) -> Response:
            body = await request.body()
            return Response(
                content=body,
                headers={"x-seen-header": request.headers.get("x-demo", "missing")},
            )

        app = Starlette(routes=[Route("/echo", echo, methods=["POST"])])
        async with httpx2.AsyncClient(
            transport=StreamingASGITransport(app), base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/echo", content=b"payload", headers={"x-demo": "abc"}
            )

        assert response.content == b"payload"
        assert response.headers["x-seen-header"] == "abc"

    async def test_query_string_reaches_the_app(self):
        async def show(request: Request) -> Response:
            return Response(content=request.query_params["q"])

        app = Starlette(routes=[Route("/search", show)])
        async with httpx2.AsyncClient(
            transport=StreamingASGITransport(app), base_url="http://testserver"
        ) as client:
            response = await client.get("/search", params={"q": "hello"})

        assert response.text == "hello"

    async def test_error_before_response_start_propagates_to_caller(self):
        async def boom(scope: Scope, receive: Receive, send: Send) -> None:
            raise ValueError("app exploded")

        async with httpx2.AsyncClient(
            transport=StreamingASGITransport(boom), base_url="http://testserver"
        ) as client:
            with pytest.raises(ValueError, match="app exploded"):
                await client.get("/anything")

    async def test_error_after_response_start_truncates_the_body(self):
        """Post-start failures look like a dropped socket, not a raised exception."""

        async def boom(scope: Scope, receive: Receive, send: Send) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            # Raise with no checkpoint in between, so the error is guaranteed to be
            # recorded before the transport's waiter resumes — the scheduling order
            # that used to surface the failure as a raised exception.
            raise ValueError("app exploded mid-response")

        async with httpx2.AsyncClient(
            transport=StreamingASGITransport(boom), base_url="http://testserver"
        ) as client:
            response = await client.get("/anything")

        assert response.status_code == 200
        assert response.content == b""


class TestRunAsgiLifespan:
    async def test_startup_and_shutdown_run_once(self):
        events: list[str] = []

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            assert scope["type"] == "lifespan"
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    events.append("startup")
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    events.append("shutdown")
                    await send({"type": "lifespan.shutdown.complete"})
                    return

        async with run_asgi_lifespan(app):
            assert events == ["startup"]

        assert events == ["startup", "shutdown"]

    async def test_startup_failure_raises(self):
        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            await receive()
            await send({"type": "lifespan.startup.failed", "message": "nope"})

        with pytest.raises(RuntimeError, match="startup failed"):
            async with run_asgi_lifespan(app):
                pass

    async def test_shutdown_failure_raises(self):
        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            await receive()
            await send({"type": "lifespan.startup.complete"})
            await receive()
            await send({"type": "lifespan.shutdown.failed", "message": "teardown nope"})

        with pytest.raises(RuntimeError, match="shutdown failed"):
            async with run_asgi_lifespan(app):
                pass

    async def test_shutdown_failure_does_not_mask_body_error(self):
        """A broken teardown must never hide the failure the caller actually cares about."""

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            await receive()
            await send({"type": "lifespan.startup.complete"})
            await receive()
            await send({"type": "lifespan.shutdown.failed", "message": "teardown nope"})

        with pytest.raises(ValueError, match="body exploded"):
            async with run_asgi_lifespan(app):
                raise ValueError("body exploded")


class TestRunServerInMemory:
    @pytest.mark.parametrize("transport", ["http", "streamable-http", "sse"])
    async def test_client_round_trip(
        self, transport: Literal["http", "streamable-http", "sse"]
    ):
        async with asgi_server(build_server(), transport=transport) as server:
            async with server.client() as client:
                result = await client.call_tool("greet", {"name": "World"})

        assert result.data == "Hello, World!"

    async def test_default_path_matches_transport(self):
        async with asgi_server(build_server(), transport="sse") as server:
            assert server.url.endswith("/sse")
        async with asgi_server(build_server(), transport="http") as server:
            assert server.url.endswith("/mcp")

    async def test_custom_path_is_used(self):
        async with asgi_server(build_server(), path="/custom") as server:
            assert server.url.endswith("/custom")
            async with server.client() as client:
                assert await client.ping() is True

    async def test_server_initiated_request_mid_stream(self):
        """Elicitation needs a server->client request while the POST is still open.

        This is the capability a buffering ASGI transport cannot provide, and the
        reason the bridge streams responses.
        """

        async def elicitation_handler(message, response_type, params, ctx):
            return {"value": "Alice"}

        async with asgi_server(build_server()) as server:
            async with server.client(elicitation_handler=elicitation_handler) as client:
                result = await client.call_tool("elicit_name", {})

        assert result.data == "You said Alice"

    async def test_http_client_reaches_the_app(self):
        async with asgi_server(build_server()) as server:
            async with server.http_client() as http:
                # A GET on the streamable HTTP endpoint without a session is rejected;
                # the point is that the request reaches the real app at all.
                response = await http.get(server.url)

        assert response.status_code in {400, 405}

    async def test_auth_middleware_runs(self):
        """A migrated test must not pass by bypassing the real middleware stack."""
        key_pair = RSAKeyPair.generate()
        server = build_server()
        server.auth = JWTVerifier(
            public_key=key_pair.public_key,
            issuer="https://issuer.example.com",
            audience="test-audience",
        )

        async with asgi_server(server) as running_server:
            async with running_server.http_client() as http:
                unauthenticated = await http.post(
                    running_server.url,
                    json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                )

        assert unauthenticated.status_code == 401

    async def test_yields_in_memory_server(self):
        async with asgi_server(build_server()) as server:
            assert isinstance(server, ASGIServer)
            assert server.transport_type == "http"
