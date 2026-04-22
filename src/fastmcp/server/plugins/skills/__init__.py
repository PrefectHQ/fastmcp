"""Skills plugin — expose agent skill folders as MCP resources.

    from fastmcp import FastMCP
    from fastmcp.server.plugins.skills import Skills, SkillsConfig

    mcp = FastMCP("skills", plugins=[Skills(SkillsConfig(vendor="claude"))])

The underlying `SkillProvider` and `SkillsDirectoryProvider` classes
live on `.skill_provider` and `.directory_provider` submodules for
direct-composition use cases; the plugin is the canonical entry point.
"""

from fastmcp.server.plugins.skills.plugin import Skills, SkillsConfig

__all__ = ["Skills", "SkillsConfig"]
