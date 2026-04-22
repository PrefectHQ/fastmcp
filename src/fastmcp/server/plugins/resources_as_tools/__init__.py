"""ResourcesAsTools plugin — expose MCP resources as callable tools.

    from fastmcp import FastMCP
    from fastmcp.server.plugins.resources_as_tools import ResourcesAsTools

    mcp = FastMCP("Server", plugins=[ResourcesAsTools()])

The low-level `ResourcesAsToolsTransform` lives in `.transform` for
advanced users who want to compose it directly with other transforms.
"""

from fastmcp.server.plugins.resources_as_tools.plugin import (
    ResourcesAsTools,
    ResourcesAsToolsConfig,
)

__all__ = ["ResourcesAsTools", "ResourcesAsToolsConfig"]
