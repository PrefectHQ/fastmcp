"""Base classes and interfaces for FastMCP resources."""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, overload

import mcp.types

if TYPE_CHECKING:
    from docket import Docket
    from docket.execution import Execution

    from fastmcp.resources.function_resource import FunctionResource

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

from fastmcp.server.tasks.config import TaskConfig, TaskMeta
from fastmcp.tools.tool import AuthCheckCallable
from fastmcp.utilities.components import FastMCPComponent


class ResourceContent(pydantic.BaseModel):
    """Wrapper for resource content with optional MIME type and metadata.

    Accepts any value for content - strings and bytes pass through directly,
    other types (dict, list, BaseModel, etc.) are automatically JSON-serialized.

    Example:
        ```python
        from fastmcp.resources import ResourceContent

        # String content
        ResourceContent("plain text")

        # Binary content
        ResourceContent(b"binary data", mime_type="application/octet-stream")

        # Auto-serialized to JSON
        ResourceContent({"key": "value"})
        ResourceContent(["a", "b", "c"])
        ```
    """

    content: str | bytes
    mime_type: str | None = None
    meta: dict[str, Any] | None = None

    def __init__(
        self,
        content: Any,
        mime_type: str | None = None,
        meta: dict[str, Any] | None = None,
    ):
        """Create ResourceContent with automatic serialization.

        Args:
            content: The content value. str and bytes pass through directly.
                     Other types (dict, list, BaseModel) are JSON-serialized.
            mime_type: Optional MIME type. Defaults based on content type:
                       str → "text/plain", bytes → "application/octet-stream",
                       other → "application/json"
            meta: Optional metadata dictionary.
        """
        if isinstance(content, str):
            normalized_content: str | bytes = content
            mime_type = mime_type or "text/plain"
        elif isinstance(content, bytes):
            normalized_content = content
            mime_type = mime_type or "application/octet-stream"
        else:
            # dict, list, BaseModel, etc → JSON
            normalized_content = pydantic_core.to_json(content, fallback=str).decode()
            mime_type = mime_type or "application/json"

        super().__init__(content=normalized_content, mime_type=mime_type, meta=meta)

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
                _meta=self.meta,  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
            )
        else:
            return mcp.types.BlobResourceContents(
                uri=AnyUrl(uri) if isinstance(uri, str) else uri,
                blob=base64.b64encode(self.content).decode(),
                mimeType=self.mime_type or "application/octet-stream",
                _meta=self.meta,  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
            )


class ResourceResult(pydantic.BaseModel):
    """Canonical result type for resource reads.

    Provides explicit control over resource responses: multiple content items,
    per-item MIME types, and metadata at both the item and result level.

    Accepts:
        - str: Wrapped as single ResourceContent (text/plain)
        - bytes: Wrapped as single ResourceContent (application/octet-stream)
        - list[ResourceContent]: Used directly for multiple items or custom MIME types

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.resources import ResourceResult, ResourceContent

        mcp = FastMCP()

        # Simple string content
        @mcp.resource("data://simple")
        def get_simple() -> ResourceResult:
            return ResourceResult("hello world")

        # Multiple items with custom MIME types
        @mcp.resource("data://items")
        def get_items() -> ResourceResult:
            return ResourceResult(
                contents=[
                    ResourceContent({"key": "value"}),  # auto-serialized to JSON
                    ResourceContent(b"binary data"),
                ],
                meta={"count": 2}
            )
        ```
    """

    contents: list[ResourceContent]
    meta: dict[str, Any] | None = None

    def __init__(
        self,
        contents: str | bytes | list[ResourceContent],
        meta: dict[str, Any] | None = None,
    ):
        """Create ResourceResult.

        Args:
            contents: String, bytes, or list of ResourceContent objects.
            meta: Optional metadata about the resource result.
        """
        normalized = self._normalize_contents(contents)
        super().__init__(contents=normalized, meta=meta)

    @staticmethod
    def _normalize_contents(
        contents: str | bytes | list[ResourceContent],
    ) -> list[ResourceContent]:
        """Normalize input to list[ResourceContent]."""
        if isinstance(contents, str):
            return [ResourceContent(contents)]
        if isinstance(contents, bytes):
            return [ResourceContent(contents)]
        if isinstance(contents, list):
            # Validate all items are ResourceContent
            for i, item in enumerate(contents):
                if not isinstance(item, ResourceContent):
                    raise TypeError(
                        f"contents[{i}] must be ResourceContent, got {type(item).__name__}. "
                        f"Use ResourceContent({item!r}) to wrap the value."
                    )
            return contents
        raise TypeError(
            f"contents must be str, bytes, or list[ResourceContent], got {type(contents).__name__}"
        )

    def to_mcp_result(self, uri: AnyUrl | str) -> mcp.types.ReadResourceResult:
        """Convert to MCP ReadResourceResult.

        Args:
            uri: The URI of the resource (required by MCP types)

        Returns:
            MCP ReadResourceResult with converted contents
        """
        mcp_contents = [item.to_mcp_resource_contents(uri) for item in self.contents]
        return mcp.types.ReadResourceResult(
            contents=mcp_contents,
            _meta=self.meta,  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
        )


class Resource(FastMCPComponent):
    """Base class for all resources."""

    KEY_PREFIX: ClassVar[str] = "resource"

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
    auth: Annotated[
        AuthCheckCallable | list[AuthCheckCallable] | None,
        Field(description="Authorization checks for this resource", exclude=True),
    ] = None

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        uri: str | AnyUrl,
        *,
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
        from fastmcp.resources.function_resource import (
            FunctionResource,
        )

        return FunctionResource.from_function(
            fn=fn,
            uri=uri,
            name=name,
            version=version,
            title=title,
            description=description,
            icons=icons,
            mime_type=mime_type,
            tags=tags,
            annotations=annotations,
            meta=meta,
            task=task,
            auth=auth,
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

    async def read(
        self,
    ) -> str | bytes | ResourceResult:
        """Read the resource content.

        Subclasses implement this to return resource data. Supported return types:
            - str: Text content
            - bytes: Binary content
            - ResourceResult: Full control over contents and result-level meta
        """
        raise NotImplementedError("Subclasses must implement read()")

    def convert_result(self, raw_value: Any) -> ResourceResult:
        """Convert a raw result to ResourceResult.

        This is used in two contexts:
        1. In _read() to convert user function return values to ResourceResult
        2. In tasks_result_handler() to convert Docket task results to ResourceResult

        Handles ResourceResult passthrough and converts raw values using
        ResourceResult's normalization.
        """
        if isinstance(raw_value, ResourceResult):
            return raw_value

        # ResourceResult.__init__ handles all normalization
        return ResourceResult(raw_value)

    @overload
    async def _read(self, task_meta: None = None) -> ResourceResult: ...

    @overload
    async def _read(self, task_meta: TaskMeta) -> mcp.types.CreateTaskResult: ...

    async def _read(
        self, task_meta: TaskMeta | None = None
    ) -> ResourceResult | mcp.types.CreateTaskResult:
        """Server entry point that handles task routing.

        This allows ANY Resource subclass to support background execution by setting
        task_config.mode to "supported" or "required". The server calls this
        method instead of read() directly.

        Args:
            task_meta: If provided, execute as a background task and return
                CreateTaskResult. If None (default), execute synchronously and
                return ResourceResult.

        Returns:
            ResourceResult when task_meta is None.
            CreateTaskResult when task_meta is provided.

        Subclasses can override this to customize task routing behavior.
        For example, FastMCPProviderResource overrides to delegate to child
        middleware without submitting to Docket.
        """
        from fastmcp.server.tasks.routing import check_background_task

        task_result = await check_background_task(
            component=self, task_type="resource", arguments=None, task_meta=task_meta
        )
        if task_result:
            return task_result

        # Synchronous execution - convert result to ResourceResult
        result = await self.read()
        return self.convert_result(result)

    def to_mcp_resource(
        self,
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
            _meta=overrides.get(  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
                "_meta", self.get_meta()
            ),
        )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(uri={self.uri!r}, name={self.name!r}, description={self.description!r}, tags={self.tags})"

    @property
    def key(self) -> str:
        """The globally unique lookup key for this resource."""
        base_key = self.make_key(str(self.uri))
        return f"{base_key}@{self.version or ''}"

    def register_with_docket(self, docket: Docket) -> None:
        """Register this resource with docket for background execution."""
        if not self.task_config.supports_tasks():
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

    def get_span_attributes(self) -> dict[str, Any]:
        return super().get_span_attributes() | {
            "fastmcp.component.type": "resource",
            "fastmcp.provider.type": "LocalProvider",
        }


__all__ = [
    "Resource",
    "ResourceContent",
    "ResourceResult",
]


def __getattr__(name: str) -> Any:
    """Deprecated re-exports for backwards compatibility."""
    deprecated_exports = {
        "FunctionResource": "FunctionResource",
        "resource": "resource",
    }

    if name in deprecated_exports:
        import warnings

        import fastmcp

        if fastmcp.settings.deprecation_warnings:
            warnings.warn(
                f"Importing {name} from fastmcp.resources.resource is deprecated. "
                f"Import from fastmcp.resources.function_resource instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        from fastmcp.resources import function_resource

        return getattr(function_resource, name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
