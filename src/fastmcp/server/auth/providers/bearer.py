"""Backwards compatibility shim for BearerAuthProvider.

The BearerAuthProvider class has been moved to fastmcp.server.auth.verifiers.JWTVerifier
for better organization. This module provides a backwards-compatible import.
"""

import warnings

import fastmcp
from fastmcp.server.auth.verifiers import JWKData, JWKSData, RSAKeyPair
from fastmcp.server.auth.verifiers import JWTVerifier as BearerAuthProvider

# Re-export for backwards compatibility
__all__ = ["BearerAuthProvider", "RSAKeyPair", "JWKData", "JWKSData"]

# Deprecated in 2.11
if fastmcp.settings.deprecation_warnings:
    warnings.warn(
        "The `fastmcp.server.auth.providers.bearer` module is deprecated "
        "and will be removed in a future version. "
        "Please use `fastmcp.server.auth.verifiers.JWTVerifier` "
        "instead of this module's BearerAuthProvider.",
        DeprecationWarning,
        stacklevel=2,
    )
