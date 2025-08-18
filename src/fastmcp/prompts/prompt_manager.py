from __future__ import annotations as _annotations

import warnings
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from mcp import GetPromptResult

from fastmcp import settings
from fastmcp.exceptions import NotFoundError, PromptError
from fastmcp.prompts.prompt import FunctionPrompt, Prompt, PromptResult
from fastmcp.settings import DuplicateBehavior
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.server.server import MountedServer

logger = get_logger(__name__)


class PromptManager:
    """Manages FastMCP prompts."""

    def __init__(
        self,
        duplicate_behavior: DuplicateBehavior | None = None,
        mask_error_details: bool | None = None,
    ):
        self._prompts: dict[str, Prompt] = {}
        self._mounted_servers: list[MountedServer] = []
        self.mask_error_details = mask_error_details or settings.mask_error_details

        # Default to "warn" if None is provided
        if duplicate_behavior is None:
            duplicate_behavior = "warn"

        if duplicate_behavior not in DuplicateBehavior.__args__:
            raise ValueError(
                f"Invalid duplicate_behavior: {duplicate_behavior}. "
                f"Must be one of: {', '.join(DuplicateBehavior.__args__)}"
            )

        self.duplicate_behavior = duplicate_behavior

    def mount(self, server: MountedServer) -> None:
        """Adds a mounted server as a source for prompts."""
        self._mounted_servers.append(server)

    async def _load_prompts(self, *, via_server: bool = False) -> dict[str, Prompt]:
        """
        The single, consolidated recursive method for fetching prompts. The 'via_server'
        parameter determines the communication path.

        - via_server=False: Manager-to-manager path for complete, unfiltered inventory
        - via_server=True: Server-to-server path for filtered MCP requests
        """
        all_prompts: dict[str, Prompt] = {}

        for mounted in self._mounted_servers:
            try:
                if via_server:
                    # Use the server-to-server filtered path
                    child_results = await mounted.server._list_prompts()
                else:
                    # Use the manager-to-manager unfiltered path
                    child_results = await mounted.server._prompt_manager.list_prompts()

                # The combination logic is the same for both paths
                child_dict = {p.key: p for p in child_results}
                if mounted.prefix:
                    for prompt in child_dict.values():
                        prefixed_prompt = prompt.model_copy(
                            key=f"{mounted.prefix}_{prompt.key}"
                        )
                        all_prompts[prefixed_prompt.key] = prefixed_prompt
                else:
                    all_prompts.update(child_dict)
            except Exception as e:
                # Skip failed mounts silently, matches existing behavior
                logger.warning(
                    f"Failed to get prompts from server: {mounted.server.name!r}, mounted at: {mounted.prefix!r}: {e}"
                )
                if settings.mounted_components_raise_on_load_error:
                    raise
                continue

        # Finally, add local prompts, which always take precedence
        all_prompts.update(self._prompts)
        return all_prompts

    async def has_prompt(self, key: str) -> bool:
        """Check if a prompt exists."""
        prompts = await self.get_prompts()
        return key in prompts

    async def get_prompt(self, key: str) -> Prompt:
        """Get prompt by key."""
        prompts = await self.get_prompts()
        if key in prompts:
            return prompts[key]
        raise NotFoundError(f"Unknown prompt: {key}")

    async def get_prompts(self) -> dict[str, Prompt]:
        """
        Gets the complete, unfiltered inventory of all prompts.
        """
        return await self._load_prompts(via_server=False)

    async def list_prompts(self) -> list[Prompt]:
        """
        Lists all prompts, applying protocol filtering.
        """
        prompts_dict = await self._load_prompts(via_server=True)
        return list(prompts_dict.values())

    def add_prompt_from_fn(
        self,
        fn: Callable[..., PromptResult | Awaitable[PromptResult]],
        name: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
    ) -> FunctionPrompt:
        """Create a prompt from a function."""
        # deprecated in 2.7.0
        if settings.deprecation_warnings:
            warnings.warn(
                "PromptManager.add_prompt_from_fn() is deprecated. Use Prompt.from_function() and call add_prompt() instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        prompt = FunctionPrompt.from_function(
            fn, name=name, description=description, tags=tags
        )
        return self.add_prompt(prompt)  # type: ignore

    def add_prompt(self, prompt: Prompt) -> Prompt:
        """Add a prompt to the manager."""
        # Check for duplicates
        existing = self._prompts.get(prompt.key)
        if existing:
            if self.duplicate_behavior == "warn":
                logger.warning(f"Prompt already exists: {prompt.key}")
                self._prompts[prompt.key] = prompt
            elif self.duplicate_behavior == "replace":
                self._prompts[prompt.key] = prompt
            elif self.duplicate_behavior == "error":
                raise ValueError(f"Prompt already exists: {prompt.key}")
            elif self.duplicate_behavior == "ignore":
                return existing
        else:
            self._prompts[prompt.key] = prompt
        return prompt

    async def render_prompt(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> GetPromptResult:
        """
        Internal API for servers: Finds and renders a prompt, respecting the
        filtered protocol path.
        """
        # 1. Check local prompts first. The server will have already applied its filter.
        if name in self._prompts:
            prompt = await self.get_prompt(name)
            if not prompt:
                raise NotFoundError(f"Unknown prompt: {name}")

            try:
                messages = await prompt.render(arguments)
                return GetPromptResult(
                    description=prompt.description, messages=messages
                )

            # Pass through PromptErrors as-is
            except PromptError as e:
                logger.exception(f"Error rendering prompt {name!r}")
                raise e

            # Handle other exceptions
            except Exception as e:
                logger.exception(f"Error rendering prompt {name!r}")
                if self.mask_error_details:
                    # Mask internal details
                    raise PromptError(f"Error rendering prompt {name!r}") from e
                else:
                    # Include original error details
                    raise PromptError(f"Error rendering prompt {name!r}: {e}") from e

        # 2. Check mounted servers using the filtered protocol path.
        for mounted in reversed(self._mounted_servers):
            prompt_key = name
            if mounted.prefix:
                if name.startswith(f"{mounted.prefix}_"):
                    prompt_key = name.removeprefix(f"{mounted.prefix}_")
                else:
                    continue
            try:
                return await mounted.server._get_prompt(prompt_key, arguments)
            except NotFoundError:
                continue

        raise NotFoundError(f"Unknown prompt: {name}")
