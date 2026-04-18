"""FastMCP plugin primitive.

Plugins are reusable, configurable units that contribute middleware,
transforms, providers, and custom HTTP routes to a FastMCP server. See
the design document for the full specification.

Only the two user-facing primitives are re-exported here: :class:`Plugin`
(subclass to define a plugin) and :class:`PluginMeta` (the metadata model
plugins instantiate). Error classes live in :mod:`fastmcp.server.plugins.base`
and can be imported from there if needed.
"""

from fastmcp.server.plugins.base import Plugin, PluginMeta

__all__ = ["Plugin", "PluginMeta"]
