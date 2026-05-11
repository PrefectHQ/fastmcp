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

if TYPE_CHECKING:
    from fastmcp.client.transports import (
        ClientTransport,
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


def _coerce_tool_transform_configs(tools: dict[str, Any]) -> dict[str, Any]:
    from fastmcp.tools.tool_transform import ToolTransformConfig

    return {
        name: config
        if isinstance(config, ToolTransformConfig)
        else ToolTransformConfig.model_validate(config)
        for name, config in tools.items()
    }


class _TransformingMCPServerMixin(BaseModel):
    """A mixin that enables wrapping an MCP Server with tool transforms."""

    tools: dict[str, Any] = Field(default_factory=dict)
    """The multi-tool transform to apply to the tools."""

    include_tags: set[str] | None = Field(
        default=None,
        description="The tags to include in the proxy.",
    )

    exclude_tags: set[str] | None = Field(
        default=None,
        description="The tags to exclude in the proxy.",
    )

    @model_validator(mode="before")
    @classmethod
    def _require_at_least_one_transform_field(
        cls, values: dict[str, Any]
    ) -> dict[str, Any]:
        """Reject if none of the transforming fields are set.

        This ensures that plain server configs (without tools, include_tags,
        or exclude_tags) fall through to the base server types during union
        validation, avoiding unnecessary proxy wrapping.
        """
        if isinstance(values, dict):
            has_tools = bool(values.get("tools"))
            has_include = values.get("include_tags") is not None
            has_exclude = values.get("exclude_tags") is not None
            if not (has_tools or has_include or has_exclude):
                raise ValueError(
                    "At least one of 'tools', 'include_tags', or 'exclude_tags' is required"
                )
        return values

    def _to_server_and_underlying_transport(
        self,
        server_name: str | None = None,
        client_name: str | None = None,
    ) -> tuple[Any, ClientTransport]:
        """Turn the transforming server into a FastMCP proxy and return its transport."""
        try:
            from fastmcp import Client
            from fastmcp.server import create_proxy
            from fastmcp.server.transforms import ToolTransform
        except ImportError as exc:
            raise ImportError(
                "MCP configs that use FastMCP-specific tool transforms or tag filters "
                "require the full `fastmcp` package. Install it with `pip install fastmcp`."
            ) from exc

        transport = cast("ClientTransport", super().to_transport())  # ty: ignore[unresolved-attribute]
        client = Client(transport=transport, name=client_name)
        wrapped_mcp_server = create_proxy(client, name=server_name)

        if self.include_tags is not None:
            wrapped_mcp_server.enable(tags=self.include_tags, only=True)
        if self.exclude_tags is not None:
            wrapped_mcp_server.disable(tags=self.exclude_tags)
        if self.tools:
            wrapped_mcp_server.add_transform(
                ToolTransform(_coerce_tool_transform_configs(self.tools))
            )

        return wrapped_mcp_server, transport

    def to_transport(self) -> ClientTransport:
        """Get the transport for the transforming MCP server."""
        try:
            from fastmcp.client.transports import FastMCPTransport
        except ImportError as exc:
            raise ImportError(
                "MCP configs that use FastMCP-specific tool transforms or tag filters "
                "require the full `fastmcp` package. Install it with `pip install fastmcp`."
            ) from exc

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
    keep_alive: bool | None = (
        None  # Whether to keep the subprocess alive between connections
    )

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
            keep_alive=self.keep_alive,
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
        from fastmcp.client.transports import (
            SSETransport,
            StreamableHttpTransport,
        )

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
            return cls.model_validate_json(content)  # ty: ignore[possibly-unresolved-reference]

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
