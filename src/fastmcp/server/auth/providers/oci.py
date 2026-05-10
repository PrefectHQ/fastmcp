"""Backward compatibility shim for OCI auth provider."""

from __future__ import annotations

import warnings

from fastmcp import settings
from fastmcp.exceptions import FastMCPDeprecationWarning

if settings.deprecation_warnings:
    warnings.warn(
        "fastmcp.server.auth.providers.oci is deprecated. "
        "Import from fastmcp.server.plugins.auth.oci.provider instead.",
        FastMCPDeprecationWarning,
        stacklevel=2,
    )

from fastmcp.server.plugins.auth.oci.provider import OCIProvider

__all__ = ["OCIProvider"]
