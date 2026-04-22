"""Deprecation shim — vendor skills providers moved to `fastmcp.server.plugins.skills.vendor_providers`.

Prefer `Skills(SkillsConfig(vendor="<name>"))` over the individual
vendor subclasses — one plugin entry replaces the seven hardcoded
classes.
"""

import warnings

from fastmcp.exceptions import FastMCPDeprecationWarning
from fastmcp.server.plugins.skills.vendor_providers import (
    CodexSkillsProvider,
    CopilotSkillsProvider,
    CursorSkillsProvider,
    GeminiSkillsProvider,
    GooseSkillsProvider,
    OpenCodeSkillsProvider,
    VSCodeSkillsProvider,
)

warnings.warn(
    "fastmcp.server.providers.skills.vendor_providers has moved to "
    "fastmcp.server.plugins.skills.vendor_providers. Prefer the Skills "
    'plugin: `Skills(SkillsConfig(vendor="<name>"))`. This old '
    "leaf-submodule import path will be removed in a future release.",
    FastMCPDeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "CodexSkillsProvider",
    "CopilotSkillsProvider",
    "CursorSkillsProvider",
    "GeminiSkillsProvider",
    "GooseSkillsProvider",
    "OpenCodeSkillsProvider",
    "VSCodeSkillsProvider",
]
