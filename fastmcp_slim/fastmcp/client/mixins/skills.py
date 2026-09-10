"""Skills-extension methods for the FastMCP client."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import mcp_types
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
        self: Client, *, cursor: str | None = None
    ) -> ListSkillsResult:
        """Send one `skills/list` request and return its protocol result."""
        self._require_skills_extension()
        with client_span(
            "skills/list",
            "skills/list",
            "",
            session_id=self.transport.get_session_id(),
        ):
            meta = cast(
                "mcp_types.RequestParamsMeta | None", inject_trace_context(None)
            )
            request = ListSkillsRequest(
                params=ListSkillsParams(cursor=cursor, meta=meta)
            )
            return await self._await_with_session_monitoring(
                self.session.send_request(request, ListSkillsResult)
            )

    async def list_skills(
        self: Client, max_pages: int = AUTO_PAGINATION_MAX_PAGES
    ) -> list[Skill]:
        """Retrieve every page returned by `skills/list`."""
        skills: list[Skill] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()

        for _ in range(max_pages):
            result = await self.list_skills_mcp(cursor=cursor)
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

    async def get_skill(self: Client, uri: str) -> Skill:
        """Retrieve one skill entry by its `SKILL.md` resource URI."""
        self._require_skills_extension()
        with client_span(
            "skills/get",
            "skills/get",
            uri,
            session_id=self.transport.get_session_id(),
            resource_uri=uri,
        ):
            meta = cast(
                "mcp_types.RequestParamsMeta | None", inject_trace_context(None)
            )
            request = GetSkillRequest(params=GetSkillParams(uri=uri, meta=meta))
            result = await self._await_with_session_monitoring(
                self.session.send_request(request, GetSkillResult)
            )
            return result.skill
