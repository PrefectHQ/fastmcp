from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, Field


class Environment(BaseModel, ABC):
    """Base class for environment configuration."""

    type: str = Field(description="Environment type identifier")

    @abstractmethod
    def build_command(self, command: list[str]) -> list[str]:
        """Build the full command with environment setup.

        Args:
            command: Base command to wrap with environment setup

        Returns:
            Full command ready for subprocess execution
        """
        pass

    async def prepare(self, output_dir: Path | None = None) -> None:
        """Prepare the environment (optional, can be no-op).

        Args:
            output_dir: Directory for persistent environment setup
        """
        pass  # Default no-op implementation
