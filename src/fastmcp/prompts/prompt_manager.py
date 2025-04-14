"""Prompt management functionality."""

import copy
from collections.abc import Awaitable, Callable
from typing import Any

from fastmcp.exceptions import PromptError
from fastmcp.prompts.prompt import Message, Prompt, PromptResult
from fastmcp.settings import DuplicateBehavior
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class PromptManager:
    """Manages FastMCP prompts."""

    def __init__(self, duplicate_behavior: DuplicateBehavior = DuplicateBehavior.WARN):
        self._prompts: dict[str, Prompt] = {}
        self.duplicate_behavior = duplicate_behavior

    def get_prompt(self, name: str) -> Prompt | None:
        """Get prompt by name."""
        return self._prompts.get(name)

    def list_prompts(self) -> list[Prompt]:
        """List all registered prompts."""
        return list(self._prompts.values())

    def add_prompt_from_fn(
        self,
        fn: Callable[..., PromptResult | Awaitable[PromptResult]],
        name: str | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
    ) -> Prompt:
        """Create a prompt from a function."""
        prompt = Prompt.from_function(fn, name=name, description=description, tags=tags)
        return self.add_prompt(prompt)

    def add_prompt(self, prompt: Prompt) -> Prompt:
        """Add a prompt to the manager."""

        # Check for duplicates
        existing = self._prompts.get(prompt.name)
        if existing:
            if self.duplicate_behavior == DuplicateBehavior.WARN:
                logger.warning(f"Prompt already exists: {prompt.name}")
                self._prompts[prompt.name] = prompt
            elif self.duplicate_behavior == DuplicateBehavior.REPLACE:
                self._prompts[prompt.name] = prompt
            elif self.duplicate_behavior == DuplicateBehavior.ERROR:
                raise ValueError(f"Prompt already exists: {prompt.name}")
            elif self.duplicate_behavior == DuplicateBehavior.IGNORE:
                pass

        self._prompts[prompt.name] = prompt
        return prompt

    async def render_prompt(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> list[Message]:
        """Render a prompt by name with arguments."""
        prompt = self.get_prompt(name)
        if not prompt:
            raise PromptError(f"Unknown prompt: {name}")

        return await prompt.render(arguments)

    def import_prompts(
        self, manager: "PromptManager", prefix: str | None = None
    ) -> None:
        """
        Import all prompts from another PromptManager with prefixed names.

        Args:
            manager: Another PromptManager instance to import prompts from
            prefix: Prefix to add to prompt names. The resulting prompt name will
                   be in the format "{prefix}{original_name}" if prefix is provided,
                   otherwise the original name is used.
                   For example, with prefix "weather/" and prompt "forecast_prompt",
                   the imported prompt would be available as "weather/forecast_prompt"
        """
        for name, prompt in manager._prompts.items():
            # Create prefixed name
            prefixed_name = f"{prefix}{name}" if prefix else name

            new_prompt = copy.copy(prompt)
            new_prompt.name = prefixed_name

            # Store the prompt with the prefixed name
            self.add_prompt(new_prompt)
            logger.debug(f'Imported prompt "{name}" as "{prefixed_name}"')
