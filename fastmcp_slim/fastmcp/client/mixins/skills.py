"""Skills-extension methods for the FastMCP client."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import mcp_types
from mcp.client.caching import CacheMode
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from fastmcp.client.telemetry import client_span
from fastmcp.skills._constants import SKILLS_EXTENSION_ID
from fastmcp.skills.models import (
    GetSkillParams,
    GetSkillRequest,
    GetSkillResult,
    ListSkillsParams,
    ListSkillsRequest,
    ListSkillsResult,
    Skill,
)
from fastmcp.telemetry import inject_trace_context
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.client.client import Client

logger = get_logger(__name__)

AUTO_PAGINATION_MAX_PAGES = 250


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
        with client_span(
            "skills/list",
            "skills/list",
            "",
            session_id=self.transport.get_session_id(),
        ):

            async def _send() -> ListSkillsResult:
                request = ListSkillsRequest(
                    params=ListSkillsParams(cursor=cursor, meta=_trace_meta())
                )
                return await self._await_with_session_monitoring(
                    self.session.send_request(request, ListSkillsResult)
                )

            return await self._cached_fetch(
                "skills/list",
                cursor=cursor,
                cache_mode=cache_mode,
                send=_send,
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
        skills: list[Skill] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()

        for _ in range(max_pages):
            result = await self.list_skills_mcp(cursor=cursor, cache_mode=cache_mode)
            skills.extend(result.skills)
            if not result.next_cursor:
                break
            if result.next_cursor in seen_cursors:
                logger.warning(
                    "[%s] Server returned duplicate pagination cursor %r for "
                    "list_skills; stopping pagination",
                    self.name,
                    result.next_cursor,
                )
                break
            seen_cursors.add(result.next_cursor)
            cursor = result.next_cursor
        else:
            raise RuntimeError(
                f"[{self.name}] Reached auto-pagination limit ({max_pages} pages) "
                "for list_skills. Use list_skills_mcp() for manual pagination, "
                "or increase max_pages."
            )

        return skills

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
        with client_span(
            "skills/get",
            "skills/get",
            uri,
            session_id=self.transport.get_session_id(),
            resource_uri=uri,
        ):

            async def _send() -> GetSkillResult:
                request = GetSkillRequest(
                    params=GetSkillParams(uri=uri, meta=_trace_meta())
                )
                return await self._await_with_session_monitoring(
                    self.session.send_request(request, GetSkillResult)
                )

            return await self._cached_fetch(
                "skills/get",
                cursor=None,
                params_key=uri,
                cache_mode=cache_mode,
                send=_send,
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
