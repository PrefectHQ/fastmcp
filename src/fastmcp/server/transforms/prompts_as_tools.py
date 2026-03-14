"""Transform that exposes prompts as tools.

This transform generates tools for listing and getting prompts, enabling
clients that only support tools to access prompt functionality.

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

from fastmcp.exceptions import AuthorizationError
from fastmcp.server.auth import AuthContext, run_auth_checks
from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.server.transforms.visibility import (
    apply_session_transforms,
    is_enabled,
)
from fastmcp.tools.tool import Tool
from fastmcp.utilities.versions import VersionSpec

if TYPE_CHECKING:
    from fastmcp.server.providers.base import Provider


class PromptsAsTools(Transform):
    """Transform that adds tools for listing and getting prompts.

    Generates two tools:
    - `list_prompts`: Lists all prompts from the provider
    - `get_prompt`: Gets a specific prompt with optional arguments

    The transform captures a provider reference at construction and queries it
    for prompts when the generated tools are called. Auth and visibility
    filtering is applied regardless of provider type.

    Example:
        ```python
        mcp = FastMCP("Server")
        mcp.add_transform(PromptsAsTools(mcp))
        # Now has list_prompts and get_prompt tools
        ```
    """

    def __init__(self, provider: Provider) -> None:
        """Initialize the transform with a provider reference.

        Args:
            provider: The provider to query for prompts. Typically this is
                the same FastMCP server the transform is added to.
        """
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
        # Check if it's one of our generated tools
        if name == "list_prompts":
            return self._make_list_prompts_tool()
        if name == "get_prompt":
            return self._make_get_prompt_tool()

        # Otherwise delegate to downstream
        return await call_next(name, version=version)

    def _make_list_prompts_tool(self) -> Tool:
        """Create the list_prompts tool."""
        provider = self._provider

        async def list_prompts() -> str:
            """List all available prompts.

            Returns JSON with prompt metadata including name, description,
            and optional arguments.
            """
            from fastmcp.server.server import FastMCP

            if isinstance(provider, FastMCP):
                all_prompts = await provider.list_prompts()
            else:
                all_prompts = list(
                    await _filter_authorized(
                        await apply_session_transforms(
                            [p for p in await provider.list_prompts() if is_enabled(p)]
                        )
                    )
                )

            result: list[dict[str, Any]] = []
            for p in all_prompts:
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
        provider = self._provider

        async def get_prompt(
            name: Annotated[str, "The name of the prompt to get"],
            arguments: Annotated[
                dict[str, Any] | None,
                "Optional arguments for the prompt",
            ] = None,
        ) -> str:
            """Get a prompt by name with optional arguments.

            Returns the rendered prompt as JSON with a messages array.
            Arguments should be provided as a dict mapping argument names to values.
            """
            from fastmcp.server.server import FastMCP

            # Use FastMCP.render_prompt() if available - runs middleware chain
            if isinstance(provider, FastMCP):
                result = await provider.render_prompt(name, arguments=arguments or {})
                return _format_prompt_result(result)

            # Plain providers: apply visibility and auth checks
            prompt = await provider.get_prompt(name)
            if prompt is None:
                raise ValueError(f"Prompt not found: {name}")

            await _check_component_access(prompt)

            result = await prompt._render(arguments or {})
            return _format_prompt_result(result)

        return Tool.from_function(fn=get_prompt)


async def _check_component_access(component: Any) -> None:
    """Check visibility and auth for a single component.

    Applies session transforms, then checks is_enabled and runs auth checks.
    Raises ValueError if the component is disabled or unauthorized.
    """
    from fastmcp.server.dependencies import get_access_token

    marked = await apply_session_transforms([component])
    if not marked or not is_enabled(marked[0]):
        raise ValueError(f"Prompt not found: {component.name}")

    if component.auth is not None:
        token = get_access_token()
        ctx = AuthContext(token=token, component=component)
        try:
            if not await run_auth_checks(component.auth, ctx):
                raise ValueError(f"Prompt not found: {component.name}")
        except AuthorizationError:
            raise ValueError(f"Prompt not found: {component.name}") from None


async def _filter_authorized(components: Sequence[Any]) -> Sequence[Any]:
    """Filter components by visibility and auth.

    Returns only components that are enabled and pass auth checks.
    """
    from fastmcp.server.dependencies import get_access_token

    enabled = [c for c in components if is_enabled(c)]
    token = get_access_token()
    authorized: list[Any] = []
    for component in enabled:
        if component.auth is not None:
            ctx = AuthContext(token=token, component=component)
            try:
                if not await run_auth_checks(component.auth, ctx):
                    continue
            except AuthorizationError:
                continue
        authorized.append(component)
    return authorized


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
            # Preserve structured content (e.g., EmbeddedResource) as dict
            content = msg.content.model_dump(mode="json", exclude_none=True)

        messages.append(
            {
                "role": msg.role,
                "content": content,
            }
        )

    return json.dumps({"messages": messages}, indent=2)
