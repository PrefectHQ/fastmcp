"""Deprecation shim — code mode moved to `fastmcp.server.plugins.code_mode`.

The preferred API is now the `CodeMode` plugin:

    from fastmcp import FastMCP
    from fastmcp.server.plugins.code_mode import CodeMode

    mcp = FastMCP("Server", plugins=[CodeMode()])

Note the behavioral change: the old `CodeMode` was a transform added
via `mcp.add_transform(CodeMode(...))`. The new `CodeMode` is a plugin
and the original transform has been renamed to `CodeModeTransform` for
callers that composed it directly.

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
from fastmcp.server.plugins.code_mode.plugin import CodeMode, CodeModeConfig
from fastmcp.server.plugins.code_mode.sandbox import (
    MontySandboxProvider,
    SandboxProvider,
)
from fastmcp.server.plugins.code_mode.transform import CodeModeTransform

warnings.warn(
    "fastmcp.experimental.transforms.code_mode has moved to "
    "fastmcp.server.plugins.code_mode. `CodeMode` is now a plugin — pass "
    "it via `plugins=[CodeMode(...)]` instead of `add_transform(...)`. "
    "Callers that composed the transform directly should import "
    "`CodeModeTransform` from the new location. The old import path "
    "will be removed in a future release.",
    FastMCPDeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "CodeMode",
    "CodeModeConfig",
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
