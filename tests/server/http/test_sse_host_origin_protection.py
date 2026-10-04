"""Host and Origin validation on the SSE transport's connection and message endpoints."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Literal

import httpx
import pytest
import uvicorn

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.transports import SSETransport
from fastmcp.server.http import HostOriginProtection, create_sse_app
from fastmcp.utilities.http import find_available_port
from fastmcp.utilities.tests import run_server_async, temporary_settings

UNTRUSTED_HOST = {"host": "untrusted.example"}
UNTRUSTED_ORIGIN = {"origin": "https://untrusted.example"}
PING_REQUEST = {"jsonrpc": "2.0", "id": 1, "method": "ping"}

GuardResult = Literal["host rejected", "origin rejected", "allowed"]


def create_server() -> FastMCP:
    server = FastMCP("SSEGuardServer")

    @server.tool
    def greet(name: str) -> str:
        return f"Hello, {name}!"

    return server


@asynccontextmanager
async def serve_http_app(
    transport: Literal["http", "sse"],
    **http_app_kwargs: Any,
) -> AsyncGenerator[str, None]:
    """Serve `http_app()` on a loopback port and yield the MCP endpoint URL."""
    path = "/sse" if transport == "sse" else "/mcp"
    app = create_server().http_app(transport=transport, path=path, **http_app_kwargs)
    port = find_available_port()
    uvicorn_server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="critical",
            timeout_graceful_shutdown=1,
            ws="websockets-sansio",
        )
    )
    server_task = asyncio.create_task(uvicorn_server.serve())
    for _ in range(200):
        if uvicorn_server.started:
            break
        await asyncio.sleep(0.01)
    assert uvicorn_server.started

    try:
        yield f"http://127.0.0.1:{port}{path}"
    finally:
        uvicorn_server.should_exit = True
        await server_task


def _guard_result(status_code: int) -> GuardResult:
    if status_code == 421:
        return "host rejected"
    if status_code == 403:
        return "origin rejected"
    return "allowed"


async def _get_status(url: str, headers: dict[str, str]) -> int:
    async with httpx.AsyncClient() as http:
        async with http.stream(
            "GET",
            url,
            headers={"accept": "text/event-stream", **headers},
        ) as response:
            return response.status_code


@asynccontextmanager
async def _sse_session(url: str) -> AsyncGenerator[str, None]:
    """Open a trusted SSE stream and yield the session's message endpoint URL."""
    async with httpx.AsyncClient() as http:
        async with http.stream(
            "GET",
            url,
            headers={"accept": "text/event-stream"},
        ) as response:
            assert response.status_code == 200
            message_path: str | None = None
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    message_path = line.removeprefix("data: ")
                    break
            assert message_path is not None

            yield str(httpx.URL(url).join(message_path))


async def _post_status(message_url: str, headers: dict[str, str]) -> int:
    async with httpx.AsyncClient() as http:
        response = await http.post(message_url, headers=headers, json=PING_REQUEST)
    return response.status_code


@pytest.fixture
async def protected_url() -> AsyncGenerator[str, None]:
    async with serve_http_app("sse", host_origin_protection=True) as url:
        yield url


@pytest.fixture
async def unprotected_url() -> AsyncGenerator[str, None]:
    async with serve_http_app("sse", host_origin_protection=False) as url:
        yield url


class TestSSEConnectionEndpoint:
    @pytest.mark.parametrize(
        ("headers", "expected_status"),
        [
            (UNTRUSTED_HOST, 421),
            (UNTRUSTED_ORIGIN, 403),
        ],
    )
    async def test_rejects_untrusted_request(
        self,
        protected_url: str,
        headers: dict[str, str],
        expected_status: int,
    ):
        assert await _get_status(protected_url, headers) == expected_status

    async def test_disabled_protection_opens_stream(self, unprotected_url: str):
        status = await _get_status(
            unprotected_url,
            {**UNTRUSTED_HOST, **UNTRUSTED_ORIGIN},
        )

        assert status == 200


class TestSSEMessageEndpoint:
    @pytest.mark.parametrize(
        ("headers", "expected_status"),
        [
            (UNTRUSTED_HOST, 421),
            (UNTRUSTED_ORIGIN, 403),
        ],
    )
    async def test_rejects_untrusted_request_for_open_session(
        self,
        protected_url: str,
        headers: dict[str, str],
        expected_status: int,
    ):
        async with _sse_session(protected_url) as message_url:
            status = await _post_status(message_url, headers)
            trusted_status = await _post_status(message_url, {})

        assert status == expected_status
        assert trusted_status == 202

    async def test_disabled_protection_accepts_message(self, unprotected_url: str):
        async with _sse_session(unprotected_url) as message_url:
            status = await _post_status(
                message_url,
                {**UNTRUSTED_HOST, **UNTRUSTED_ORIGIN},
            )

        assert status == 202


class TestSSEProtectionMatchesStreamableHTTP:
    """The same protection settings give the same guard result on both transports."""

    @pytest.mark.parametrize(
        ("protection", "allowed_hosts", "allowed_origins", "headers", "expected"),
        [
            ("auto", None, None, UNTRUSTED_HOST, "host rejected"),
            ("auto", None, None, UNTRUSTED_ORIGIN, "origin rejected"),
            ("auto", None, None, {"origin": "http://localhost:3000"}, "allowed"),
            (
                "auto",
                ["mcp.example.com"],
                ["https://app.example.com"],
                {"host": "mcp.example.com", "origin": "https://app.example.com"},
                "allowed",
            ),
            (True, ["mcp.example.com"], None, {"host": "mcp.example.com"}, "allowed"),
            (True, None, None, {"host": "mcp.example.com"}, "host rejected"),
            (False, None, None, {**UNTRUSTED_HOST, **UNTRUSTED_ORIGIN}, "allowed"),
        ],
    )
    async def test_sse_and_streamable_http_agree(
        self,
        protection: HostOriginProtection,
        allowed_hosts: list[str] | None,
        allowed_origins: list[str] | None,
        headers: dict[str, str],
        expected: GuardResult,
    ):
        results: dict[str, GuardResult] = {}
        for transport in ("http", "sse"):
            async with serve_http_app(
                transport,
                host_origin_protection=protection,
                allowed_hosts=allowed_hosts,
                allowed_origins=allowed_origins,
            ) as url:
                results[transport] = _guard_result(await _get_status(url, headers))

        assert results == {"http": expected, "sse": expected}


class TestSSEProtectionConfiguration:
    def test_invalid_value_is_rejected(self):
        invalid_value: Any = "always"

        with pytest.raises(ValueError, match="host_origin_protection"):
            create_sse_app(
                server=create_server(),
                message_path="/messages/",
                sse_path="/sse",
                host_origin_protection=invalid_value,
            )

    async def test_trusted_client_completes_tool_call(self, protected_url: str):
        async with Client(SSETransport(protected_url)) as client:
            result = await client.call_tool("greet", {"name": "World"})

        assert result.data == "Hello, World!"

    async def test_setting_protects_running_server(self):
        with temporary_settings(http_host_origin_protection=True):
            async with run_server_async(
                create_server(),
                transport="sse",
                path="/sse",
            ) as url:
                untrusted_status = await _get_status(url, UNTRUSTED_HOST)

                async with Client(SSETransport(url)) as client:
                    result = await client.call_tool("greet", {"name": "World"})

        assert untrusted_status == 421
        assert result.data == "Hello, World!"
