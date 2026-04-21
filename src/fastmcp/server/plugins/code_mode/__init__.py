"""Code-mode plugin — discovery + sandboxed Python execution in place of the tool catalog.

The `CodeMode` plugin is the public entry point:

    from fastmcp import FastMCP
    from fastmcp.server.plugins.code_mode import CodeMode

    mcp = FastMCP("Server", plugins=[CodeMode()])

Discovery-tool factories (`Search`, `GetSchemas`, `GetTags`,
`ListTools`) and the sandbox-provider protocol (`SandboxProvider`,
`MontySandboxProvider`) are re-exported for custom composition. The
low-level `CodeModeTransform` lives in `.transform` for advanced users
who want to stack it directly with other transforms.
"""

from fastmcp.server.plugins.code_mode.discovery import (
    DiscoveryToolFactory,
    GetSchemas,
    GetTags,
    GetToolCatalog,
    ListTools,
    Search,
)
from fastmcp.server.plugins.code_mode.plugin import CodeMode, CodeModeConfig
from fastmcp.server.plugins.code_mode.sandbox import (
    MontySandboxProvider,
    SandboxProvider,
)

__all__ = [
    "CodeMode",
    "CodeModeConfig",
    "DiscoveryToolFactory",
    "GetSchemas",
    "GetTags",
    "GetToolCatalog",
    "ListTools",
    "MontySandboxProvider",
    "SandboxProvider",
    "Search",
]
