"""Backward compatibility shim for Descope auth provider."""

from __future__ import annotations

import warnings

from fastmcp import settings
from fastmcp.exceptions import FastMCPDeprecationWarning

if settings.deprecation_warnings:
    warnings.warn(
        "fastmcp.server.auth.providers.descope is deprecated. "
        "Import from fastmcp.server.plugins.auth.descope.provider instead.",
        FastMCPDeprecationWarning,
        stacklevel=2,
    )

from fastmcp.server.plugins.auth.descope.provider import DescopeProvider

__all__ = ["DescopeProvider"]
