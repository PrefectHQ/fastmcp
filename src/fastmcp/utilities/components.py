from collections.abc import Sequence
from typing import Annotated, TypeVar

from pydantic import BeforeValidator, Field

from fastmcp.utilities.types import FastMCPBaseModel

T = TypeVar("T")


def _convert_set_default_none(maybe_set: set[T] | Sequence[T] | None) -> set[T]:
    """Convert a sequence to a set, defaulting to an empty set if None."""
    if maybe_set is None:
        return set()
    if isinstance(maybe_set, set):
        return maybe_set
    return set(maybe_set)


class FastMCPComponent(FastMCPBaseModel):
    """Base class for FastMCP tools, prompts, resources, and resource templates."""

    name: str = Field(
        description="The name of the component.",
    )
    description: str | None = Field(
        default=None,
        description="The description of the component.",
    )
    tags: Annotated[set[str], BeforeValidator(_convert_set_default_none)] = Field(
        default_factory=set,
        description="Tags for the component.",
    )

    enabled: bool = Field(
        default=True,
        description="Whether the component is enabled.",
    )

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return False
        assert isinstance(other, type(self))
        return self.model_dump() == other.model_dump()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, description={self.description!r}, tags={self.tags}, enabled={self.enabled})"

    def enable(self) -> None:
        """Enable the component."""
        self.enabled = True

    def disable(self) -> None:
        """Disable the component."""
        self.enabled = False
