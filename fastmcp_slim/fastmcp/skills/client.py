"""Client capability declaration for the MCP Skills extension."""

from mcp.client.extension import ClientExtension

from fastmcp.skills._constants import SKILLS_EXTENSION_ID


class SkillsClientExtension(ClientExtension):
    """Declare support for the SEP-2640 Skills extension."""

    identifier = SKILLS_EXTENSION_ID
