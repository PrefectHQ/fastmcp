"""Backwards-compatibility shim — skills providers moved to `fastmcp.server.plugins.skills`.

The preferred entry point is now the `Skills` plugin:

    from fastmcp import FastMCP
    from fastmcp.server.plugins.skills import Skills, SkillsConfig

    mcp = FastMCP("skills", plugins=[Skills(SkillsConfig(vendor="claude"))])

The underlying `SkillProvider`, `SkillsDirectoryProvider`, and the
vendor subclasses (`ClaudeSkillsProvider`, `CursorSkillsProvider`, etc.)
remain importable from this package for direct composition. The
top-level import path is silent; importing from the leaf submodules
emits a `FastMCPDeprecationWarning`.
"""

from fastmcp.server.plugins.skills.claude_provider import ClaudeSkillsProvider
from fastmcp.server.plugins.skills.directory_provider import SkillsDirectoryProvider
from fastmcp.server.plugins.skills.skill_provider import SkillProvider
from fastmcp.server.plugins.skills.vendor_providers import (
    CodexSkillsProvider,
    CopilotSkillsProvider,
    CursorSkillsProvider,
    GeminiSkillsProvider,
    GooseSkillsProvider,
    OpenCodeSkillsProvider,
    VSCodeSkillsProvider,
)

# Backwards-compatibility alias preserved from the original module.
SkillsProvider = SkillsDirectoryProvider

__all__ = [
    "ClaudeSkillsProvider",
    "CodexSkillsProvider",
    "CopilotSkillsProvider",
    "CursorSkillsProvider",
    "GeminiSkillsProvider",
    "GooseSkillsProvider",
    "OpenCodeSkillsProvider",
    "SkillProvider",
    "SkillsDirectoryProvider",
    "SkillsProvider",
    "VSCodeSkillsProvider",
]
