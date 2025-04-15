"""Prompt management functionality."""

from collections.abc import Awaitable, Callable
from typing import Any

from fastmcp.exceptions import PromptError
from fastmcp.prompts.prompt import MCPPrompt, Message, Prompt, PromptResult
from fastmcp.settings import DuplicateBehavior
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class PromptManager:
    """Manages FastMCP prompts."""

    def __init__(self, duplicate_behavior: DuplicateBehavior | None = None):
        self._prompts: dict[str, Prompt] = {}

        # Default to "warn" if None is provided
        if duplicate_behavior is None:
            duplicate_behavior = "warn"

        if duplicate_behavior not in DuplicateBehavior.__args__:
            raise ValueError(
                f"Invalid duplicate_behavior: {duplicate_behavior}. "
                f"Must be one of: {', '.join(DuplicateBehavior.__args__)}"
            )

        self.duplicate_behavior = duplicate_behavior

    def get_prompt(self, key: str) -> Prompt | None:
        """Get prompt by key."""
        return self._prompts.get(key)

    def get_prompts(self) -> dict[str, Prompt]:
        """Get all registered prompts, indexed by registered key."""
        return self._prompts

    def list_prompts(self) -> list[Prompt]:
        """List all registered prompts."""
        return list(self.get_prompts().values())

    def list_mcp_prompts(self) -> list[MCPPrompt]:
        """List all registered prompts in the format expected by the low-level MCP server."""
        return [
            prompt.to_mcp_prompt(name=key) for key, prompt in self.get_prompts().items()
        ]

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

    def add_prompt(self, prompt: Prompt, key: str | None = None) -> Prompt:
        """Add a prompt to the manager."""
        key = key or prompt.name

        # Check for duplicates
        existing = self._prompts.get(key)
        if existing:
            if self.duplicate_behavior == "warn":
                logger.warning(f"Prompt already exists: {key}")
                self._prompts[key] = prompt
            elif self.duplicate_behavior == "replace":
                self._prompts[key] = prompt
            elif self.duplicate_behavior == "error":
                raise ValueError(f"Prompt already exists: {key}")
            elif self.duplicate_behavior == "ignore":
                return existing
        else:
            self._prompts[key] = prompt
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
            prefix: Prefix to add to prompt names. The resulting prompt key will
                   be in the format "{prefix}{original_name}" if prefix is provided,
                   otherwise the original name is used.
                   For example, with prefix "weather/" and prompt "forecast_prompt",
                   the imported prompt would be available as "weather/forecast_prompt"
        """
        for name, prompt in manager._prompts.items():
            # Create prefixed key
            key = f"{prefix}{name}" if prefix else name

            # Store the prompt with the prefixed key
            self.add_prompt(prompt, key=key)
            logger.debug(f'Imported prompt "{prompt.name}" as "{key}"')
