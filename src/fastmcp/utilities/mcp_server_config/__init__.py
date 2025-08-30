"""FastMCP Configuration module.

This module provides versioned configuration support for FastMCP servers.
The current version is v1, which is re-exported here for convenience.
"""

from fastmcp.utilities.mcp_server_config.v1.mcp_server_config import (
    Deployment,
    Environment,
    MCPServerConfig,
    generate_schema,
)
from fastmcp.utilities.mcp_server_config.v1.sources.base import BaseSource
from fastmcp.utilities.mcp_server_config.v1.sources.filesystem import FileSystemSource

__all__ = [
    "BaseSource",
    "Deployment",
    "Environment",
    "MCPServerConfig",
    "FileSystemSource",
    "generate_schema",
]
