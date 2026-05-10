"""Client mixins for FastMCP."""

from fastmcp_client.client.mixins.prompts import ClientPromptsMixin
from fastmcp_client.client.mixins.resources import ClientResourcesMixin
from fastmcp_client.client.mixins.task_management import ClientTaskManagementMixin
from fastmcp_client.client.mixins.tools import ClientToolsMixin

__all__ = [
    "ClientPromptsMixin",
    "ClientResourcesMixin",
    "ClientTaskManagementMixin",
    "ClientToolsMixin",
]
