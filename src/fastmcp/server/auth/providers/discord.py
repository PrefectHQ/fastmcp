"""Backward compatibility shim for Discord auth provider."""

from __future__ import annotations

import warnings

from fastmcp import settings
from fastmcp.exceptions import FastMCPDeprecationWarning

if settings.deprecation_warnings:
    warnings.warn(
        "fastmcp.server.auth.providers.discord is deprecated. "
        "Import from fastmcp.server.plugins.auth.discord.provider instead.",
        FastMCPDeprecationWarning,
        stacklevel=2,
    )

from fastmcp.server.plugins.auth.discord.provider import (
    DiscordProvider,
    DiscordTokenVerifier,
)

__all__ = ["DiscordProvider", "DiscordTokenVerifier"]
