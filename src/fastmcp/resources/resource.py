"""Base classes and interfaces for FastMCP resources."""

from __future__ import annotations

import abc
import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any

import pydantic_core
from mcp.types import Resource as MCPResource
from pydantic import (
    AnyUrl,
    ConfigDict,
    Field,
    UrlConstraints,
    field_validator,
    model_validator,
)
from typing_extensions import Self

from fastmcp.server.dependencies import get_context
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.types import (
    find_kwarg_by_type,
)

if TYPE_CHECKING:
    pass


class Resource(FastMCPComponent, abc.ABC):
    """Base class for all resources."""

    model_config = ConfigDict(validate_default=True)

    uri: Annotated[AnyUrl, UrlConstraints(host_required=False)] = Field(
        default=..., description="URI of the resource"
    )
    name: str = Field(default="", description="Name of the resource")
    mime_type: str = Field(
        default="text/plain",
        description="MIME type of the resource content",
        pattern=r"^[a-zA-Z0-9]+/[a-zA-Z0-9\-+.]+$",
    )

    def enable(self) -> None:
        super().enable()
        try:
            context = get_context()
            context._queue_resource_list_changed()  # type: ignore[private-use]
        except RuntimeError:
            pass  # No context available

    def disable(self) -> None:
        super().disable()
        try:
            context = get_context()
            context._queue_resource_list_changed()  # type: ignore[private-use]
        except RuntimeError:
            pass  # No context available

    @staticmethod
    def from_function(
        fn: Callable[[], Any],
        uri: str | AnyUrl,
        name: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
    ) -> FunctionResource:
        return FunctionResource.from_function(
            fn=fn,
            uri=uri,
            name=name,
            description=description,
            mime_type=mime_type,
            tags=tags,
            enabled=enabled,
        )

    @field_validator("mime_type", mode="before")
    @classmethod
    def set_default_mime_type(cls, mime_type: str | None) -> str:
        """Set default MIME type if not provided."""
        if mime_type:
            return mime_type
        return "text/plain"

    @model_validator(mode="after")
    def set_default_name(self) -> Self:
        """Set default name from URI if not provided."""
        if self.name:
            pass
        elif self.uri:
            self.name = str(self.uri)
        else:
            raise ValueError("Either name or uri must be provided")
        return self

    @abc.abstractmethod
    async def read(self) -> str | bytes:
        """Read the resource content."""
        pass

    def to_mcp_resource(self, **overrides: Any) -> MCPResource:
        """Convert the resource to an MCPResource."""
        kwargs = {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime_type,
        }
        return MCPResource(**kwargs | overrides)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(uri={self.uri!r}, name={self.name!r}, description={self.description!r}, tags={self.tags})"

    @property
    def key(self) -> str:
        """
        The key of the component. This is used for internal bookkeeping
        and may reflect e.g. prefixes or other identifiers. You should not depend on
        keys having a certain value, as the same tool loaded from different
        hierarchies of servers may have different keys.
        """
        return self._key or str(self.uri)


class FunctionResource(Resource):
    """A resource that defers data loading by wrapping a function.

    The function is only called when the resource is read, allowing for lazy loading
    of potentially expensive data. This is particularly useful when listing resources,
    as the function won't be called until the resource is actually accessed.

    The function can return:
    - str for text content (default)
    - bytes for binary content
    - other types will be converted to JSON
    """

    fn: Callable[[], Any]

    @classmethod
    def from_function(
        cls,
        fn: Callable[[], Any],
        uri: str | AnyUrl,
        name: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
    ) -> FunctionResource:
        """Create a FunctionResource from a function."""
        if isinstance(uri, str):
            uri = AnyUrl(uri)
        return cls(
            fn=fn,
            uri=uri,
            name=name or fn.__name__,
            description=description or inspect.getdoc(fn),
            mime_type=mime_type or "text/plain",
            tags=tags or set(),
            enabled=enabled if enabled is not None else True,
        )

    async def read(self) -> str | bytes:
        """Read the resource by calling the wrapped function."""
        from fastmcp.server.context import Context

        kwargs = {}
        context_kwarg = find_kwarg_by_type(self.fn, kwarg_type=Context)
        if context_kwarg is not None:
            kwargs[context_kwarg] = get_context()

        result = self.fn(**kwargs)
        if inspect.iscoroutinefunction(self.fn):
            result = await result

        if isinstance(result, Resource):
            return await result.read()
        elif isinstance(result, bytes):
            return result
        elif isinstance(result, str):
            return result
        else:
            return pydantic_core.to_json(result, fallback=str, indent=2).decode()
