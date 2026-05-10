"""Backward compatibility shim for Scalekit auth provider."""

from __future__ import annotations

import warnings

from fastmcp import settings
from fastmcp.exceptions import FastMCPDeprecationWarning

if settings.deprecation_warnings:
    warnings.warn(
        "fastmcp.server.auth.providers.scalekit is deprecated. "
        "Import from fastmcp.server.plugins.auth.scalekit.provider instead.",
        FastMCPDeprecationWarning,
        stacklevel=2,
    )

from fastmcp.server.plugins.auth.scalekit.provider import ScalekitProvider

__all__ = ["ScalekitProvider"]
