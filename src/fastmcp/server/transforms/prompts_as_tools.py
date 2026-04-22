"""Deprecation shim — prompts-as-tools moved to `fastmcp.server.plugins.prompts_as_tools`.

The preferred API is now the `PromptsAsTools` plugin:

    from fastmcp import FastMCP
    from fastmcp.server.plugins.prompts_as_tools import PromptsAsTools

    mcp = FastMCP("Server", plugins=[PromptsAsTools()])

For backcompat, this module keeps `PromptsAsTools` bound to the
**transform** class (so existing `mcp.add_transform(PromptsAsTools(mcp))`
code keeps working). The transform is also exported under its new
canonical name, `PromptsAsToolsTransform`.

This path issues a `FastMCPDeprecationWarning` on import — a
`DeprecationWarning` subclass that fastmcp enables by default (plain
`DeprecationWarning` is suppressed by CPython's default filter, so
users wouldn't see the notice).
"""

import warnings

from fastmcp.exceptions import FastMCPDeprecationWarning
from fastmcp.server.plugins.prompts_as_tools.transform import PromptsAsToolsTransform

# `PromptsAsTools` at this old path stays bound to the transform class,
# so `mcp.add_transform(PromptsAsTools(mcp))` keeps working. The new
# plugin class is at `fastmcp.server.plugins.prompts_as_tools.PromptsAsTools`.
PromptsAsTools = PromptsAsToolsTransform

warnings.warn(
    "fastmcp.server.transforms.prompts_as_tools has moved to "
    "fastmcp.server.plugins.prompts_as_tools. Prefer the PromptsAsTools "
    "plugin: `from fastmcp.server.plugins.prompts_as_tools import "
    "PromptsAsTools` and pass it via `plugins=[PromptsAsTools()]`. At "
    "this old path, `PromptsAsTools` remains the transform class (also "
    "exported as `PromptsAsToolsTransform`) for backcompat. The old "
    "import path will be removed in a future release.",
    FastMCPDeprecationWarning,
    stacklevel=2,
)

__all__ = ["PromptsAsTools", "PromptsAsToolsTransform"]
