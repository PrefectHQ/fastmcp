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
    BeforeValidator,
    ConfigDict,
    Field,
    UrlConstraints,
    ValidationInfo,
    field_validator,
)

from fastmcp.server.dependencies import get_context
from fastmcp.utilities.types import (
    FastMCPBaseModel,
    _convert_set_defaults,
    find_kwarg_by_type,
)

if TYPE_CHECKING:
    pass


class Resource(FastMCPBaseModel, abc.ABC):
    """Base class for all resources."""

    model_config = ConfigDict(validate_default=True)

    uri: Annotated[AnyUrl, UrlConstraints(host_required=False)] = Field(
        default=..., description="URI of the resource"
    )
    name: str | None = Field(description="Name of the resource", default=None)
    description: str | None = Field(
        description="Description of the resource", default=None
    )
    tags: Annotated[set[str], BeforeValidator(_convert_set_defaults)] = Field(
        default_factory=set, description="Tags for the resource"
    )
    mime_type: str = Field(
        default="text/plain",
        description="MIME type of the resource content",
        pattern=r"^[a-zA-Z0-9]+/[a-zA-Z0-9\-+.]+$",
    )

    @staticmethod
    def from_function(
        fn: Callable[[], Any],
        uri: str | AnyUrl,
        name: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
    ) -> FunctionResource:
        return FunctionResource.from_function(
            fn=fn,
            uri=uri,
            name=name,
            description=description,
            mime_type=mime_type,
            tags=tags,
        )

    @field_validator("mime_type", mode="before")
    @classmethod
    def set_default_mime_type(cls, mime_type: str | None) -> str:
        """Set default MIME type if not provided."""
        if mime_type:
            return mime_type
        return "text/plain"

    @field_validator("name", mode="before")
    @classmethod
    def set_default_name(cls, name: str | None, info: ValidationInfo) -> str:
        """Set default name from URI if not provided."""
        if name:
            return name
        if uri := info.data.get("uri"):
            return str(uri)
        raise ValueError("Either name or uri must be provided")

    @abc.abstractmethod
    async def read(self) -> str | bytes:
        """Read the resource content."""
        pass

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return False
        assert isinstance(other, type(self))
        return self.model_dump() == other.model_dump()

    def to_mcp_resource(self, **overrides: Any) -> MCPResource:
        """Convert the resource to an MCPResource."""
        kwargs = {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime_type,
        }
        return MCPResource(**kwargs | overrides)


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
    ) -> FunctionResource:
        """Create a FunctionResource from a function."""
        if isinstance(uri, str):
            uri = AnyUrl(uri)
        return cls(
            fn=fn,
            uri=uri,
            name=name or fn.__name__,
            description=description or fn.__doc__,
            mime_type=mime_type or "text/plain",
            tags=tags or set(),
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
