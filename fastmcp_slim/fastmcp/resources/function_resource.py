"""Standalone @resource decorator for FastMCP."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from types import MethodType
from typing import (
    Any,
    Literal,
    Protocol,
    TypeVar,
    cast,
    runtime_checkable,
)

from mcp_types import Annotations, Icon
from pydantic import AnyUrl
from pydantic.json_schema import SkipJsonSchema

from fastmcp.resources.base import Resource, ResourceResult
from fastmcp.resources.security import (
    INHERIT_SECURITY,
    InheritSecurity,
    ResourceSecurity,
)
from fastmcp.utilities.async_utils import (
    call_sync_fn_in_threadpool,
    is_coroutine_function,
)
from fastmcp.utilities.authorization import AuthCheck
from fastmcp.utilities.mime import resolve_ui_mime_type

F = TypeVar("F", bound=Callable[..., Any])


@runtime_checkable
class DecoratedResource(Protocol):
    """Protocol for functions decorated with @resource."""

    __fastmcp__: ResourceMeta

    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, kw_only=True)
class ResourceMeta:
    """Metadata attached to functions by the @resource decorator."""

    type: Literal["resource"] = field(default="resource", init=False)
    uri: str
    name: str | None = None
    version: str | int | None = None
    title: str | None = None
    description: str | None = None
    icons: list[Icon] | None = None
    tags: set[str] | None = None
    mime_type: str | None = None
    annotations: Annotations | None = None
    meta: dict[str, Any] | None = None
    auth: AuthCheck | list[AuthCheck] | None = None
    enabled: bool = True
    security: ResourceSecurity | None | InheritSecurity = INHERIT_SECURITY


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

    fn: SkipJsonSchema[Callable[..., Any]]

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        uri: str | AnyUrl | None = None,
        *,
        metadata: ResourceMeta | None = None,
        # Keep individual params for backwards compat
        name: str | None = None,
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[Icon] | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
        annotations: Annotations | None = None,
        meta: dict[str, Any] | None = None,
        auth: AuthCheck | list[AuthCheck] | None = None,
    ) -> FunctionResource:
        """Create a FunctionResource from a function.

        Args:
            fn: The function to wrap
            uri: The URI for the resource (required if metadata not provided)
            metadata: ResourceMeta object with all configuration. If provided,
                individual parameters must not be passed.
            name, title, etc.: Individual parameters for backwards compatibility.
                Cannot be used together with metadata parameter.
        """
        # Check mutual exclusion
        individual_params_provided = (
            any(
                x is not None
                for x in [
                    name,
                    version,
                    title,
                    description,
                    icons,
                    mime_type,
                    tags,
                    annotations,
                    meta,
                    auth,
                ]
            )
            or uri is not None
        )

        if metadata is not None and individual_params_provided:
            raise TypeError(
                "Cannot pass both 'metadata' and individual parameters to from_function(). "
                "Use metadata alone or individual parameters alone."
            )

        # Build metadata from kwargs if not provided
        if metadata is None:
            if uri is None:
                raise TypeError("uri is required when metadata is not provided")
            metadata = ResourceMeta(
                uri=str(uri),
                name=name,
                version=version,
                title=title,
                description=description,
                icons=icons,
                tags=tags,
                mime_type=mime_type,
                annotations=annotations,
                meta=meta,
                auth=auth,
            )

        uri_obj = AnyUrl(metadata.uri)

        # Get function name - use class name for callable objects
        func_name = (
            metadata.name or getattr(fn, "__name__", None) or fn.__class__.__name__
        )

        # if the fn is a callable class, we need to get the __call__ method from here out
        if not inspect.isroutine(fn) and not isinstance(fn, functools.partial):
            fn = fn.__call__
        # if the fn is a staticmethod, we need to work with the underlying function
        if isinstance(fn, staticmethod):
            fn = fn.__func__

        # Transform Context type annotations to Depends() for unified DI
        from fastmcp.server.dependencies import (
            transform_context_annotations,
            without_injected_parameters,
        )

        fn = transform_context_annotations(fn)

        # Wrap fn to handle dependency resolution internally
        wrapped_fn = without_injected_parameters(fn)

        # Apply ui:// MIME default, then fall back to text/plain
        resolved_mime = resolve_ui_mime_type(metadata.uri, metadata.mime_type)

        return cls(
            fn=wrapped_fn,
            uri=uri_obj,
            name=func_name,
            version=str(metadata.version) if metadata.version is not None else None,
            title=metadata.title,
            description=metadata.description
            if metadata.description is not None
            else inspect.getdoc(fn),
            icons=metadata.icons,
            mime_type=resolved_mime or "text/plain",
            tags=metadata.tags or set(),
            annotations=metadata.annotations,
            meta=metadata.meta,
            auth=metadata.auth,
        )

    async def read(
        self,
    ) -> str | bytes | ResourceResult:
        """Read the resource by calling the wrapped function."""
        # self.fn is wrapped by without_injected_parameters which handles
        # dependency resolution internally
        if is_coroutine_function(self.fn):
            result = await self.fn()
        else:
            # Run sync functions in threadpool to avoid blocking the event loop
            result = await call_sync_fn_in_threadpool(self.fn)
            # Handle sync wrappers that return awaitables (e.g., partial(async_fn))
            if inspect.isawaitable(result):
                result = await result

        # If user returned another Resource, read it recursively
        if isinstance(result, Resource):
            return await result.read()

        return result


def resource(
    uri: str,
    *,
    name: str | None = None,
    version: str | int | None = None,
    title: str | None = None,
    description: str | None = None,
    icons: list[Icon] | None = None,
    mime_type: str | None = None,
    tags: set[str] | None = None,
    annotations: Annotations | dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    auth: AuthCheck | list[AuthCheck] | None = None,
    security: ResourceSecurity | None | InheritSecurity = INHERIT_SECURITY,
) -> Callable[[F], F]:
    """Standalone decorator to mark a function as an MCP resource.

    Returns the original function with metadata attached. Register with a server
    using mcp.add_resource().
    """
    if isinstance(annotations, dict):
        annotations = Annotations(**annotations)

    if inspect.isroutine(uri):
        raise TypeError(
            "The @resource decorator requires a URI. "
            "Use @resource('uri') instead of @resource"
        )

    def attach_metadata(fn: F) -> F:
        metadata = ResourceMeta(
            uri=uri,
            name=name,
            version=version,
            title=title,
            description=description,
            icons=icons,
            tags=tags,
            mime_type=mime_type,
            annotations=annotations,
            meta=meta,
            auth=auth,
            security=security,
        )
        target = fn.__func__ if isinstance(fn, staticmethod | MethodType) else fn
        cast(Any, target).__fastmcp__ = metadata
        return fn

    def decorator(fn: F) -> F:
        return attach_metadata(fn)

    return decorator
