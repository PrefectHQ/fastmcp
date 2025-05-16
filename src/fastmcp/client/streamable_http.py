import contextlib
import datetime
from collections.abc import AsyncIterator

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from pydantic import AnyUrl
from typing_extensions import Unpack

from fastmcp.client.auth.httpx_client import patch_mcp_httpx_client
from fastmcp.client.client import ClientTransport, SessionKwargs
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class StreamableHttpTransport(ClientTransport):
    """Transport implementation that connects to an MCP server via Streamable HTTP Requests."""

    def __init__(
        self,
        url: str | AnyUrl,
        headers: dict[str, str] | None = None,
        sse_read_timeout: datetime.timedelta | float | int | None = None,
    ):
        if isinstance(url, AnyUrl):
            url = str(url)
        if not isinstance(url, str) or not url.startswith("http"):
            raise ValueError("Invalid HTTP/S URL provided for Streamable HTTP.")
        self.url = url
        self.headers = headers or {}

        if isinstance(sse_read_timeout, int | float):
            sse_read_timeout = datetime.timedelta(seconds=sse_read_timeout)
        self.sse_read_timeout = sse_read_timeout

    @contextlib.asynccontextmanager
    async def connect_session(
        self, **session_kwargs: Unpack[SessionKwargs]
    ) -> AsyncIterator[ClientSession]:
        client_kwargs = {}
        # sse_read_timeout has a default value set, so we can't pass None without overriding it
        # instead we simply leave the kwarg out if it's not provided
        if self.sse_read_timeout is not None:
            client_kwargs["sse_read_timeout"] = self.sse_read_timeout
        if session_kwargs.get("read_timeout_seconds", None) is not None:
            client_kwargs["timeout"] = session_kwargs.get("read_timeout_seconds")

        with patch_mcp_httpx_client(self.url):
            async with streamablehttp_client(
                self.url, headers=self.headers, **client_kwargs
            ) as transport:
                read_stream, write_stream, _ = transport
                async with ClientSession(
                    read_stream, write_stream, **session_kwargs
                ) as session:
                    await session.initialize()
                    yield session

    def __repr__(self) -> str:
        return f"<StreamableHttp(url='{self.url}')>"
