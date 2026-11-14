"""Client capability declaration for the MCP Events extension."""

from mcp.client.extension import ClientExtension

from fastmcp.events._constants import EVENTS_EXTENSION_ID


class EventsClientExtension(ClientExtension):
    """Declare support for the SEP-3415 Events extension."""

    identifier = EVENTS_EXTENSION_ID
