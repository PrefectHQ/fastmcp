"""Protocol support for the MCP Skills extension (SEP-2640)."""

from fastmcp.skills.client import SkillsClientExtension
from fastmcp.skills.models import (
    GetSkillResult,
    ListSkillsResult,
    Skill,
    SkillFrontmatter,
    SkillResource,
)

__all__ = [
    "GetSkillResult",
    "ListSkillsResult",
    "Skill",
    "SkillFrontmatter",
    "SkillResource",
    "SkillsClientExtension",
]
