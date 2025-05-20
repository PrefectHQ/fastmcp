from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal, TypeAlias

from pydantic import Field
from pydantic.dataclasses import dataclass

if TYPE_CHECKING:
    from fastmcp.client.client import Client
    from fastmcp.client.transports import (
        SSETransport,
        StdioTransport,
        StreamableHttpTransport,
    )


@dataclass
class LocalMCPServer:
    command: str
    args: list[str]
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


@dataclass
class RemoteMCPServer:
    url: str
    transport: Literal["http", "sse"] | None = None
    headers: dict[str, str] = Field(default_factory=dict)

    def to_transport(self) -> StreamableHttpTransport | SSETransport:
        from fastmcp.client.transports import SSETransport, StreamableHttpTransport

        if self.transport in {"http", None}:
            return StreamableHttpTransport(self.url, headers=self.headers)
        else:
            return SSETransport(self.url, headers=self.headers)


MCPServer: TypeAlias = LocalMCPServer | RemoteMCPServer


@dataclass
class MCPConfig:
    mcp_servers: Annotated[dict[str, MCPServer], Field(alias="mcpServers")]

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> MCPConfig:
        return cls(mcp_servers=config.get("mcpServers", config))

    def to_transports(
        self,
    ) -> dict[str, StdioTransport | StreamableHttpTransport | SSETransport]:
        return {
            name: server.to_transport() for name, server in self.mcp_servers.items()
        }

    def to_clients(self) -> dict[str, Client]:
        from fastmcp.client.client import Client

        return {
            name: Client(transport=transport)
            for name, transport in self.to_transports().items()
        }
