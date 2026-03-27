"""MCP Apps support — extension negotiation and typed UI metadata models.

Provides constants and Pydantic models for the MCP Apps extension
(io.modelcontextprotocol/ui), enabling tools and resources to carry
UI metadata for clients that support interactive app rendering.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from fastmcp.utilities.mime import UI_MIME_TYPE as UI_MIME_TYPE
from fastmcp.utilities.mime import resolve_ui_mime_type as resolve_ui_mime_type

UI_EXTENSION_ID = "io.modelcontextprotocol/ui"


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
    visibility: list[Literal["app", "model"]] | None = Field(
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


class PrefabAppConfig(AppConfig):
    """App configuration for Prefab tools with sensible defaults.

    Like ``app=True`` but customizable. Auto-wires the Prefab renderer
    URI and merges the renderer's CSP with any additional domains you
    specify.  The renderer resource is registered automatically.

    Example::

        @mcp.tool(app=PrefabAppConfig())  # same as app=True

        @mcp.tool(app=PrefabAppConfig(
            csp=ResourceCSP(frame_domains=["https://example.com"]),
        ))
    """

    def model_post_init(self, __context: Any) -> None:
        # Set the renderer URI if not explicitly overridden
        if self.resource_uri is None:
            self.resource_uri = "ui://prefab/renderer.html"

        # Merge renderer CSP with user-provided CSP
        try:
            from prefab_ui.renderer import get_renderer_csp

            renderer_csp = get_renderer_csp()
        except ImportError:
            renderer_csp = {}

        if renderer_csp:
            user_csp = self.csp or ResourceCSP()
            merged = ResourceCSP(
                connect_domains=_merge_domains(
                    renderer_csp.get("connect_domains"),
                    user_csp.connect_domains,
                ),
                resource_domains=_merge_domains(
                    renderer_csp.get("resource_domains"),
                    user_csp.resource_domains,
                ),
                frame_domains=user_csp.frame_domains,
                base_uri_domains=user_csp.base_uri_domains,
            )
            self.csp = merged


def _merge_domains(base: list[str] | None, extra: list[str] | None) -> list[str] | None:
    """Merge two domain lists, deduplicating."""
    if base is None and extra is None:
        return None
    combined = list(base or [])
    for d in extra or []:
        if d not in combined:
            combined.append(d)
    return combined or None


def app_config_to_meta_dict(app: AppConfig | dict[str, Any]) -> dict[str, Any]:
    """Convert an AppConfig or dict to the wire-format dict for ``meta["ui"]``."""
    if isinstance(app, AppConfig):
        return app.model_dump(by_alias=True, exclude_none=True)
    return app
