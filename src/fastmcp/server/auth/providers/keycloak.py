"""Backward compatibility shim for Keycloak auth provider."""

from __future__ import annotations

import warnings

from fastmcp import settings
from fastmcp.exceptions import FastMCPDeprecationWarning

if settings.deprecation_warnings:
    warnings.warn(
        "fastmcp.server.auth.providers.keycloak is deprecated. "
        "Import from fastmcp.server.plugins.auth.keycloak.provider instead.",
        FastMCPDeprecationWarning,
        stacklevel=2,
    )

from fastmcp.server.plugins.auth.keycloak.provider import (
    KeycloakAuthProvider,
)

__all__ = ["KeycloakAuthProvider"]
