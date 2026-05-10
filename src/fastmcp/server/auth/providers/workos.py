"""Backward compatibility shim for WorkOS auth providers."""

from __future__ import annotations

import warnings

from fastmcp import settings
from fastmcp.exceptions import FastMCPDeprecationWarning

if settings.deprecation_warnings:
    warnings.warn(
        "fastmcp.server.auth.providers.workos is deprecated. "
        "Import from fastmcp.server.plugins.auth.workos.provider or "
        "fastmcp.server.plugins.auth.authkit.provider instead.",
        FastMCPDeprecationWarning,
        stacklevel=2,
    )

from fastmcp.server.plugins.auth.authkit.provider import AuthKitProvider
from fastmcp.server.plugins.auth.workos.provider import (
    WorkOSProvider,
    WorkOSTokenVerifier,
)

__all__ = ["AuthKitProvider", "WorkOSProvider", "WorkOSTokenVerifier"]
