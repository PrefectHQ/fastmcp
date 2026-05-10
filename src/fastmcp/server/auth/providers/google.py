"""Backward compatibility shim for Google auth provider."""

from __future__ import annotations

import warnings

from fastmcp import settings
from fastmcp.exceptions import FastMCPDeprecationWarning

if settings.deprecation_warnings:
    warnings.warn(
        "fastmcp.server.auth.providers.google is deprecated. "
        "Import from fastmcp.server.plugins.auth.google.provider instead.",
        FastMCPDeprecationWarning,
        stacklevel=2,
    )

from fastmcp.server.plugins.auth.google.provider import (
    GoogleProvider,
    GoogleTokenVerifier,
)

__all__ = ["GoogleProvider", "GoogleTokenVerifier"]
