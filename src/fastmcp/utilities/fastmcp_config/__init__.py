"""FastMCP Configuration module.

This module provides versioned configuration support for FastMCP servers.
The current version is v1, which is re-exported here for convenience.
"""

from fastmcp.utilities.fastmcp_config.v1.fastmcp_config import (
    DeploymentConfig,
    EntrypointConfig,
    EnvironmentConfig,
    FastMCPConfig,
    generate_schema,
)

__all__ = [
    "FastMCPConfig",
    "EntrypointConfig",
    "EnvironmentConfig",
    "DeploymentConfig",
    "generate_schema",
]
