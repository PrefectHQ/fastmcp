"""Base classes and interfaces for FastMCP resources."""

from __future__ import annotations

import base64
import inspect
import warnings
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any

import mcp.types

if TYPE_CHECKING:
    from docket import Docket
    from docket.execution import Execution
import pydantic
import pydantic_core
from mcp.types import Annotations, Icon
from mcp.types import Resource as SDKResource
from pydantic import (
    AnyUrl,
    ConfigDict,
    Field,
    UrlConstraints,
    field_validator,
    model_validator,
)
from typing_extensions import Self

from fastmcp import settings
from fastmcp.server.dependencies import get_context, without_injected_parameters
from fastmcp.server.tasks.config import TaskConfig
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.types import (
    get_fn_name,
)


class ResourceContent(pydantic.BaseModel):
    """Canonical wrapper for resource content.

    This is the internal representation for all resource reads. Users can
    return ResourceContent directly for full control, or return simpler types
    (str, bytes, dict) which will be automatically converted.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.resources import ResourceContent

        mcp = FastMCP()

        @mcp.resource("widget://my-widget")
        def my_widget() -> ResourceContent:
            return ResourceContent(
                content="<widget html>",
                meta={"csp": "script-src 'self'"}
            )
        ```
    """

    model_config = pydantic.ConfigDict(arbitrary_types_allowed=True)

    content: str | bytes
    mime_type: str | None = None
    meta: dict[str, Any] | None = None

    @classmethod
    def from_value(
        cls,
        value: Any,
        mime_type: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ResourceContent:
        """Convert any value to ResourceContent, handling serialization.

        Args:
            value: The value to convert. Can be:
                - ResourceContent: returned as-is (meta param ignored)
                - str: text content
                - bytes: binary content
                - other: serialized to JSON string

            mime_type: Optional MIME type override. If not provided:
                - str → "text/plain"
                - bytes → "application/octet-stream"
                - other → "application/json"

            meta: Optional metadata (ignored if value is already ResourceContent)

        Returns:
            ResourceContent instance
        """
        if isinstance(value, ResourceContent):
            return value
        if isinstance(value, str):
            return cls(content=value, mime_type=mime_type or "text/plain", meta=meta)
        if isinstance(value, bytes):
            return cls(
                content=value,
                mime_type=mime_type or "application/octet-stream",
                meta=meta,
            )
        # dict, list, BaseModel, etc → JSON
        json_str = pydantic_core.to_json(value, fallback=str).decode()
        return cls(
            content=json_str, mime_type=mime_type or "application/json", meta=meta
        )

    def to_mcp_resource_contents(
        self, uri: AnyUrl | str
    ) -> mcp.types.TextResourceContents | mcp.types.BlobResourceContents:
        """Convert to MCP resource contents type.

        Args:
            uri: The URI of the resource (required by MCP types)

        Returns:
            TextResourceContents for str content, BlobResourceContents for bytes
        """
        if isinstance(self.content, str):
            return mcp.types.TextResourceContents(
                uri=AnyUrl(uri) if isinstance(uri, str) else uri,
                text=self.content,
                mimeType=self.mime_type or "text/plain",
                _meta=self.meta,
            )
        else:
            return mcp.types.BlobResourceContents(
                uri=AnyUrl(uri) if isinstance(uri, str) else uri,
                blob=base64.b64encode(self.content).decode(),
                mimeType=self.mime_type or "application/octet-stream",
                _meta=self.meta,
            )


class Resource(FastMCPComponent):
    """Base class for all resources."""

    model_config = ConfigDict(validate_default=True)

    uri: Annotated[AnyUrl, UrlConstraints(host_required=False)] = Field(
        default=..., description="URI of the resource"
    )
    name: str = Field(default="", description="Name of the resource")
    mime_type: str = Field(
        default="text/plain",
        description="MIME type of the resource content",
    )
    annotations: Annotated[
        Annotations | None,
        Field(description="Optional annotations about the resource's behavior"),
    ] = None

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
        fn: Callable[..., Any],
        uri: str | AnyUrl,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[Icon] | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
        annotations: Annotations | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
    ) -> FunctionResource:
        return FunctionResource.from_function(
            fn=fn,
            uri=uri,
            name=name,
            title=title,
            description=description,
            icons=icons,
            mime_type=mime_type,
            tags=tags,
            enabled=enabled,
            annotations=annotations,
            meta=meta,
            task=task,
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

    async def read(self) -> str | bytes | ResourceContent:
        """Read the resource content.

        This method must be implemented by subclasses. For backwards compatibility,
        subclasses can return str, bytes, or ResourceContent. However, returning
        str or bytes is deprecated - new code should return ResourceContent.

        Returns:
            str | bytes | ResourceContent: The resource content. Returning str
            or bytes is deprecated; prefer ResourceContent for full control
            over MIME type and metadata.
        """
        raise NotImplementedError("Subclasses must implement read()")

    def convert_result(self, raw_value: Any) -> ResourceContent:
        """Convert a raw return value to ResourceContent.

        Handles ResourceContent passthrough and converts raw values using mime_type.
        """
        return ResourceContent.from_value(raw_value, mime_type=self.mime_type)

    async def _read(self) -> ResourceContent | mcp.types.CreateTaskResult:
        """Server entry point that handles task routing.

        This allows ANY Resource subclass to support background execution by setting
        task_config.mode to "supported" or "required". The server calls this
        method instead of read() directly.

        Subclasses can override this to customize task routing behavior.
        For example, FastMCPProviderResource overrides to delegate to child
        middleware without submitting to Docket.
        """
        from fastmcp.server.dependencies import _docket_fn_key
        from fastmcp.server.tasks.routing import check_background_task

        key = _docket_fn_key.get() or self.key
        task_result = await check_background_task(
            component=self, task_type="resource", key=key
        )
        if task_result:
            return task_result

        # Synchronous execution
        result = await self.read()
        if isinstance(result, ResourceContent):
            return result
        # Deprecated in 2.14.1: returning str/bytes from read()
        if settings.deprecation_warnings:
            warnings.warn(
                f"Resource.read() returning str or bytes is deprecated (since 2.14.1). "
                f"Return ResourceContent instead. "
                f"(Resource: {self.__class__.__name__}, URI: {self.uri})",
                DeprecationWarning,
                stacklevel=2,
            )
        return self.convert_result(result)

    def to_mcp_resource(
        self,
        *,
        include_fastmcp_meta: bool | None = None,
        **overrides: Any,
    ) -> SDKResource:
        """Convert the resource to an SDKResource."""

        return SDKResource(
            name=overrides.get("name", self.name),
            uri=overrides.get("uri", self.uri),
            description=overrides.get("description", self.description),
            mimeType=overrides.get("mimeType", self.mime_type),
            title=overrides.get("title", self.title),
            icons=overrides.get("icons", self.icons),
            annotations=overrides.get("annotations", self.annotations),
            _meta=overrides.get(
                "_meta", self.get_meta(include_fastmcp_meta=include_fastmcp_meta)
            ),
        )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(uri={self.uri!r}, name={self.name!r}, description={self.description!r}, tags={self.tags})"

    @property
    def key(self) -> str:
        """The lookup key for this resource. Returns str(uri)."""
        return str(self.uri)

    def register_with_docket(self, docket: Docket) -> None:
        """Register this resource with docket for background execution."""
        if self.task_config.mode == "forbidden":
            return
        docket.register(self.read, names=[self.key])

    async def add_to_docket(  # type: ignore[override]
        self,
        docket: Docket,
        *,
        fn_key: str | None = None,
        task_key: str | None = None,
        **kwargs: Any,
    ) -> Execution:
        """Schedule this resource for background execution via docket.

        Args:
            docket: The Docket instance
            fn_key: Function lookup key in Docket registry (defaults to self.key)
            task_key: Redis storage key for the result
            **kwargs: Additional kwargs passed to docket.add()
        """
        lookup_key = fn_key or self.key
        if task_key:
            kwargs["key"] = task_key
        return await docket.add(lookup_key, **kwargs)()


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
        uri: str | AnyUrl,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[Icon] | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
        annotations: Annotations | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
    ) -> FunctionResource:
        """Create a FunctionResource from a function."""
        if isinstance(uri, str):
            uri = AnyUrl(uri)

        func_name = name or get_fn_name(fn)

        # Normalize task to TaskConfig and validate
        if task is None:
            task_config = TaskConfig(mode="forbidden")
        elif isinstance(task, bool):
            task_config = TaskConfig.from_bool(task)
        else:
            task_config = task
        task_config.validate_function(fn, func_name)

        # Wrap fn to handle dependency resolution internally
        wrapped_fn = without_injected_parameters(fn)

        return cls(
            fn=wrapped_fn,
            uri=uri,
            name=name or get_fn_name(fn),
            title=title,
            description=description or inspect.getdoc(fn),
            icons=icons,
            mime_type=mime_type or "text/plain",
            tags=tags or set(),
            enabled=enabled if enabled is not None else True,
            annotations=annotations,
            meta=meta,
            task_config=task_config,
        )

    async def read(self) -> str | bytes | ResourceContent:
        """Read the resource by calling the wrapped function.

        Returns:
            str | bytes | ResourceContent: The resource content. If the user's
            function returns str, bytes, dict, etc., it will be wrapped
            in ResourceContent. Nested Resource reads may return raw types.
        """
        # self.fn is wrapped by without_injected_parameters which handles
        # dependency resolution internally
        result = self.fn()
        if inspect.isawaitable(result):
            result = await result

        # If user returned another Resource, read it recursively
        if isinstance(result, Resource):
            return await result.read()

        return self.convert_result(result)

    def register_with_docket(self, docket: Docket) -> None:
        """Register this resource with docket for background execution.

        FunctionResource registers the underlying function, which has the user's
        Depends parameters for docket to resolve.
        """
        if self.task_config.mode == "forbidden":
            return
        docket.register(self.fn, names=[self.key])
