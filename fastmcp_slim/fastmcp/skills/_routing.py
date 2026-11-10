"""How Skills discovery checks a manifest file against the server's reads."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp.exceptions import AuthorizationError

if TYPE_CHECKING:
    from fastmcp.resources.base import Resource
    from fastmcp.resources.template import ResourceTemplate
    from fastmcp.server.server import FastMCP


async def _resolve_read(
    server: FastMCP, uri: str
) -> Resource | ResourceTemplate | None:
    """Return the component a read of `uri` would use, without reading it."""
    resource, exists = await server._lookup_resource(uri)
    if resource is not None or exists:
        # A hidden concrete resource blocks the template, as in reads.
        return resource
    return await server.get_resource_template(uri)


async def _may_read(server: FastMCP, uri: str) -> bool:
    try:
        await server._check_resource_access(uri)
    except AuthorizationError:
        return False
    return True
