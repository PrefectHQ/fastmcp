"""Deprecation shim — `OpenAPIProvider` moved to
`fastmcp.server.plugins.openapi.provider`.

Prefer the `OpenAPI` plugin at `fastmcp.server.plugins.openapi` for new
code. `OpenAPIProvider` is still importable here for backcompat with
callers that composed it directly.
"""

import warnings

from fastmcp.exceptions import FastMCPDeprecationWarning
from fastmcp.server.plugins.openapi.provider import OpenAPIProvider

warnings.warn(
    "fastmcp.server.providers.openapi.provider has moved to "
    "fastmcp.server.plugins.openapi.provider. Prefer the OpenAPI plugin: "
    "`from fastmcp.server.plugins.openapi import OpenAPI`. This old "
    "leaf-submodule import path will be removed in a future release.",
    FastMCPDeprecationWarning,
    stacklevel=2,
)

__all__ = ["OpenAPIProvider"]
