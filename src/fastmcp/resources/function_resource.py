"""Standalone @resource decorator for FastMCP."""

from __future__ import annotations

import inspect
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeVar, runtime_checkable

from mcp.types import Annotations, Icon
from pydantic import AnyUrl

import fastmcp
from fastmcp.decorators import resolve_task_config
from fastmcp.resources.resource import Resource, ResourceResult
from fastmcp.server.dependencies import (
    transform_context_annotations,
    without_injected_parameters,
)
from fastmcp.server.tasks.config import TaskConfig
from fastmcp.tools.tool import AuthCheckCallable
from fastmcp.utilities.async_utils import call_sync_fn_in_threadpool

if TYPE_CHECKING:
    from docket import Docket

    from fastmcp.resources.template import ResourceTemplate

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
    task: bool | TaskConfig | None = None
    auth: AuthCheckCallable | list[AuthCheckCallable] | None = None
    enabled: bool = True


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

    fn: Callable[..., Any]

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
        task: bool | TaskConfig | None = None,
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
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
                    task,
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
                task=task,
                auth=auth,
            )

        uri_obj = AnyUrl(metadata.uri)

        # Get function name - use class name for callable objects
        func_name = (
            metadata.name or getattr(fn, "__name__", None) or fn.__class__.__name__
        )

        # Normalize task to TaskConfig and validate
        task_value = metadata.task
        if task_value is None:
            task_config = TaskConfig(mode="forbidden")
        elif isinstance(task_value, bool):
            task_config = TaskConfig.from_bool(task_value)
        else:
            task_config = task_value
        task_config.validate_function(fn, func_name)

        # if the fn is a callable class, we need to get the __call__ method from here out
        if not inspect.isroutine(fn):
            fn = fn.__call__
        # if the fn is a staticmethod, we need to work with the underlying function
        if isinstance(fn, staticmethod):
            fn = fn.__func__

        # Transform Context type annotations to Depends() for unified DI
        fn = transform_context_annotations(fn)

        # Wrap fn to handle dependency resolution internally
        wrapped_fn = without_injected_parameters(fn)

        return cls(
            fn=wrapped_fn,
            uri=uri_obj,
            name=func_name,
            version=str(metadata.version) if metadata.version is not None else None,
            title=metadata.title,
            description=metadata.description or inspect.getdoc(fn),
            icons=metadata.icons,
            mime_type=metadata.mime_type or "text/plain",
            tags=metadata.tags or set(),
            annotations=metadata.annotations,
            meta=metadata.meta,
            task_config=task_config,
            auth=metadata.auth,
        )

    async def read(
        self,
    ) -> str | bytes | ResourceResult:
        """Read the resource by calling the wrapped function."""
        # self.fn is wrapped by without_injected_parameters which handles
        # dependency resolution internally
        if inspect.iscoroutinefunction(self.fn):
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

    def register_with_docket(self, docket: Docket) -> None:
        """Register this resource with docket for background execution.

        FunctionResource registers the underlying function, which has the user's
        Depends parameters for docket to resolve.
        """
        if not self.task_config.supports_tasks():
            return
        docket.register(self.fn, names=[self.key])


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
    task: bool | TaskConfig | None = None,
    auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
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

    def create_resource(fn: Callable[..., Any]) -> FunctionResource | ResourceTemplate:
        from fastmcp.resources.template import ResourceTemplate
        from fastmcp.server.dependencies import without_injected_parameters

        resolved = resolve_task_config(task)
        has_uri_params = "{" in uri and "}" in uri
        wrapper_fn = without_injected_parameters(fn)
        has_func_params = bool(inspect.signature(wrapper_fn).parameters)

        # Create metadata first
        resource_meta = ResourceMeta(
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
            task=resolved,
            auth=auth,
        )

        if has_uri_params or has_func_params:
            # ResourceTemplate doesn't have metadata support yet, so pass individual params
            return ResourceTemplate.from_function(
                fn=fn,
                uri_template=uri,
                name=name,
                version=version,
                title=title,
                description=description,
                icons=icons,
                mime_type=mime_type,
                tags=tags,
                annotations=annotations,
                meta=meta,
                task=resolved,
                auth=auth,
            )
        else:
            return FunctionResource.from_function(fn, metadata=resource_meta)

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
            task=task,
            auth=auth,
        )
        target = fn.__func__ if hasattr(fn, "__func__") else fn
        target.__fastmcp__ = metadata
        return fn

    def decorator(fn: F) -> F:
        if fastmcp.settings.decorator_mode == "object":
            warnings.warn(
                "decorator_mode='object' is deprecated and will be removed in a future version. "
                "Decorators now return the original function with metadata attached.",
                DeprecationWarning,
                stacklevel=3,
            )
            return create_resource(fn)  # type: ignore[return-value]
        return attach_metadata(fn)

    return decorator
