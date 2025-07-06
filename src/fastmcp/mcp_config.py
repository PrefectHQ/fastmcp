"""Canonical MCP Configuration Format.

This module defines the standard configuration format for Model Context Protocol (MCP) servers.
It provides a client-agnostic, extensible format that can be used across all MCP implementations.

The configuration format supports both stdio and remote (HTTP/SSE) transports, with comprehensive
field definitions for server metadata, authentication, and execution parameters.

Example configuration:
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
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import AnyUrl, BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from fastmcp.client.transports import (
        SSETransport,
        StdioTransport,
        StreamableHttpTransport,
    )


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


class MCPConfig(BaseModel):
    """Canonical MCP configuration format.

    This defines the standard configuration format for Model Context Protocol servers.
    The format is designed to be client-agnostic and extensible for future use cases.
    """

    mcpServers: dict[str, StdioMCPServer | RemoteMCPServer]

    model_config = ConfigDict(extra="allow")  # Preserve unknown top-level fields

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> MCPConfig:
        """Parse MCP configuration from dictionary format."""
        # Handle case where config is just the mcpServers object
        if "mcpServers" not in config and any(
            isinstance(v, dict) and ("command" in v or "url" in v)
            for v in config.values()
        ):
            # This looks like a bare mcpServers object
            servers_dict = config
        else:
            # Standard format with mcpServers wrapper
            servers_dict = config.get("mcpServers", {})

        # Parse each server configuration
        parsed_servers = {}
        for name, server_config in servers_dict.items():
            if not isinstance(server_config, dict):
                continue

            # Determine if this is stdio or remote based on fields
            if "command" in server_config:
                parsed_servers[name] = StdioMCPServer.model_validate(server_config)
            elif "url" in server_config:
                parsed_servers[name] = RemoteMCPServer.model_validate(server_config)
            else:
                # Skip invalid server configs but preserve them as raw dicts
                # This allows for forward compatibility with unknown server types
                continue

        # Create config with any extra top-level fields preserved
        config_data = {k: v for k, v in config.items() if k != "mcpServers"}
        config_data["mcpServers"] = parsed_servers

        return cls.model_validate(config_data)

    def to_dict(self) -> dict[str, Any]:
        """Convert MCPConfig to dictionary format, preserving all fields."""
        # Start with all extra fields at the top level
        result = self.model_dump(exclude={"mcpServers"}, exclude_none=True)

        # Add mcpServers with all fields preserved
        result["mcpServers"] = {
            name: server.model_dump(exclude_none=True)
            for name, server in self.mcpServers.items()
        }

        return result

    def write_to_file(self, file_path: Path) -> None:
        """Write configuration to JSON file."""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def from_file(cls, file_path: Path) -> MCPConfig:
        """Load configuration from JSON file."""
        if not file_path.exists():
            return cls(mcpServers={})
        with open(file_path) as f:
            content = f.read().strip()
            if not content:
                return cls(mcpServers={})
            data = json.loads(content)
        return cls.from_dict(data)

    def add_server(self, name: str, server: StdioMCPServer | RemoteMCPServer) -> None:
        """Add or update a server in the configuration."""
        self.mcpServers[name] = server

    def remove_server(self, name: str) -> None:
        """Remove a server from the configuration."""
        if name in self.mcpServers:
            del self.mcpServers[name]


def update_config_file(
    file_path: Path,
    server_name: str,
    server_config: StdioMCPServer | RemoteMCPServer,
) -> None:
    """Update MCP configuration file with new server, preserving existing fields."""
    config = MCPConfig.from_file(file_path)

    # If updating an existing server, merge with existing configuration
    # to preserve any unknown fields
    if server_name in config.mcpServers:
        existing_server = config.mcpServers[server_name]
        # Get the raw dict representation of both servers
        existing_dict = existing_server.model_dump()
        new_dict = server_config.model_dump(exclude_none=True)

        # Merge, with new values taking precedence
        merged_dict = {**existing_dict, **new_dict}

        # Create new server instance with merged data
        if "command" in merged_dict:
            merged_server = StdioMCPServer.model_validate(merged_dict)
        else:
            merged_server = RemoteMCPServer.model_validate(merged_dict)

        config.add_server(server_name, merged_server)
    else:
        config.add_server(server_name, server_config)

    config.write_to_file(file_path)
