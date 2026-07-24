"""Base classes and interfaces for FastMCP resources."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any, ClassVar

import mcp_types

if TYPE_CHECKING:
    from fastmcp.resources.function_resource import FunctionResource

import pydantic
import pydantic_core
from mcp_types import Annotations, Icon
from mcp_types import Resource as SDKResource
from pydantic import (
    AnyUrl,
    ConfigDict,
    Field,
    UrlConstraints,
    field_validator,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema
from typing_extensions import Self

from fastmcp.utilities.authorization import AuthCheck
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
    ) -> mcp_types.TextResourceContents | mcp_types.BlobResourceContents:
        """Convert to MCP resource contents type.

        Args:
            uri: The URI of the resource (required by MCP types)

        Returns:
            TextResourceContents for str content, BlobResourceContents for bytes
        """
        if isinstance(self.content, str):
            return mcp_types.TextResourceContents(
                uri=str(uri),
                text=self.content,
                mime_type=self.mime_type or "text/plain",
                _meta=self.meta,  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
            )
        else:
            return mcp_types.BlobResourceContents(
                uri=str(uri),
                blob=base64.b64encode(self.content).decode(),
                mime_type=self.mime_type or "application/octet-stream",
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
        # Auto-serialize JSON-native types to JSON text
        if (
            isinstance(contents, dict | list | tuple | int | float | bool)
            or contents is None
        ):
            return [ResourceContent(json.dumps(contents), mime_type="application/json")]
        raise TypeError(
            f"contents must be str, bytes, or list[ResourceContent], got {type(contents).__name__}"
        )

    def to_mcp_result(self, uri: AnyUrl | str) -> mcp_types.ReadResourceResult:
        """Convert to MCP ReadResourceResult.

        Args:
            uri: The URI of the resource (required by MCP types)

        Returns:
            MCP ReadResourceResult with converted contents
        """
        mcp_contents = [item.to_mcp_resource_contents(uri) for item in self.contents]
        return mcp_types.ReadResourceResult(
            contents=mcp_contents,
            _meta=self.meta,  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
        )


def _public_content_meta(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    """Strip FastMCP's internal bookkeeping out of component meta.

    Component `meta` carries private entries under the `fastmcp` namespace
    (e.g. `_internal.visibility`) that must never reach the wire. Listings
    already filter these via `FastMCPComponent.get_meta()`; content items
    served by `resources/read` need the same treatment.

    Returns None when nothing public remains, so resources without user
    metadata keep an absent `_meta` rather than an empty object.
    """
    if not meta:
        return None

    public = dict(meta)
    fastmcp_meta = public.get("fastmcp")
    if isinstance(fastmcp_meta, dict):
        public_fastmcp = {
            key: value for key, value in fastmcp_meta.items() if not key.startswith("_")
        }
        if public_fastmcp:
            public["fastmcp"] = public_fastmcp
        else:
            public.pop("fastmcp")

    return public or None


def convert_raw_to_resource_result(
    raw_value: Any,
    *,
    mime_type: str | None,
    meta: dict[str, Any] | None,
) -> ResourceResult:
    """Wrap a user function's return value in a ResourceResult.

    Shared by `Resource` and `ResourceTemplate` so both honor the MIME type
    the component declares in listings. A component that advertises
    `text/csv` must not serve `text/plain` on read.

    Args:
        raw_value: The value returned by the user's function.
        mime_type: The component's declared MIME type, forwarded to content items.
        meta: Component-level meta (e.g. `ui` metadata for MCP Apps CSP/permissions)
            propagated to each content item.
    """
    if isinstance(raw_value, ResourceResult):
        return raw_value

    meta = _public_content_meta(meta)

    # For plain str/bytes returns, wrap in ResourceContent with the
    # component's MIME type and meta so the wire response carries the
    # correct type and metadata (e.g. CSP for MCP Apps).
    if isinstance(raw_value, (str, bytes)):
        return ResourceResult(
            [ResourceContent(raw_value, mime_type=mime_type, meta=meta)]
        )

    # For JSON-native types (dict, list, tuple, int, float, bool, None),
    # serialize and wrap in ResourceContent with the component's meta,
    # matching the str/bytes path above so CSP/permissions propagate.
    # Exclude list[ResourceContent] which should go through ResourceResult
    # normalization below.
    if (
        isinstance(raw_value, dict | list | tuple | int | float | bool)
        or raw_value is None
    ) and not (
        isinstance(raw_value, list)
        and raw_value
        and isinstance(raw_value[0], ResourceContent)
    ):
        return ResourceResult(
            [
                ResourceContent(
                    json.dumps(raw_value),
                    mime_type=mime_type or "application/json",
                    meta=meta,
                )
            ]
        )

    # All other types fall through to ResourceResult for error handling
    return ResourceResult(raw_value)


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
        SkipJsonSchema[AuthCheck | list[AuthCheck] | None],
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
        auth: AuthCheck | list[AuthCheck] | None = None,
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
        ResourceResult's normalization.  When the raw value is a plain
        string or bytes, the resource's own ``mime_type`` is forwarded so
        that ``ui://`` resources (and others with non-default MIME types)
        don't fall back to ``text/plain``.

        The resource's component-level ``meta`` (e.g. ``ui`` metadata for
        MCP Apps CSP/permissions) is propagated to each content item so
        that hosts can read it from the ``resources/read`` response.
        """
        return convert_raw_to_resource_result(
            raw_value, mime_type=self.mime_type, meta=self.meta
        )

    async def _read(self) -> ResourceResult:
        """Server entry point for resource reads.

        The server calls this method instead of ``read()`` directly so that
        subclasses can customize dispatch. For example,
        ``FastMCPProviderResource`` overrides this to delegate to child-server
        middleware.
        """
        result = await self.read()
        return self.convert_result(result)

    def to_mcp_resource(
        self,
        **overrides: Any,
    ) -> SDKResource:
        """Convert the resource to an SDKResource."""

        return SDKResource(
            name=overrides.get("name", self.name),
            uri=str(overrides.get("uri", self.uri)),
            description=overrides.get("description", self.description),
            mime_type=overrides.get("mimeType", self.mime_type),
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
