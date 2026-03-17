"""Transform that exposes prompts as tools.

This transform generates tools for listing and getting prompts, enabling
clients that only support tools to access prompt functionality.

The generated tools route through `ctx.fastmcp` at runtime, so all server
middleware (auth, visibility, rate limiting, etc.) applies to prompt
operations exactly as it would for direct `prompts/get` calls.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.transforms import PromptsAsTools

    mcp = FastMCP("Server")
    mcp.add_transform(PromptsAsTools(mcp))
    # Now has list_prompts and get_prompt tools
    ```
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


class PromptsAsTools(Transform):
    """Transform that adds tools for listing and getting prompts.

    Generates two tools:
    - `list_prompts`: Lists all prompts
    - `get_prompt`: Gets a specific prompt with optional arguments

    The generated tools route through the server at runtime, so auth,
    middleware, and visibility apply automatically.

    This transform should be applied to a FastMCP server instance, not
    a raw Provider, because the generated tools need the server's
    middleware chain for auth and visibility filtering.

    Example:
        ```python
        mcp = FastMCP("Server")
        mcp.add_transform(PromptsAsTools(mcp))
        # Now has list_prompts and get_prompt tools
        ```
    """

    def __init__(self, provider: Provider) -> None:
        from fastmcp.server.server import FastMCP

        if not isinstance(provider, FastMCP):
            raise TypeError(
                "PromptsAsTools requires a FastMCP server instance, not a"
                f" {type(provider).__name__}. The generated tools route through"
                " the server's middleware chain at runtime for auth and"
                " visibility. Pass your FastMCP server: PromptsAsTools(mcp)"
            )
        self._provider = provider

    def __repr__(self) -> str:
        return f"PromptsAsTools({self._provider!r})"

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        """Add prompt tools to the tool list."""
        return [
            *tools,
            self._make_list_prompts_tool(),
            self._make_get_prompt_tool(),
        ]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        """Get a tool by name, including generated prompt tools."""
        if name == "list_prompts":
            return self._make_list_prompts_tool()
        if name == "get_prompt":
            return self._make_get_prompt_tool()
        return await call_next(name, version=version)

    def _make_list_prompts_tool(self) -> Tool:
        """Create the list_prompts tool."""

        async def list_prompts() -> str:
            """List all available prompts.

            Returns JSON with prompt metadata including name, description,
            and optional arguments.
            """
            ctx = get_context()
            prompts = await ctx.fastmcp.list_prompts()

            result: list[dict[str, Any]] = []
            for p in prompts:
                result.append(
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
        """Create the get_prompt tool."""

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
