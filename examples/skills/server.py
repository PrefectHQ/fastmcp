"""Example: Skills Provider Server

This example shows how to expose agent skills as MCP resources.
Skills can be discovered, browsed, and downloaded by any MCP client.

Run this server:
    uv run python examples/skills/server.py

Then use the client example to interact with it:
    uv run python examples/skills/client.py
"""

from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.providers.skills import (
    SkillsDirectoryProvider,
)

# Create server
mcp = FastMCP("Skills Server")

# Option 1: Load a single skill
# mcp.add_provider(SkillProvider(Path.home() / ".claude/skills/pdf-processing"))

# Option 2: Load all skills from a custom directory
skills_dir = Path(__file__).parent / "sample_skills"
mcp.add_provider(SkillsDirectoryProvider(roots=skills_dir, reload=True))

# Option 3: Load skills from a platform's default location
# mcp.add_provider(ClaudeSkillsProvider())  # ~/.claude/skills/

# Option 4: Load from multiple directories (in precedence order)
# mcp.add_provider(SkillsDirectoryProvider(roots=[
#     Path.cwd() / ".claude/skills",      # Project-level first
#     Path.home() / ".claude/skills",     # User-level fallback
# ]))

# Other vendor providers available:
# - CursorSkillsProvider()   → ~/.cursor/skills/
# - VSCodeSkillsProvider()   → ~/.copilot/skills/
# - CodexSkillsProvider()    → ~/.codex/skills/
# - GeminiSkillsProvider()   → ~/.gemini/skills/
# - GooseSkillsProvider()    → ~/.config/agents/skills/
# - CopilotSkillsProvider()  → ~/.copilot/skills/
# - OpenCodeSkillsProvider() → ~/.config/opencode/skills/

if __name__ == "__main__":
    mcp.run()
