"""PromptsAsTools plugin: expose MCP prompts as callable tools."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from fastmcp.server.plugins.base import Plugin
from fastmcp.server.plugins.prompts_as_tools.transform import PromptsAsToolsTransform
from fastmcp.server.transforms import Transform


class PromptsAsToolsConfig(BaseModel):
    """Config model for the `PromptsAsTools` plugin.

    Currently empty — included so plugin configs loaded from JSON/YAML
    can still reference this plugin by name, and so future
    per-deployment tool-name overrides have somewhere to land.
    """

    model_config = ConfigDict(extra="forbid")


class PromptsAsTools(Plugin[PromptsAsToolsConfig]):
    """Append `list_prompts` and `get_prompt` synthetic tools to the catalog.

    For clients that only speak the tools protocol, this plugin exposes
    prompt discovery and rendering as regular tool calls. The generated
    tools route through `ctx.fastmcp` at request time, so middleware,
    auth, and visibility apply automatically.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.plugins.prompts_as_tools import PromptsAsTools

        mcp = FastMCP("Server", plugins=[PromptsAsTools()])
        ```
    """

    def transforms(self) -> list[Transform]:
        return [PromptsAsToolsTransform()]
