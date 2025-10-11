from __future__ import annotations as _annotations

import warnings
from collections.abc import Awaitable, Callable
from typing import Any

from mcp import GetPromptResult

from fastmcp import settings
from fastmcp.exceptions import NotFoundError, PromptError
from fastmcp.prompts.prompt import FunctionPrompt, Prompt, PromptResult
from fastmcp.settings import DuplicateBehavior
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class PromptManager:
    """Manages FastMCP prompts."""

    def __init__(
        self,
        duplicate_behavior: DuplicateBehavior | None = None,
        mask_error_details: bool | None = None,
    ):
        self._prompts: dict[str, Prompt] = {}
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
        Gets the complete, unfiltered inventory of local prompts.
        """
        return dict(self._prompts)

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
        prompt = await self.get_prompt(name)
        try:
            messages = await prompt.render(arguments)
            return GetPromptResult(description=prompt.description, messages=messages)
        except PromptError as e:
            logger.exception(f"Error rendering prompt {name!r}")
            raise e
        except Exception as e:
            logger.exception(f"Error rendering prompt {name!r}")
            if self.mask_error_details:
                raise PromptError(f"Error rendering prompt {name!r}") from e
            else:
                raise PromptError(f"Error rendering prompt {name!r}: {e}") from e
