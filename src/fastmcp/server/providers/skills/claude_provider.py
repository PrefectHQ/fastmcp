"""Deprecation shim — `ClaudeSkillsProvider` moved to `fastmcp.server.plugins.skills.claude_provider`."""

import warnings

from fastmcp.exceptions import FastMCPDeprecationWarning
from fastmcp.server.plugins.skills.claude_provider import ClaudeSkillsProvider

warnings.warn(
    "fastmcp.server.providers.skills.claude_provider has moved to "
    "fastmcp.server.plugins.skills.claude_provider. Prefer the Skills "
    "plugin: `Skills(SkillsConfig(vendor=\"claude\"))`. This old "
    "leaf-submodule import path will be removed in a future release.",
    FastMCPDeprecationWarning,
    stacklevel=2,
)

__all__ = ["ClaudeSkillsProvider"]
