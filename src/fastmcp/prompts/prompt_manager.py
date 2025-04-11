"""Prompt management functionality."""

from typing import Any

from fastmcp.prompts.base import Message, Prompt
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class PromptManager:
    """Manages FastMCP prompts."""

    def __init__(self, warn_on_duplicate_prompts: bool = True):
        self._prompts: dict[str, Prompt] = {}
        self.warn_on_duplicate_prompts = warn_on_duplicate_prompts

    def get_prompt(self, name: str) -> Prompt | None:
        """Get prompt by name."""
        return self._prompts.get(name)

    def list_prompts(self) -> list[Prompt]:
        """List all registered prompts."""
        return list(self._prompts.values())

    def add_prompt(
        self,
        prompt: Prompt,
    ) -> Prompt:
        """Add a prompt to the manager."""

        # Check for duplicates
        existing = self._prompts.get(prompt.name)
        if existing:
            if self.warn_on_duplicate_prompts:
                logger.warning(f"Prompt already exists: {prompt.name}")
            return existing

        self._prompts[prompt.name] = prompt
        return prompt

    async def render_prompt(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> list[Message]:
        """Render a prompt by name with arguments."""
        prompt = self.get_prompt(name)
        if not prompt:
            raise ValueError(f"Unknown prompt: {name}")

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
            # Create prefixed name - we keep the original name in the Prompt object
            prefixed_name = f"{prefix}{name}" if prefix else name

            # Log the import
            logger.debug(f"Importing prompt with name {name} as {prefixed_name}")

            # Store the prompt with the prefixed name
            self._prompts[prefixed_name] = prompt
