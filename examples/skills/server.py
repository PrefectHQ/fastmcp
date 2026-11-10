"""Serve sample Skills. Run with: uv run python -m examples.skills.server"""

from pathlib import Path

from fastmcp import FastMCP
from fastmcp.skills.provider import SkillsDirectoryProvider

root = Path(__file__).parent / "sample_skills"
mcp = FastMCP("Skills Server", providers=[SkillsDirectoryProvider(root)])


if __name__ == "__main__":
    mcp.run()
