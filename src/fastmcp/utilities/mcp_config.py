from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

from pydantic import AnyUrl, BaseModel, Field

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


class StdioMCPServer(BaseModel):
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


class RemoteMCPServer(BaseModel):
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    transport: Literal["streamable-http", "sse", "http"] | None = None

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
    mcpServers: dict[str, StdioMCPServer | RemoteMCPServer]

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> MCPConfig:
        return cls(mcpServers=config.get("mcpServers", config))
