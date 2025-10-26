"""Backwards compatibility shim for BearerAuthProvider.

The BearerAuthProvider class has been moved to fastmcp.server.auth.providers.jwt.JWTVerifier
for better organization. This module provides a backwards-compatible import.
"""

import warnings

import fastmcp
from fastmcp.server.auth.providers.jwt import JWKData, JWKSData, RSAKeyPair
from fastmcp.server.auth.providers.jwt import JWTVerifier as BearerAuthProvider

# Re-export for backwards compatibility
__all__ = ["BearerAuthProvider", "JWKData", "JWKSData", "RSAKeyPair"]

# Deprecated in 2.11
if fastmcp.settings.deprecation_warnings:
    warnings.warn(
        "The `fastmcp.server.auth.providers.bearer` module is deprecated "
        "and will be removed in a future version. "
        "Please use `fastmcp.server.auth.providers.jwt.JWTVerifier` "
        "instead of this module's BearerAuthProvider.",
        DeprecationWarning,
        stacklevel=2,
    )
