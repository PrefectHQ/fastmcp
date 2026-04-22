"""Deprecation shim — resources-as-tools moved to `fastmcp.server.plugins.resources_as_tools`.

The preferred API is now the `ResourcesAsTools` plugin:

    from fastmcp import FastMCP
    from fastmcp.server.plugins.resources_as_tools import ResourcesAsTools

    mcp = FastMCP("Server", plugins=[ResourcesAsTools()])

For backcompat, this module keeps `ResourcesAsTools` bound to the
**transform** class (so existing `mcp.add_transform(ResourcesAsTools(mcp))`
code keeps working). The transform is also exported under its new
canonical name, `ResourcesAsToolsTransform`.

This path issues a `FastMCPDeprecationWarning` on import — a
`DeprecationWarning` subclass that fastmcp enables by default (plain
`DeprecationWarning` is suppressed by CPython's default filter, so
users wouldn't see the notice).
"""

import warnings

from fastmcp.exceptions import FastMCPDeprecationWarning
from fastmcp.server.plugins.resources_as_tools.transform import (
    ResourcesAsToolsTransform,
)

# `ResourcesAsTools` at this old path stays bound to the transform class,
# so `mcp.add_transform(ResourcesAsTools(mcp))` keeps working. The new
# plugin class is at `fastmcp.server.plugins.resources_as_tools.ResourcesAsTools`.
ResourcesAsTools = ResourcesAsToolsTransform

warnings.warn(
    "fastmcp.server.transforms.resources_as_tools has moved to "
    "fastmcp.server.plugins.resources_as_tools. Prefer the "
    "ResourcesAsTools plugin: `from fastmcp.server.plugins.resources_as_tools "
    "import ResourcesAsTools` and pass it via `plugins=[ResourcesAsTools()]`. "
    "At this old path, `ResourcesAsTools` remains the transform class "
    "(also exported as `ResourcesAsToolsTransform`) for backcompat. The "
    "old import path will be removed in a future release.",
    FastMCPDeprecationWarning,
    stacklevel=2,
)

__all__ = ["ResourcesAsTools", "ResourcesAsToolsTransform"]
