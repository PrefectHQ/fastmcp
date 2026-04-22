"""Deprecation shim — `SkillProvider` moved to `fastmcp.server.plugins.skills.skill_provider`."""

import warnings

from fastmcp.exceptions import FastMCPDeprecationWarning
from fastmcp.server.plugins.skills.skill_provider import SkillProvider

warnings.warn(
    "fastmcp.server.providers.skills.skill_provider has moved to "
    "fastmcp.server.plugins.skills.skill_provider. Prefer the Skills "
    "plugin: `from fastmcp.server.plugins.skills import Skills`. This "
    "old leaf-submodule import path will be removed in a future release.",
    FastMCPDeprecationWarning,
    stacklevel=2,
)

__all__ = ["SkillProvider"]
