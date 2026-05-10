"""Backward compatibility shim for Auth0 auth provider."""

from __future__ import annotations

import warnings

from fastmcp import settings
from fastmcp.exceptions import FastMCPDeprecationWarning

if settings.deprecation_warnings:
    warnings.warn(
        "fastmcp.server.auth.providers.auth0 is deprecated. "
        "Import from fastmcp.server.plugins.auth.auth0.provider instead.",
        FastMCPDeprecationWarning,
        stacklevel=2,
    )

from fastmcp.server.plugins.auth.auth0.provider import Auth0Provider

__all__ = ["Auth0Provider"]
