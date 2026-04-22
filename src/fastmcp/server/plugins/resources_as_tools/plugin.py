"""ResourcesAsTools plugin: expose MCP resources as callable tools."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from fastmcp.server.plugins.base import Plugin
from fastmcp.server.plugins.resources_as_tools.transform import (
    ResourcesAsToolsTransform,
)
from fastmcp.server.transforms import Transform


class ResourcesAsToolsConfig(BaseModel):
    """Config model for the `ResourcesAsTools` plugin.

    Currently empty — included so plugin configs loaded from JSON/YAML
    can still reference this plugin by name, and so future
    per-deployment tool-name overrides have somewhere to land.
    """

    model_config = ConfigDict(extra="forbid")


class ResourcesAsTools(Plugin[ResourcesAsToolsConfig]):
    """Append `list_resources` and `read_resource` synthetic tools to the catalog.

    For clients that only speak the tools protocol, this plugin exposes
    resource discovery and reads as regular tool calls. The generated
    tools route through `ctx.fastmcp` at request time, so middleware,
    auth, and visibility apply automatically.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.plugins.resources_as_tools import ResourcesAsTools

        mcp = FastMCP("Server", plugins=[ResourcesAsTools()])
        ```
    """

    def transforms(self) -> list[Transform]:
        return [ResourcesAsToolsTransform()]
