"""FastMCP Configuration module.

This module provides versioned configuration support for FastMCP servers.
The current version is v1, which is re-exported here for convenience.
"""

from fastmcp.utilities.fastmcp_config.v1.fastmcp_config import (
    Deployment,
    Environment,
    FastMCPConfig,
    generate_schema,
)
from fastmcp.utilities.fastmcp_config.v1.sources.base import BaseSource
from fastmcp.utilities.fastmcp_config.v1.sources.filesystem import FileSystemSource

__all__ = [
    "BaseSource",
    "Deployment",
    "Environment",
    "FastMCPConfig",
    "FileSystemSource",
    "generate_schema",
]
