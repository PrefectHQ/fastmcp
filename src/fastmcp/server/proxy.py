"""Backwards compatibility - import from fastmcp.server.providers.proxy instead.

This module re-exports all proxy-related classes from their new location
at fastmcp.server.providers.proxy. Direct imports from this module are
deprecated and will be removed in a future version.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "fastmcp.server.proxy is deprecated. Use fastmcp.server.providers.proxy instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export everything from the new location
from fastmcp.server.providers.proxy import (  # noqa: E402
    ClientFactoryT,
    FastMCPProxy,
    ProxyClient,
    ProxyPrompt,
    ProxyProvider,
    ProxyResource,
    ProxyTemplate,
    ProxyTool,
    StatefulProxyClient,
)

__all__ = [
    "ClientFactoryT",
    "FastMCPProxy",
    "ProxyClient",
    "ProxyPrompt",
    "ProxyProvider",
    "ProxyResource",
    "ProxyTemplate",
    "ProxyTool",
    "StatefulProxyClient",
]
