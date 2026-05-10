"""Backward compatibility shim for Supabase auth provider."""

from __future__ import annotations

import warnings

from fastmcp import settings
from fastmcp.exceptions import FastMCPDeprecationWarning

if settings.deprecation_warnings:
    warnings.warn(
        "fastmcp.server.auth.providers.supabase is deprecated. "
        "Import from fastmcp.server.plugins.auth.supabase.provider instead.",
        FastMCPDeprecationWarning,
        stacklevel=2,
    )

from fastmcp.server.plugins.auth.supabase.provider import SupabaseProvider

__all__ = ["SupabaseProvider"]
