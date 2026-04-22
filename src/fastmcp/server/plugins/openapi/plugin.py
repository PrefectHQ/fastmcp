"""OpenAPI plugin: wrap an OpenAPI spec into an MCP server via the
`OpenAPIProvider`.

The plugin is the JSON-configurable entry point for the OpenAPI
integration. Spec, base URL, headers, timeout, and route mappings can
all be declared in a plugin config (useful for `plugins.json`, Horizon
config forms, or anywhere else you want to spin up an OpenAPI server
without writing Python). For scenarios that need a custom
`httpx.AsyncClient` or callables (`route_map_fn`, `mcp_component_fn`),
pass them through `__init__` directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict

from fastmcp.server.plugins.base import Plugin, PluginMeta
from fastmcp.server.plugins.openapi.provider import (
    OpenAPIProvider,
    resolve_spec_base_url,
)
from fastmcp.server.plugins.openapi.routing import (
    ComponentFn,
    MCPType,
    RouteMap,
    RouteMapFn,
)
from fastmcp.server.providers import Provider
from fastmcp.utilities.openapi.models import HttpMethod


class RouteMapDict(BaseModel):
    """JSON-serializable form of `RouteMap`.

    Converted to a real `RouteMap` when the plugin builds the provider.
    The `pattern` field is always a regex string (the typed `RouteMap`
    accepts a compiled `Pattern` too, but Config stays JSON-friendly).
    """

    model_config = ConfigDict(extra="forbid")

    mcp_type: Literal["TOOL", "RESOURCE", "RESOURCE_TEMPLATE", "EXCLUDE"]
    """Target component type. Matches `MCPType` enum values."""

    methods: list[HttpMethod] | Literal["*"] = "*"
    """HTTP methods to match (e.g. `["GET", "POST"]`) or `"*"` for any."""

    pattern: str = r".*"
    """Regex pattern matched against the route path."""

    tags: list[str] = []
    """Route tags that must all be present for this mapping to apply."""

    mcp_tags: list[str] = []
    """Tags to attach to the generated MCP component."""

    def to_route_map(self) -> RouteMap:
        methods: list[HttpMethod] | Literal["*"] = (
            "*" if self.methods == "*" else list(self.methods)
        )
        return RouteMap(
            methods=methods,
            pattern=self.pattern,
            tags=set(self.tags),
            mcp_type=MCPType[self.mcp_type],
            mcp_tags=set(self.mcp_tags),
        )


class OpenAPIConfig(BaseModel):
    """Config model for the `OpenAPI` plugin.

    Exactly one of `spec` or `spec_path` must be set — the check fires
    when the plugin builds its provider, not at Config construction,
    so that `OpenAPIConfig()` with no args still satisfies the
    plugin-framework's defaults-are-instantiable contract.

    For specs that need to be fetched from a URL at startup, fetch the
    dict in your application code and pass it via `spec=...`.
    """

    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any] | None = None
    """Inline OpenAPI spec as a dict."""

    spec_path: str | None = None
    """Path to a local JSON file containing the OpenAPI spec."""

    base_url: str | None = None
    """Base URL for the default httpx client. If omitted, the first
    server URL from the spec is used."""

    headers: dict[str, str] | None = None
    """Default headers added to every request the generated client
    makes."""

    timeout_secs: float = 30.0
    """Default timeout (seconds) for the generated httpx client."""

    mcp_names: dict[str, str] | None = None
    """Mapping from OpenAPI `operationId` to the MCP component name
    that gets generated for it."""

    tags: list[str] = []
    """Tags applied to every generated MCP component."""

    validate_output: bool = True
    """When true (default), generated tools use the OpenAPI response
    schema for output validation. Set false to accept any shape."""

    route_maps: list[RouteMapDict] = []
    """Ordered route-mapping rules. First match wins. If omitted, all
    routes become tools."""


class OpenAPI(Plugin[OpenAPIConfig]):
    """Mount an OpenAPI spec as an MCP server via a plugin.

    Everything declarative (spec, base URL, headers, route mappings)
    goes in `OpenAPIConfig`. Python-only knobs — custom `httpx.AsyncClient`,
    route-mapping callables, component customization — go in `__init__`
    kwargs.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.plugins.openapi import OpenAPI, OpenAPIConfig

        # Declarative (JSON-friendly):
        mcp = FastMCP(
            "Petstore",
            plugins=[
                OpenAPI(
                    OpenAPIConfig(
                        spec=petstore_spec,
                        base_url="https://api.example.com",
                        headers={"Authorization": "Bearer ..."},
                    )
                )
            ],
        )

        # With a custom httpx client (shared auth, retries, etc.):
        custom_client = httpx.AsyncClient(...)
        mcp = FastMCP(
            "Petstore",
            plugins=[
                OpenAPI(
                    OpenAPIConfig(spec=petstore_spec),
                    client=custom_client,
                )
            ],
        )
        ```
    """

    # "OpenAPI" is a single technical term; the auto-kebab would split
    # it into "open-api", which is uglier than the established spelling.
    meta = PluginMeta(name="openapi")

    def __init__(
        self,
        config: OpenAPIConfig | dict[str, Any] | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        route_maps: list[RouteMap] | None = None,
        route_map_fn: RouteMapFn | None = None,
        mcp_component_fn: ComponentFn | None = None,
    ) -> None:
        super().__init__(config)
        self._client_override = client
        self._route_maps_override = route_maps
        self._route_map_fn = route_map_fn
        self._mcp_component_fn = mcp_component_fn

    def providers(self) -> list[Provider]:
        spec = self._load_spec()
        if self._client_override is not None:
            client = self._client_override
            # User-supplied client: they own the lifecycle.
            owns_client: bool | None = None
        else:
            client = self._build_default_client(spec)
            # Plugin built the client, so the provider lifespan must
            # close it on shutdown (default ownership heuristic would
            # miss this since `client` is not None by the time we pass
            # it in).
            owns_client = True
        route_maps = self._resolve_route_maps()

        return [
            OpenAPIProvider(
                openapi_spec=spec,
                client=client,
                route_maps=route_maps,
                route_map_fn=self._route_map_fn,
                mcp_component_fn=self._mcp_component_fn,
                mcp_names=self.config.mcp_names,
                tags=set(self.config.tags) if self.config.tags else None,
                validate_output=self.config.validate_output,
                _owns_client=owns_client,
            )
        ]

    def _load_spec(self) -> dict[str, Any]:
        if self.config.spec is not None and self.config.spec_path is not None:
            raise ValueError(
                "OpenAPIConfig requires exactly one of `spec` or `spec_path`, not both."
            )
        if self.config.spec is not None:
            return self.config.spec
        if self.config.spec_path is not None:
            return json.loads(Path(self.config.spec_path).read_text())
        raise ValueError(
            "OpenAPIConfig requires `spec` (inline dict) or `spec_path` "
            "(local JSON file) to be set."
        )

    def _build_default_client(self, spec: dict[str, Any]) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {
            "base_url": self.config.base_url or resolve_spec_base_url(spec),
            "timeout": self.config.timeout_secs,
        }
        if self.config.headers:
            kwargs["headers"] = self.config.headers
        return httpx.AsyncClient(**kwargs)

    def _resolve_route_maps(self) -> list[RouteMap] | None:
        # Typed override wins over dict-form config so power users who
        # pass real RouteMap objects aren't shadowed by an empty default.
        if self._route_maps_override is not None:
            return self._route_maps_override
        if self.config.route_maps:
            return [rm.to_route_map() for rm in self.config.route_maps]
        return None


__all__ = ["OpenAPI", "OpenAPIConfig", "RouteMapDict"]
