import contextlib
import datetime
import logging
from collections.abc import AsyncIterator
from typing import cast

from mcp import ClientSession
from mcp.client.sse import sse_client
from pydantic import AnyUrl
from typing_extensions import Unpack

from fastmcp.client.auth.httpx_client import patch_mcp_httpx_client
from fastmcp.client.client import ClientTransport, SessionKwargs

logger = logging.getLogger(__name__)


class SSETransport(ClientTransport):
    """Transport implementation that connects to an MCP server via Server-Sent Events."""

    def __init__(
        self,
        url: str | AnyUrl,
        headers: dict[str, str] | None = None,
        sse_read_timeout: datetime.timedelta | float | int | None = None,
    ):
        if isinstance(url, AnyUrl):
            url = str(url)
        if not isinstance(url, str) or not url.startswith("http"):
            raise ValueError("Invalid HTTP/S URL provided for SSE.")
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
            client_kwargs["sse_read_timeout"] = self.sse_read_timeout.total_seconds()
        if session_kwargs.get("read_timeout_seconds", None) is not None:
            read_timeout_seconds = cast(
                datetime.timedelta, session_kwargs.get("read_timeout_seconds")
            )
            client_kwargs["timeout"] = read_timeout_seconds.total_seconds()

        with patch_mcp_httpx_client(self.url):
            async with sse_client(
                self.url, headers=self.headers, **client_kwargs
            ) as transport:
                read_stream, write_stream = transport
                async with ClientSession(
                    read_stream, write_stream, **session_kwargs
                ) as session:
                    await session.initialize()
                    yield session

    def __repr__(self) -> str:
        return f"<SSE(url='{self.url}')>"
