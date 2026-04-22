"""Low-level transform that powers the PromptsAsTools plugin.

`PromptsAsToolsTransform` appends two synthetic tools — `list_prompts`
and `get_prompt` — to the tool catalog, so clients that only speak the
tools protocol can still drive prompt discovery and rendering. Both
generated tools route through `get_context().fastmcp` at request time,
so middleware, auth, and visibility all apply exactly as they would for
direct `prompts/*` calls.

Most users should configure this through the `PromptsAsTools` plugin
(`fastmcp.server.plugins.prompts_as_tools`). The transform is exposed
for advanced composition and for backcompat with the old
`fastmcp.server.transforms.prompts_as_tools` path.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, Any

from mcp.types import TextContent

from fastmcp.server.dependencies import get_context
from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools.base import Tool
from fastmcp.utilities.versions import VersionSpec

if TYPE_CHECKING:
    from fastmcp.server.providers.base import Provider


class PromptsAsToolsTransform(Transform):
    """Transform that adds `list_prompts` and `get_prompt` synthetic tools.

    The generated tools call back into the server via `ctx.fastmcp` at
    request time, so server middleware (auth, visibility, rate limiting)
    applies automatically.

    The `provider` argument exists purely for intent — if passed, it
    must be a FastMCP server instance (raw providers don't expose an
    `add_transform` path anyway). The plugin wrapper constructs this
    transform without a provider.

    Example:
        ```python
        mcp = FastMCP("Server")
        mcp.add_transform(PromptsAsToolsTransform(mcp))
        ```
    """

    def __init__(self, provider: Provider | None = None) -> None:
        if provider is not None:
            from fastmcp.server.server import FastMCP

            if not isinstance(provider, FastMCP):
                raise TypeError(
                    "PromptsAsToolsTransform accepts a FastMCP server instance, "
                    f"not a {type(provider).__name__}. The generated tools route "
                    "through the server's middleware chain at runtime for auth "
                    "and visibility. Pass your FastMCP server, or omit the "
                    "argument entirely when using the plugin wrapper."
                )
        self._provider = provider

    def __repr__(self) -> str:
        return f"PromptsAsToolsTransform({self._provider!r})"

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [
            *tools,
            self._make_list_prompts_tool(),
            self._make_get_prompt_tool(),
        ]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        if name == "list_prompts":
            return self._make_list_prompts_tool()
        if name == "get_prompt":
            return self._make_get_prompt_tool()
        return await call_next(name, version=version)

    def _make_list_prompts_tool(self) -> Tool:
        async def list_prompts() -> str:
            """List all available prompts.

            Returns JSON with prompt metadata including name, description,
            and optional arguments.
            """
            ctx = get_context()
            prompts = await ctx.fastmcp.list_prompts()

            result: list[dict[str, Any]] = []
            for p in prompts:
                result.append(  # noqa: PERF401
                    {
                        "name": p.name,
                        "description": p.description,
                        "arguments": [
                            {
                                "name": arg.name,
                                "description": arg.description,
                                "required": arg.required,
                            }
                            for arg in (p.arguments or [])
                        ],
                    }
                )

            return json.dumps(result, indent=2)

        return Tool.from_function(fn=list_prompts)

    def _make_get_prompt_tool(self) -> Tool:
        async def get_prompt(
            name: Annotated[str, "The name of the prompt to get"],
            arguments: Annotated[
                dict[str, Any] | None,
                "Optional arguments for the prompt",
            ] = None,
        ) -> str:
            """Get a prompt by name with optional arguments.

            Returns the rendered prompt as JSON with a messages array.
            Arguments should be provided as a dict mapping argument names
            to values.
            """
            ctx = get_context()
            result = await ctx.fastmcp.render_prompt(name, arguments=arguments or {})
            return _format_prompt_result(result)

        return Tool.from_function(fn=get_prompt)


def _format_prompt_result(result: Any) -> str:
    """Format PromptResult for tool output.

    Returns JSON with the messages array. Preserves embedded resources
    as structured JSON objects.
    """
    messages = []
    for msg in result.messages:
        if isinstance(msg.content, TextContent):
            content = msg.content.text
        else:
            content = msg.content.model_dump(mode="json", exclude_none=True)

        messages.append(
            {
                "role": msg.role,
                "content": content,
            }
        )

    return json.dumps({"messages": messages}, indent=2)
