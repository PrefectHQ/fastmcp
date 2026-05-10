"""Backward compatibility shim for PropelAuth auth provider."""

from __future__ import annotations

import warnings

from fastmcp import settings
from fastmcp.exceptions import FastMCPDeprecationWarning

if settings.deprecation_warnings:
    warnings.warn(
        "fastmcp.server.auth.providers.propelauth is deprecated. "
        "Import from fastmcp.server.plugins.auth.propelauth.provider instead.",
        FastMCPDeprecationWarning,
        stacklevel=2,
    )

from fastmcp.server.plugins.auth.propelauth.provider import (
    PropelAuthProvider,
    PropelAuthTokenIntrospectionOverrides,
)

__all__ = ["PropelAuthProvider", "PropelAuthTokenIntrospectionOverrides"]
