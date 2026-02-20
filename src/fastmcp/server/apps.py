"""MCP Apps support — extension negotiation and typed UI metadata models.

Provides constants and Pydantic models for the MCP Apps extension
(io.modelcontextprotocol/ui), enabling tools and resources to carry
UI metadata for clients that support interactive app rendering.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

UI_EXTENSION_ID = "io.modelcontextprotocol/ui"
UI_MIME_TYPE = "text/html;profile=mcp-app"


class ResourceCSP(BaseModel):
    """Content Security Policy for MCP App resources.

    Declares which external origins the app is allowed to connect to or
    load resources from.  Hosts use these declarations to build the
    ``Content-Security-Policy`` header for the sandboxed iframe.
    """

    connect_domains: list[str] | None = Field(
        default=None,
        alias="connectDomains",
        description="Origins allowed for fetch/XHR/WebSocket (connect-src)",
    )
    resource_domains: list[str] | None = Field(
        default=None,
        alias="resourceDomains",
        description="Origins allowed for scripts, images, styles, fonts (script-src etc.)",
    )
    frame_domains: list[str] | None = Field(
        default=None,
        alias="frameDomains",
        description="Origins allowed for nested iframes (frame-src)",
    )
    base_uri_domains: list[str] | None = Field(
        default=None,
        alias="baseUriDomains",
        description="Allowed base URIs for the document (base-uri)",
    )

    model_config = {"populate_by_name": True, "extra": "allow"}


class ResourcePermissions(BaseModel):
    """Iframe sandbox permissions for MCP App resources.

    Each field, when set (typically to ``{}``), requests that the host
    grant the corresponding Permission Policy feature to the sandboxed
    iframe.  Hosts MAY honour these; apps should use JS feature detection
    as a fallback.
    """

    camera: dict[str, Any] | None = Field(
        default=None, description="Request camera access"
    )
    microphone: dict[str, Any] | None = Field(
        default=None, description="Request microphone access"
    )
    geolocation: dict[str, Any] | None = Field(
        default=None, description="Request geolocation access"
    )
    clipboard_write: dict[str, Any] | None = Field(
        default=None,
        alias="clipboardWrite",
        description="Request clipboard-write access",
    )

    model_config = {"populate_by_name": True, "extra": "allow"}


class AppConfig(BaseModel):
    """Configuration for MCP App tools and resources.

    Controls how a tool or resource participates in the MCP Apps extension.
    On tools, ``resource_uri`` and ``visibility`` specify which UI resource
    to render and where the tool appears.  On resources, those fields must
    be left unset (the resource itself is the UI).

    All fields use ``exclude_none`` serialization so only explicitly-set
    values appear on the wire.  Aliases match the MCP Apps wire format
    (camelCase).
    """

    resource_uri: str | None = Field(
        default=None,
        alias="resourceUri",
        description="URI of the UI resource (typically ui:// scheme). Tools only.",
    )
    visibility: list[str] | None = Field(
        default=None,
        description="Where this tool is visible: 'app', 'model', or both. Tools only.",
    )
    csp: ResourceCSP | None = Field(
        default=None, description="Content Security Policy for the app iframe"
    )
    permissions: ResourcePermissions | None = Field(
        default=None, description="Iframe sandbox permissions"
    )
    domain: str | None = Field(default=None, description="Domain for the iframe")
    prefers_border: bool | None = Field(
        default=None,
        alias="prefersBorder",
        description="Whether the UI prefers a visible border",
    )

    model_config = {"populate_by_name": True, "extra": "allow"}


def app_config_to_meta_dict(app: AppConfig | dict[str, Any]) -> dict[str, Any]:
    """Convert an AppConfig or dict to the wire-format dict for ``meta["ui"]``."""
    if isinstance(app, AppConfig):
        return app.model_dump(by_alias=True, exclude_none=True)
    return app


def resolve_ui_mime_type(uri: str, explicit_mime_type: str | None) -> str | None:
    """Return the appropriate MIME type for a resource URI.

    For ``ui://`` scheme resources, defaults to ``UI_MIME_TYPE`` when no
    explicit MIME type is provided. This ensures UI resources are correctly
    identified regardless of how they're registered (via FastMCP.resource,
    the standalone @resource decorator, or resource templates).

    Args:
        uri: The resource URI string
        explicit_mime_type: The MIME type explicitly provided by the user

    Returns:
        The resolved MIME type (explicit value, UI default, or None)
    """
    if explicit_mime_type is not None:
        return explicit_mime_type
    # Case-insensitive scheme check per RFC 3986
    if uri.lower().startswith("ui://"):
        return UI_MIME_TYPE
    return None
