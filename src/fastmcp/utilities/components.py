from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any, TypedDict, cast

from mcp.types import Icon
from pydantic import BeforeValidator, Field, PrivateAttr
from typing_extensions import Self, TypeVar

import fastmcp
from fastmcp.utilities.types import FastMCPBaseModel

T = TypeVar("T", default=Any)


class FastMCPMeta(TypedDict, total=False):
    tags: list[str]


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
    title: str | None = Field(
        default=None,
        description="The title of the component for display purposes.",
    )
    description: str | None = Field(
        default=None,
        description="The description of the component.",
    )
    icons: list[Icon] | None = Field(
        default=None,
        description="Optional list of icons for this component to display in user interfaces.",
    )
    tags: Annotated[set[str], BeforeValidator(_convert_set_default_none)] = Field(
        default_factory=set,
        description="Tags for the component.",
    )
    meta: dict[str, Any] | None = Field(
        default=None, description="Meta information about the component"
    )
    enabled: bool = Field(
        default=True,
        description="Whether the component is enabled.",
    )

    _key: str | None = PrivateAttr()

    def __init__(self, *, key: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._key = key

    @property
    def key(self) -> str:
        """
        The key of the component. This is used for internal bookkeeping
        and may reflect e.g. prefixes or other identifiers. You should not depend on
        keys having a certain value, as the same tool loaded from different
        hierarchies of servers may have different keys.
        """
        return self._key or self.name

    def get_meta(
        self, include_fastmcp_meta: bool | None = None
    ) -> dict[str, Any] | None:
        """
        Get the meta information about the component.

        If include_fastmcp_meta is True, a `_fastmcp` key will be added to the
        meta, containing a `tags` field with the tags of the component.
        """

        if include_fastmcp_meta is None:
            include_fastmcp_meta = fastmcp.settings.include_fastmcp_meta

        meta = self.meta or {}

        if include_fastmcp_meta:
            fastmcp_meta = FastMCPMeta(tags=sorted(self.tags))
            # overwrite any existing _fastmcp meta with keys from the new one
            if upstream_meta := meta.get("_fastmcp"):
                fastmcp_meta = upstream_meta | fastmcp_meta
            meta["_fastmcp"] = fastmcp_meta

        return meta or None

    def model_copy(  # type: ignore[override]
        self,
        *,
        update: dict[str, Any] | None = None,
        deep: bool = False,
        key: str | None = None,
    ) -> Self:
        """
        Create a copy of the component.

        Args:
            update: A dictionary of fields to update.
            deep: Whether to deep copy the component.
            key: The key to use for the copy.
        """
        # `model_copy` has an `update` parameter but it doesn't work for certain private attributes
        # https://github.com/pydantic/pydantic/issues/12116
        # So we manually set the private attribute here instead, such as _key
        copy = super().model_copy(update=update, deep=deep)
        if key is not None:
            copy._key = key
        return cast(Self, copy)

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return False
        if not isinstance(other, type(self)):
            return False
        return self.model_dump() == other.model_dump()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, title={self.title!r}, description={self.description!r}, tags={self.tags}, enabled={self.enabled})"

    def enable(self) -> None:
        """Enable the component."""
        self.enabled = True

    def disable(self) -> None:
        """Disable the component."""
        self.enabled = False

    def copy(self) -> Self:  # type: ignore[override]
        """Create a copy of the component."""
        return self.model_copy()


class MirroredComponent(FastMCPComponent):
    """Base class for components that are mirrored from a remote server.

    Mirrored components cannot be enabled or disabled directly. Call copy() first
    to create a local version you can modify.
    """

    _mirrored: bool = PrivateAttr(default=False)

    def __init__(self, *, _mirrored: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._mirrored = _mirrored

    def enable(self) -> None:
        """Enable the component."""
        if self._mirrored:
            raise RuntimeError(
                f"Cannot enable mirrored component '{self.name}'. "
                f"Create a local copy first with {self.name}.copy() and add it to your server."
            )
        super().enable()

    def disable(self) -> None:
        """Disable the component."""
        if self._mirrored:
            raise RuntimeError(
                f"Cannot disable mirrored component '{self.name}'. "
                f"Create a local copy first with {self.name}.copy() and add it to your server."
            )
        super().disable()

    def copy(self) -> Self:  # type: ignore[override]
        """Create a copy of the component that can be modified."""
        # Create a copy and mark it as not mirrored
        copied = self.model_copy()
        copied._mirrored = False
        return copied
