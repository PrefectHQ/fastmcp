"""Canonical MCP Configuration Format.

This module defines the standard configuration format for Model Context Protocol (MCP) servers.
It provides a client-agnostic, extensible format that can be used across all MCP implementations.

The configuration format supports both stdio and remote (HTTP/SSE) transports, with comprehensive
field definitions for server metadata, authentication, and execution parameters.

Example configuration:
```json
{
    "mcpServers": {
        "my-server": {
            "command": "npx",
            "args": ["-y", "@my/mcp-server"],
            "env": {"API_KEY": "secret"},
            "timeout": 30000,
            "description": "My MCP server"
        }
    }
}
```
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast
from urllib.parse import urlparse

import httpx
from pydantic import (
    AnyUrl,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)
from typing_extensions import Self, override

from fastmcp.tools.tool_transform import ToolTransformConfig
from fastmcp.utilities.types import FastMCPBaseModel

if TYPE_CHECKING:
    from fastmcp.client.transports import (
        ClientTransport,
        SSETransport,
        StdioTransport,
        StreamableHttpTransport,
    )
    from fastmcp.server.server import FastMCP


def infer_transport_type_from_url(
    url: str | AnyUrl,
) -> Literal["http", "sse"]:
    """
    Infer the appropriate transport type from the given URL.
    """
    url = str(url)
    if not url.startswith("http"):
        raise ValueError(f"Invalid URL: {url}")

    parsed_url = urlparse(url)
    path = parsed_url.path

    # Match /sse followed by /, ?, &, or end of string
    if re.search(r"/sse(/|\?|&|$)", path):
        return "sse"
    else:
        return "http"


class _TransformingMCPServerMixin(FastMCPBaseModel):
    """A mixin that enables wrapping an MCP Server with tool transforms."""

    tools: dict[str, ToolTransformConfig] = Field(...)
    """The multi-tool transform to apply to the tools."""

    include_tags: set[str] | None = Field(
        default=None,
        description="The tags to include in the proxy.",
    )

    exclude_tags: set[str] | None = Field(
        default=None,
        description="The tags to exclude in the proxy.",
    )

    def _to_server_and_underlying_transport(
        self,
        server_name: str | None = None,
        client_name: str | None = None,
    ) -> tuple[FastMCP[Any], ClientTransport]:
        """Turn the Transforming MCPServer into a FastMCP Server and also return the underlying transport."""
        from fastmcp import FastMCP
        from fastmcp.client import Client
        from fastmcp.client.transports import (
            ClientTransport,  # pyright: ignore[reportUnusedImport]
        )

        transport: ClientTransport = super().to_transport()  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue, reportUnknownVariableType]
        transport = cast(ClientTransport, transport)

        client: Client[ClientTransport] = Client(transport=transport, name=client_name)

        wrapped_mcp_server = FastMCP.as_proxy(
            name=server_name,
            backend=client,
            tool_transformations=self.tools,
            include_tags=self.include_tags,
            exclude_tags=self.exclude_tags,
        )

        return wrapped_mcp_server, transport

    def to_transport(self) -> ClientTransport:
        """Get the transport for the transforming MCP server."""
        from fastmcp.client.transports import FastMCPTransport

        return FastMCPTransport(mcp=self._to_server_and_underlying_transport()[0])


class StdioMCPServer(BaseModel):
    """MCP server configuration for stdio transport.

    This is the canonical configuration format for MCP servers using stdio transport.
    """

    # Required fields
    command: str

    # Common optional fields
    args: list[str] = Field(default_factory=list)
    env: dict[str, Any] = Field(default_factory=dict)

    # Transport specification
    transport: Literal["stdio"] = "stdio"
    type: Literal["stdio"] | None = None  # Alternative transport field name

    # Execution context
    cwd: str | None = None  # Working directory for command execution
    timeout: int | None = None  # Maximum response time in milliseconds

    # Metadata
    description: str | None = None  # Human-readable server description
    icon: str | None = None  # Icon path or URL for UI display

    # Authentication configuration
    authentication: dict[str, Any] | None = None  # Auth configuration object

    model_config = ConfigDict(extra="allow")  # Preserve unknown fields

    def to_transport(self) -> StdioTransport:
        from fastmcp.client.transports import StdioTransport

        return StdioTransport(
            command=self.command,
            args=self.args,
            env=self.env,
            cwd=self.cwd,
        )


class TransformingStdioMCPServer(_TransformingMCPServerMixin, StdioMCPServer):
    """A Stdio server with tool transforms."""


class RemoteMCPServer(BaseModel):
    """MCP server configuration for HTTP/SSE transport.

    This is the canonical configuration format for MCP servers using remote transports.
    """

    # Required fields
    url: str

    # Transport configuration
    transport: Literal["http", "streamable-http", "sse"] | None = None
    headers: dict[str, str] = Field(default_factory=dict)

    # Authentication
    auth: Annotated[
        str | Literal["oauth"] | httpx.Auth | None,
        Field(
            description='Either a string representing a Bearer token, the literal "oauth" to use OAuth authentication, or an httpx.Auth instance for custom authentication.',
        ),
    ] = None

    # Timeout configuration
    sse_read_timeout: datetime.timedelta | int | float | None = None
    timeout: int | None = None  # Maximum response time in milliseconds

    # Metadata
    description: str | None = None  # Human-readable server description
    icon: str | None = None  # Icon path or URL for UI display

    # Authentication configuration
    authentication: dict[str, Any] | None = None  # Auth configuration object

    model_config = ConfigDict(
        extra="allow", arbitrary_types_allowed=True
    )  # Preserve unknown fields

    def to_transport(self) -> StreamableHttpTransport | SSETransport:
        from fastmcp.client.transports import SSETransport, StreamableHttpTransport

        if self.transport is None:
            transport = infer_transport_type_from_url(self.url)
        else:
            transport = self.transport

        if transport == "sse":
            return SSETransport(
                self.url,
                headers=self.headers,
                auth=self.auth,
                sse_read_timeout=self.sse_read_timeout,
            )
        else:
            # Both "http" and "streamable-http" map to StreamableHttpTransport
            return StreamableHttpTransport(
                self.url,
                headers=self.headers,
                auth=self.auth,
                sse_read_timeout=self.sse_read_timeout,
            )


class TransformingRemoteMCPServer(_TransformingMCPServerMixin, RemoteMCPServer):
    """A Remote server with tool transforms."""


TransformingMCPServerTypes = TransformingStdioMCPServer | TransformingRemoteMCPServer

CanonicalMCPServerTypes = StdioMCPServer | RemoteMCPServer

MCPServerTypes = TransformingMCPServerTypes | CanonicalMCPServerTypes


class MCPConfig(BaseModel):
    """A configuration object for MCP Servers that conforms to the canonical MCP configuration format
    while adding additional fields for enabling FastMCP-specific features like tool transformations
    and filtering by tags.

    For an MCPConfig that is strictly canonical, see the `CanonicalMCPConfig` class.
    """

    mcpServers: dict[str, MCPServerTypes] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")  # Preserve unknown top-level fields

    @model_validator(mode="before")
    @classmethod
    def wrap_servers_at_root(cls, values: dict[str, Any]) -> dict[str, Any]:
        """If there's no mcpServers key but there are server configs at root, wrap them."""
        if "mcpServers" not in values:
            # Check if any values look like server configs
            has_servers = any(
                isinstance(v, dict) and ("command" in v or "url" in v)
                for v in values.values()
            )
            if has_servers:
                # Move all server-like configs under mcpServers
                return {"mcpServers": values}
        return values

    def add_server(self, name: str, server: MCPServerTypes) -> None:
        """Add or update a server in the configuration."""
        self.mcpServers[name] = server

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> Self:
        """Parse MCP configuration from dictionary format."""
        return cls.model_validate(config)

    def to_dict(self) -> dict[str, Any]:
        """Convert MCPConfig to dictionary format, preserving all fields."""
        return self.model_dump(exclude_none=True)

    def write_to_file(self, file_path: Path) -> None:
        """Write configuration to JSON file."""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(self.model_dump_json(indent=2))

    @classmethod
    def from_file(cls, file_path: Path) -> Self:
        """Load configuration from JSON file."""
        if file_path.exists() and (content := file_path.read_text().strip()):
            return cls.model_validate_json(content)

        raise ValueError(f"No MCP servers defined in the config: {file_path}")


class CanonicalMCPConfig(MCPConfig):
    """Canonical MCP configuration format.

    This defines the standard configuration format for Model Context Protocol servers.
    The format is designed to be client-agnostic and extensible for future use cases.
    """

    mcpServers: dict[str, CanonicalMCPServerTypes] = Field(default_factory=dict)

    @override
    def add_server(self, name: str, server: CanonicalMCPServerTypes) -> None:
        """Add or update a server in the configuration."""
        self.mcpServers[name] = server


def update_config_file(
    file_path: Path,
    server_name: str,
    server_config: CanonicalMCPServerTypes,
) -> None:
    """Update an MCP configuration file from a server object, preserving existing fields.

    This is used for updating the mcpServer configurations of third-party tools so we do not
    worry about transforming server objects here."""
    config = MCPConfig.from_file(file_path)

    # If updating an existing server, merge with existing configuration
    # to preserve any unknown fields
    if existing_server := config.mcpServers.get(server_name):
        # Get the raw dict representation of both servers
        existing_dict = existing_server.model_dump()

        new_dict = server_config.model_dump(exclude_none=True)

        # Merge, with new values taking precedence
        merged_config = server_config.model_validate({**existing_dict, **new_dict})

        config.add_server(server_name, merged_config)
    else:
        config.add_server(server_name, server_config)

    config.write_to_file(file_path)
