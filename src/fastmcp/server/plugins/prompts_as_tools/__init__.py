"""PromptsAsTools plugin — expose MCP prompts as callable tools.

    from fastmcp import FastMCP
    from fastmcp.server.plugins.prompts_as_tools import PromptsAsTools

    mcp = FastMCP("Server", plugins=[PromptsAsTools()])

The low-level `PromptsAsToolsTransform` lives in `.transform` for
advanced users who want to compose it directly with other transforms.
"""

from fastmcp.server.plugins.prompts_as_tools.plugin import (
    PromptsAsTools,
    PromptsAsToolsConfig,
)

__all__ = ["PromptsAsTools", "PromptsAsToolsConfig"]
