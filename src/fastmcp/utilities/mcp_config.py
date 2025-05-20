from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

from pydantic import AnyUrl, BaseModel, Field

if TYPE_CHECKING:
    from fastmcp.client.client import Client
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


class LocalMCPServer(BaseModel):
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, Any] = Field(default_factory=dict)
    cwd: str | None = None

    def to_transport(self) -> StdioTransport:
        from fastmcp.client.transports import StdioTransport

        return StdioTransport(
            command=self.command,
            args=self.args,
            env=self.env,
            cwd=self.cwd,
        )


class RemoteMCPServer(BaseModel):
    url: str
    transport: Literal["streamable-http", "sse", "http"] | None = None
    headers: dict[str, str] = Field(default_factory=dict)

    def to_transport(self) -> StreamableHttpTransport | SSETransport:
        from fastmcp.client.transports import SSETransport, StreamableHttpTransport

        if self.transport is None:
            transport = infer_transport_type_from_url(self.url)
        else:
            transport = self.transport

        if transport == "sse":
            return SSETransport(self.url, headers=self.headers)
        else:
            return StreamableHttpTransport(self.url, headers=self.headers)


class MCPConfig(BaseModel):
    mcpServers: dict[str, LocalMCPServer | RemoteMCPServer]

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> MCPConfig:
        return cls(mcpServers=config.get("mcpServers", config))

    def to_transports(
        self,
    ) -> dict[str, StdioTransport | StreamableHttpTransport | SSETransport]:
        return {name: server.to_transport() for name, server in self.mcpServers.items()}

    def to_clients(self) -> dict[str, Client]:
        from fastmcp.client.client import Client

        return {
            name: Client(transport=transport)
            for name, transport in self.to_transports().items()
        }
