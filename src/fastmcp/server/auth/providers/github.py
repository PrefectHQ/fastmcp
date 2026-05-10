"""Backward compatibility shim for GitHub auth provider."""

from __future__ import annotations

import warnings

from fastmcp import settings
from fastmcp.exceptions import FastMCPDeprecationWarning

if settings.deprecation_warnings:
    warnings.warn(
        "fastmcp.server.auth.providers.github is deprecated. "
        "Import from fastmcp.server.plugins.auth.github.provider instead.",
        FastMCPDeprecationWarning,
        stacklevel=2,
    )

from fastmcp.server.plugins.auth.github.provider import (
    GitHubProvider,
    GitHubTokenVerifier,
)

__all__ = ["GitHubProvider", "GitHubTokenVerifier"]
