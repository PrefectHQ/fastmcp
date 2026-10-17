"""Skills-extension methods for the FastMCP client."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import mcp_types
from mcp.client.caching import CacheMode
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from fastmcp.skills import _client
from fastmcp.skills._constants import SKILLS_EXTENSION_ID
from fastmcp.skills.models import GetSkillResult, ListSkillsResult, Skill
from fastmcp.telemetry import inject_trace_context

if TYPE_CHECKING:
    from fastmcp.client.client import Client

AUTO_PAGINATION_MAX_PAGES = _client.AUTO_PAGINATION_MAX_PAGES


def _trace_meta() -> mcp_types.RequestParamsMeta | None:
    return cast("mcp_types.RequestParamsMeta | None", inject_trace_context(None))


class ClientSkillsMixin:
    """Mixin providing SEP-2640 Skills methods for `Client`."""

    def _require_skills_extension(self: Client) -> None:
        if self.protocol_version is None:
            raise RuntimeError(
                "Client is not connected. Use the 'async with client:' context "
                "manager first."
            )
        if self.protocol_version not in MODERN_PROTOCOL_VERSIONS:
            raise RuntimeError("The Skills extension requires a modern MCP connection")

        client_extensions = self._session_kwargs.get("extensions") or {}
        if SKILLS_EXTENSION_ID not in client_extensions:
            raise RuntimeError(
                "This client did not opt into the Skills extension; pass "
                "SkillsClientExtension() in Client(extensions=[...])"
            )

        capabilities = self.server_capabilities
        server_extensions = capabilities.extensions if capabilities else None
        if not server_extensions or SKILLS_EXTENSION_ID not in server_extensions:
            raise RuntimeError("The server does not advertise the Skills extension")

    async def list_skills_mcp(
        self: Client,
        *,
        cursor: str | None = None,
        cache_mode: CacheMode = "use",
    ) -> ListSkillsResult:
        """Send one `skills/list` request and return its protocol result.

        Args:
            cursor: Optional pagination cursor from a previous result.
            cache_mode: Response-cache behavior for this call.

        Returns:
            The complete protocol result, including cache and pagination fields.

        Raises:
            RuntimeError: If the client is disconnected or Skills was not negotiated.
            MCPError: If the server rejects the request.
        """
        self._require_skills_extension()
        return await _client.list_skills(
            self, cursor=cursor, cache_mode=cache_mode, meta=_trace_meta()
        )

    async def list_skills(
        self: Client,
        max_pages: int = AUTO_PAGINATION_MAX_PAGES,
        *,
        cache_mode: CacheMode = "use",
    ) -> list[Skill]:
        """Retrieve every page returned by `skills/list`.

        Args:
            max_pages: Maximum number of pages to fetch before raising.
            cache_mode: Response-cache behavior for the first page.

        Returns:
            Every skill entry returned across the server's pages.

        Raises:
            RuntimeError: If Skills was not negotiated or the page limit is reached.
            MCPError: If the server rejects a request.
        """
        self._require_skills_extension()

        async def fetch_page(cursor: str | None) -> ListSkillsResult:
            return await self.list_skills_mcp(cursor=cursor, cache_mode=cache_mode)

        result = await _client.list_all_skills(
            fetch_page, name=self.name, max_pages=max_pages
        )
        return result.skills

    async def get_skill_mcp(
        self: Client,
        uri: str,
        *,
        cache_mode: CacheMode = "use",
    ) -> GetSkillResult:
        """Send a `skills/get` request and return its protocol result.

        Args:
            uri: URI of the skill's `SKILL.md` resource.
            cache_mode: Response-cache behavior for this call.

        Returns:
            The complete protocol result, including cache fields.

        Raises:
            RuntimeError: If the client is disconnected or Skills was not negotiated.
            MCPError: If the server rejects the request.
        """
        self._require_skills_extension()
        return await _client.get_skill(
            self, uri, cache_mode=cache_mode, meta=_trace_meta()
        )

    async def get_skill(
        self: Client,
        uri: str,
        *,
        cache_mode: CacheMode = "use",
    ) -> Skill:
        """Retrieve one skill entry by its `SKILL.md` resource URI.

        Args:
            uri: URI of the skill's `SKILL.md` resource.
            cache_mode: Response-cache behavior for this call.

        Returns:
            The skill entry returned by the server.

        Raises:
            RuntimeError: If the client is disconnected or Skills was not negotiated.
            MCPError: If the server rejects the request.
        """
        result = await self.get_skill_mcp(uri, cache_mode=cache_mode)
        return result.skill
