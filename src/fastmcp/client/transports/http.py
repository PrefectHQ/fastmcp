"""Streamable HTTP transport for FastMCP Client."""

from __future__ import annotations

import contextlib
import datetime
from collections.abc import AsyncIterator, Callable
from typing import Literal, cast

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import McpHttpClientFactory, create_mcp_http_client
from pydantic import AnyUrl
from typing_extensions import Unpack

import fastmcp
from fastmcp.client.auth.bearer import BearerAuth
from fastmcp.client.auth.oauth import OAuth
from fastmcp.client.transports.base import ClientTransport, SessionKwargs
from fastmcp.server.dependencies import get_http_headers
from fastmcp.utilities.timeout import normalize_timeout_to_timedelta


class StreamableHttpTransport(ClientTransport):
    """Transport implementation that connects to an MCP server via Streamable HTTP Requests."""

    def __init__(
        self,
        url: str | AnyUrl,
        headers: dict[str, str] | None = None,
        auth: httpx.Auth | Literal["oauth"] | str | None = None,
        sse_read_timeout: datetime.timedelta | float | int | None = None,
        httpx_client_factory: McpHttpClientFactory | None = None,
    ):
        """Initialize a Streamable HTTP transport.

        Args:
            url: The MCP server endpoint URL.
            headers: Optional headers to include in requests.
            auth: Authentication method - httpx.Auth, "oauth" for OAuth flow,
                or a bearer token string.
            sse_read_timeout: Deprecated. Use read_timeout_seconds in session_kwargs.
            httpx_client_factory: Optional factory for creating httpx.AsyncClient.
                If provided, must accept keyword arguments: headers, auth,
                follow_redirects, and optionally timeout. Using **kwargs is
                recommended to ensure forward compatibility.
        """
        if isinstance(url, AnyUrl):
            url = str(url)
        if not isinstance(url, str) or not url.startswith("http"):
            raise ValueError("Invalid HTTP/S URL provided for Streamable HTTP.")

        # Don't modify the URL path - respect the exact URL provided by the user
        # Some servers are strict about trailing slashes (e.g., PayPal MCP)

        self.url: str = url
        self.headers = headers or {}
        self.httpx_client_factory = httpx_client_factory
        self._set_auth(auth)

        if sse_read_timeout is not None:
            if fastmcp.settings.deprecation_warnings:
                import warnings

                warnings.warn(
                    "The `sse_read_timeout` parameter is deprecated and no longer used. "
                    "The new streamable_http_client API does not support this parameter. "
                    "Use `read_timeout_seconds` in session_kwargs or configure timeout on "
                    "the httpx client via `httpx_client_factory` instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )
        self.sse_read_timeout = normalize_timeout_to_timedelta(sse_read_timeout)

        self._get_session_id_cb: Callable[[], str | None] | None = None

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
        # Load headers from an active HTTP request, if available. This will only be true
        # if the client is used in a FastMCP Proxy, in which case the MCP client headers
        # need to be forwarded to the remote server.
        headers = get_http_headers() | self.headers

        # Configure timeout if provided, preserving MCP's 30s connect default
        timeout: httpx.Timeout | None = None
        if session_kwargs.get("read_timeout_seconds") is not None:
            read_timeout_seconds = cast(
                datetime.timedelta, session_kwargs.get("read_timeout_seconds")
            )
            timeout = httpx.Timeout(30.0, read=read_timeout_seconds.total_seconds())

        # Create httpx client from factory or use default with MCP-appropriate timeouts
        # create_mcp_http_client uses 30s connect/5min read timeout by default,
        # and always enables follow_redirects
        if self.httpx_client_factory is not None:
            # Factory clients get the full kwargs for backwards compatibility
            http_client = self.httpx_client_factory(
                headers=headers,
                auth=self.auth,
                follow_redirects=True,  # type: ignore[call-arg]
                **({"timeout": timeout} if timeout else {}),
            )
        else:
            http_client = create_mcp_http_client(
                headers=headers,
                timeout=timeout,
                auth=self.auth,
            )

        # Ensure httpx client is closed after use
        async with (
            http_client,
            streamable_http_client(self.url, http_client=http_client) as transport,
        ):
            read_stream, write_stream, get_session_id = transport
            self._get_session_id_cb = get_session_id
            async with ClientSession(
                read_stream, write_stream, **session_kwargs
            ) as session:
                yield session

    def get_session_id(self) -> str | None:
        if self._get_session_id_cb:
            try:
                return self._get_session_id_cb()
            except Exception:
                return None
        return None

    async def close(self):
        # Reset the session id callback
        self._get_session_id_cb = None

    def __repr__(self) -> str:
        return f"<StreamableHttpTransport(url='{self.url}')>"
