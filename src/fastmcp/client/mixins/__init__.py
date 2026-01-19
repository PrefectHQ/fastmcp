"""Client mixins for FastMCP."""

from fastmcp.client.mixins.prompts import ClientPromptsMixin
from fastmcp.client.mixins.resources import ClientResourcesMixin
from fastmcp.client.mixins.task_management import ClientTaskManagementMixin
from fastmcp.client.mixins.tools import ClientToolsMixin

__all__ = [
    "ClientPromptsMixin",
    "ClientResourcesMixin",
    "ClientTaskManagementMixin",
    "ClientToolsMixin",
]
