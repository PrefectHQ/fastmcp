"""Deprecation shim — code mode moved to `fastmcp.server.plugins.code_mode`.

The preferred API is now the `CodeMode` plugin:

    from fastmcp import FastMCP
    from fastmcp.server.plugins.code_mode import CodeMode

    mcp = FastMCP("Server", plugins=[CodeMode()])

For backcompat, this module keeps `CodeMode` bound to the **transform**
class (so existing `mcp.add_transform(CodeMode())` code keeps working).
The transform is also exported under its new canonical name,
`CodeModeTransform`. Sandbox providers, discovery-tool factories, and
related helpers re-export from the new location unchanged.

This path issues a `FastMCPDeprecationWarning` on import — a
`DeprecationWarning` subclass that fastmcp enables by default (plain
`DeprecationWarning` is suppressed by CPython's default filter, so
users wouldn't see the notice).
"""

import warnings

from fastmcp.exceptions import FastMCPDeprecationWarning
from fastmcp.server.plugins.code_mode.discovery import (
    DiscoveryToolFactory,
    GetSchemas,
    GetTags,
    GetToolCatalog,
    ListTools,
    Search,
)
from fastmcp.server.plugins.code_mode.sandbox import (
    MontySandboxProvider,
    SandboxProvider,
)
from fastmcp.server.plugins.code_mode.transform import CodeModeTransform

# `CodeMode` at this old path stays bound to the transform class, so
# `mcp.add_transform(CodeMode(...))` keeps working. The new plugin class
# is at `fastmcp.server.plugins.code_mode.CodeMode`.
CodeMode = CodeModeTransform

warnings.warn(
    "fastmcp.experimental.transforms.code_mode has moved to "
    "fastmcp.server.plugins.code_mode. Prefer the CodeMode plugin: "
    "`from fastmcp.server.plugins.code_mode import CodeMode` and pass "
    "it via `plugins=[CodeMode(...)]`. At this old path, `CodeMode` "
    "remains the transform class (also exported as `CodeModeTransform`) "
    "for backcompat. The old import path will be removed in a future "
    "release.",
    FastMCPDeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "CodeMode",
    "CodeModeTransform",
    "DiscoveryToolFactory",
    "GetSchemas",
    "GetTags",
    "GetToolCatalog",
    "ListTools",
    "MontySandboxProvider",
    "SandboxProvider",
    "Search",
]
