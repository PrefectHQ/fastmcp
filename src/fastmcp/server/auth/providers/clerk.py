"""Backward compatibility shim for Clerk auth provider."""

from __future__ import annotations

import warnings

from fastmcp import settings
from fastmcp.exceptions import FastMCPDeprecationWarning

if settings.deprecation_warnings:
    warnings.warn(
        "fastmcp.server.auth.providers.clerk is deprecated. "
        "Import from fastmcp.server.plugins.auth.clerk.provider instead.",
        FastMCPDeprecationWarning,
        stacklevel=2,
    )

from fastmcp.server.plugins.auth.clerk.provider import (
    ClerkProvider,
    ClerkTokenVerifier,
)

__all__ = ["ClerkProvider", "ClerkTokenVerifier"]
