"""Streamable HTTP transport for FastMCP Client."""

from __future__ import annotations

import contextlib
import ssl
from collections.abc import AsyncIterator
from typing import Any, Literal, cast

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import McpHttpClientFactory, create_mcp_http_client
from pydantic import AnyUrl
from typing_extensions import Unpack

from fastmcp.client.auth.bearer import BearerAuth
from fastmcp.client.auth.client_credentials import (
    ClientCredentialsOAuthProvider,
    PrivateKeyJWTOAuthProvider,
)
from fastmcp.client.auth.oauth import OAuth
from fastmcp.client.dependencies import get_http_headers
from fastmcp.client.transports.base import (
    ClientTransport,
    SessionKwargs,
    TransportOptions,
)


class StreamableHttpTransport(ClientTransport):
    """Transport implementation that connects to an MCP server via Streamable HTTP Requests."""

    def __init__(
        self,
        url: str | AnyUrl,
        headers: dict[str, str] | None = None,
        auth: httpx2.Auth | Literal["oauth"] | str | None = None,
        httpx_client_factory: McpHttpClientFactory | None = None,
        verify: ssl.SSLContext | bool | str | None = None,
    ):
        """Initialize a Streamable HTTP transport.

        Args:
            url: The MCP server endpoint URL.
            headers: Optional headers to include in requests.
            auth: Authentication method - httpx2.Auth, "oauth" for OAuth flow,
                or a bearer token string.
            httpx_client_factory: Optional factory for creating httpx2.AsyncClient.
                If provided, must accept keyword arguments: headers, auth,
                follow_redirects, and optionally timeout. Using **kwargs is
                recommended to ensure forward compatibility.
            verify: SSL certificate verification. Accepts False to disable
                verification, a path to a CA bundle, or an ssl.SSLContext
                for full control. None (default) uses httpx defaults (verification
                enabled). Ignored when httpx_client_factory is provided.
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
        self.verify: ssl.SSLContext | bool | str | None = verify

        if httpx_client_factory is not None and verify is not None:
            import warnings

            warnings.warn(
                "Both 'httpx_client_factory' and 'verify' were provided. "
                "The 'verify' parameter will be ignored because "
                "'httpx_client_factory' takes precedence. Configure SSL "
                "verification directly in your httpx_client_factory instead.",
                UserWarning,
                stacklevel=2,
            )

        self._set_auth(auth)

        # SDK v2's streamable_http_client no longer exposes a get_session_id
        # callback. We recover the session id ourselves by capturing the
        # `mcp-session-id` response header via an httpx event hook on the
        # client we own (see connect_session / _capture_session_id).
        self._session_id: str | None = None

    async def _capture_session_id(self, response: httpx2.Response) -> None:
        """httpx response event hook: record the server's `mcp-session-id`.

        The streamable HTTP server assigns the session id in the response to
        the initialize request and echoes it on subsequent responses; we keep
        the latest non-empty value.
        """
        sid = response.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid

    def _set_auth(self, auth: httpx2.Auth | Literal["oauth"] | str | None):
        resolved: httpx2.Auth | None
        if auth == "oauth":
            resolved = OAuth(
                self.url,
                httpx_client_factory=self.httpx_client_factory
                or self._make_verify_factory(),
            )
        elif isinstance(auth, OAuth):
            auth._bind(self.url)
            # Only inject the transport's factory into OAuth if OAuth still
            # has the bare default — preserve any factory the caller attached
            if auth.httpx_client_factory is httpx2.AsyncClient:
                factory = self.httpx_client_factory or self._make_verify_factory()
                if factory is not None:
                    auth.httpx_client_factory = factory
            resolved = auth
        elif isinstance(
            auth, (ClientCredentialsOAuthProvider, PrivateKeyJWTOAuthProvider)
        ):
            auth._bind(self.url)
            resolved = auth
        elif isinstance(auth, str):
            resolved = BearerAuth(auth)
        else:
            resolved = auth
        self.auth: httpx2.Auth | None = resolved

    def _make_verify_factory(self) -> McpHttpClientFactory | None:
        if self.verify is None:
            return None
        verify = self.verify

        def factory(
            headers: dict[str, str] | None = None,
            timeout: httpx2.Timeout | None = None,
            auth: httpx2.Auth | None = None,
        ) -> httpx2.AsyncClient:
            if timeout is None:
                timeout = httpx2.Timeout(30.0, read=300.0)
            kwargs: dict[str, Any] = {
                "follow_redirects": True,
                "timeout": timeout,
                "verify": verify,
            }
            if headers is not None:
                kwargs["headers"] = headers
            if auth is not None:
                kwargs["auth"] = auth
            return httpx2.AsyncClient(**kwargs)

        return cast(McpHttpClientFactory, factory)

    @contextlib.asynccontextmanager
    async def connect_session(
        self,
        *,
        transport_options: TransportOptions | None = None,
        **session_kwargs: Unpack[SessionKwargs],
    ) -> AsyncIterator[ClientSession]:
        options = transport_options or TransportOptions()

        # When used in a proxy, forward the inbound request's authorization
        # header to the upstream server. This is off by default so that a
        # plain Client used inside a server tool handler doesn't accidentally
        # leak the caller's credentials to an unrelated remote server.
        if options.forward_incoming_headers:
            headers = get_http_headers(include={"authorization"}) | self.headers
        else:
            headers = dict(self.headers)

        # Configure timeout if provided, preserving MCP's 30s connect default.
        # SDK v2 session read timeouts are float seconds (see SessionKwargs).
        timeout: httpx2.Timeout | None = None
        read_timeout_seconds = session_kwargs.get("read_timeout_seconds")
        if read_timeout_seconds is not None:
            timeout = httpx2.Timeout(30.0, read=read_timeout_seconds)

        # Create httpx client from factory or use default with MCP-appropriate
        # timeouts. Note: create_mcp_http_client enables follow_redirects, but
        # httpx automatically strips Authorization headers on cross-origin
        # redirects to prevent credential leakage.
        verify_factory = self._make_verify_factory()
        if self.httpx_client_factory is not None:
            http_client = self.httpx_client_factory(
                headers=headers,
                auth=self.auth,
                follow_redirects=True,  # type: ignore[call-arg]  # ty:ignore[unknown-argument]
                **({"timeout": timeout} if timeout else {}),
            )
        elif verify_factory is not None:
            http_client = verify_factory(
                headers=headers,
                timeout=timeout,
                auth=self.auth,
            )
        else:
            http_client = create_mcp_http_client(
                headers=headers,
                timeout=timeout,
                auth=self.auth,
            )

        # SDK v2's streamable_http_client no longer surfaces the session id, so
        # capture it off the `mcp-session-id` response header on the client we
        # own. Register on whichever instance is actually used (factory paths).
        self._session_id = None
        http_client.event_hooks.setdefault("response", []).append(
            self._capture_session_id
        )

        # Ensure httpx client is closed after use. SDK v2 streamable_http_client
        # yields a 2-tuple (read, write); get_session_id is gone from the transport.
        async with (
            http_client,
            streamable_http_client(self.url, http_client=http_client) as (
                read_stream,
                write_stream,
            ),
            options.session_class(
                read_stream, write_stream, **session_kwargs
            ) as session,
        ):
            yield session

    def get_session_id(self) -> str | None:
        return self._session_id

    async def close(self):
        # Reset the captured session id
        self._session_id = None

    def __repr__(self) -> str:
        return f"<StreamableHttpTransport(url='{self.url}')>"
