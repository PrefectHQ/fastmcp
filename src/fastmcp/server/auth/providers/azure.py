"""Backward compatibility shim for Azure auth provider."""

from __future__ import annotations

import warnings

from fastmcp import settings
from fastmcp.exceptions import FastMCPDeprecationWarning

if settings.deprecation_warnings:
    warnings.warn(
        "fastmcp.server.auth.providers.azure is deprecated. "
        "Import from fastmcp.server.plugins.auth.azure.provider instead.",
        FastMCPDeprecationWarning,
        stacklevel=2,
    )

from fastmcp.server.plugins.auth.azure.provider import (
    AzureJWTVerifier,
    AzureProvider,
    EntraOBOToken,
)

__all__ = ["AzureJWTVerifier", "AzureProvider", "EntraOBOToken"]
