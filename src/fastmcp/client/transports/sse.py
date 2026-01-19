"""Server-Sent Events (SSE) transport for FastMCP Client."""

from __future__ import annotations

import contextlib
import datetime
from collections.abc import AsyncIterator
from typing import Any, Literal, cast

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.shared._httpx_utils import McpHttpClientFactory
from pydantic import AnyUrl
from typing_extensions import Unpack

from fastmcp.client.auth.bearer import BearerAuth
from fastmcp.client.auth.oauth import OAuth
from fastmcp.client.transports.base import ClientTransport, SessionKwargs
from fastmcp.server.dependencies import get_http_headers
from fastmcp.utilities.timeout import normalize_timeout_to_timedelta


class SSETransport(ClientTransport):
    """Transport implementation that connects to an MCP server via Server-Sent Events."""

    def __init__(
        self,
        url: str | AnyUrl,
        headers: dict[str, str] | None = None,
        auth: httpx.Auth | Literal["oauth"] | str | None = None,
        sse_read_timeout: datetime.timedelta | float | int | None = None,
        httpx_client_factory: McpHttpClientFactory | None = None,
    ):
        if isinstance(url, AnyUrl):
            url = str(url)
        if not isinstance(url, str) or not url.startswith("http"):
            raise ValueError("Invalid HTTP/S URL provided for SSE.")

        # Don't modify the URL path - respect the exact URL provided by the user
        # Some servers are strict about trailing slashes (e.g., PayPal MCP)

        self.url: str = url
        self.headers = headers or {}
        self.httpx_client_factory = httpx_client_factory
        self._set_auth(auth)

        self.sse_read_timeout = normalize_timeout_to_timedelta(sse_read_timeout)

    def _set_auth(self, auth: httpx.Auth | Literal["oauth"] | str | None):
        if auth == "oauth":
            auth = OAuth(self.url, httpx_client_factory=self.httpx_client_factory)
        elif isinstance(auth, str):
            auth = BearerAuth(auth)
        self.auth = auth

    @contextlib.asynccontextmanager
    async def connect_session(
        self, **session_kwargs: Unpack[SessionKwargs]
    ) -> AsyncIterator[ClientSession]:
        client_kwargs: dict[str, Any] = {}

        # load headers from an active HTTP request, if available. This will only be true
        # if the client is used in a FastMCP Proxy, in which case the MCP client headers
        # need to be forwarded to the remote server.
        client_kwargs["headers"] = get_http_headers() | self.headers

        # sse_read_timeout has a default value set, so we can't pass None without overriding it
        # instead we simply leave the kwarg out if it's not provided
        if self.sse_read_timeout is not None:
            client_kwargs["sse_read_timeout"] = self.sse_read_timeout.total_seconds()
        if session_kwargs.get("read_timeout_seconds") is not None:
            read_timeout_seconds = cast(
                datetime.timedelta, session_kwargs.get("read_timeout_seconds")
            )
            client_kwargs["timeout"] = read_timeout_seconds.total_seconds()

        if self.httpx_client_factory is not None:
            client_kwargs["httpx_client_factory"] = self.httpx_client_factory

        async with sse_client(self.url, auth=self.auth, **client_kwargs) as transport:
            read_stream, write_stream = transport
            async with ClientSession(
                read_stream, write_stream, **session_kwargs
            ) as session:
                yield session

    def __repr__(self) -> str:
        return f"<SSETransport(url='{self.url}')>"
