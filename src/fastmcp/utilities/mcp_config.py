from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import AnyUrl, Field

from fastmcp.utilities.types import FastMCPBaseModel

if TYPE_CHECKING:
    from fastmcp.client.transports import (
        SSETransport,
        StdioTransport,
        StreamableHttpTransport,
    )


def infer_transport_type_from_url(
    url: str | AnyUrl,
) -> Literal["streamable-http", "sse"]:
    """
    Infer the appropriate transport type from the given URL.
    """
    url = str(url)
    if not url.startswith("http"):
        raise ValueError(f"Invalid URL: {url}")

    parsed_url = urlparse(url)
    path = parsed_url.path

    if "/sse/" in path or path.rstrip("/").endswith("/sse"):
        return "sse"
    else:
        return "streamable-http"


class StdioMCPServer(FastMCPBaseModel):
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, Any] = Field(default_factory=dict)
    cwd: str | None = None
    transport: Literal["stdio"] = "stdio"

    def to_transport(self) -> StdioTransport:
        from fastmcp.client.transports import StdioTransport

        return StdioTransport(
            command=self.command,
            args=self.args,
            env=self.env,
            cwd=self.cwd,
        )


class RemoteMCPServer(FastMCPBaseModel):
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    transport: Literal["streamable-http", "sse"] | None = None
    auth: Annotated[
        str | Literal["oauth"] | None,
        Field(
            description='Either a string representing a Bearer token or the literal "oauth" to use OAuth authentication.'
        ),
    ] = None

    def to_transport(self) -> StreamableHttpTransport | SSETransport:
        from fastmcp.client.transports import SSETransport, StreamableHttpTransport

        if self.transport is None:
            transport = infer_transport_type_from_url(self.url)
        else:
            transport = self.transport

        if transport == "sse":
            return SSETransport(self.url, headers=self.headers, auth=self.auth)
        else:
            return StreamableHttpTransport(
                self.url, headers=self.headers, auth=self.auth
            )


class MCPConfig(FastMCPBaseModel):
    mcpServers: dict[str, StdioMCPServer | RemoteMCPServer]

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> MCPConfig:
        return cls(mcpServers=config.get("mcpServers", config))
