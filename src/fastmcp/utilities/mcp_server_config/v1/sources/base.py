from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class Source(BaseModel, ABC):
    """Abstract base class for all source types."""

    type: str = Field(description="Source type identifier")

    async def prepare(self) -> None:
        """Prepare the source (download, clone, install, etc).

        For sources that need preparation (e.g., git clone, download),
        this method performs that preparation. For sources that don't
        need preparation (e.g., local files), this is a no-op.
        """
        # Default implementation for sources that don't need preparation
        pass

    @abstractmethod
    async def load_server(self) -> Any:
        """Load and return the FastMCP server instance.

        Must be called after prepare() if the source requires preparation.
        All information needed to load the server should be available
        as attributes on the source instance.
        """
        ...
