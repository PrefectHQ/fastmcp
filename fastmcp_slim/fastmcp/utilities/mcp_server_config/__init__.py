"""FastMCP Configuration module.

This module provides versioned configuration support for FastMCP servers.
The current version is v1, which is re-exported here for convenience.
"""

from fastmcp.utilities.mcp_server_config.v1.environments.base import Environment
from fastmcp.utilities.mcp_server_config.v1.environments.uv import UVEnvironment
from fastmcp.utilities.mcp_server_config.v1.mcp_server_config import (
    Deployment,
    MCPServerConfig,
    generate_schema,
)
from fastmcp.utilities.mcp_server_config.v1.sources.base import Source
from fastmcp.utilities.mcp_server_config.v1.sources.filesystem import FileSystemSource

__all__ = [
    "Deployment",
    "Environment",
    "FileSystemSource",
    "MCPServerConfig",
    "Source",
    "UVEnvironment",
    "generate_schema",
]
