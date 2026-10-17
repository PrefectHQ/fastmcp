"""Protocol requests shared by the typed client and transparent Skills proxies."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import mcp_types
from mcp.client.caching import CacheMode

from fastmcp.client.telemetry import client_span
from fastmcp.skills.models import (
    GetSkillParams,
    GetSkillRequest,
    GetSkillResult,
    ListSkillsParams,
    ListSkillsRequest,
    ListSkillsResult,
)
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.client.client import Client

logger = get_logger(__name__)
AUTO_PAGINATION_MAX_PAGES = 250
_Meta = mcp_types.RequestParamsMeta | dict[str, object] | None


async def list_skills(
    client: Client,
    *,
    cursor: str | None = None,
    cache_mode: CacheMode = "use",
    meta: _Meta = None,
) -> ListSkillsResult:
    with client_span(
        "skills/list", "skills/list", "", session_id=client.transport.get_session_id()
    ):

        async def send() -> ListSkillsResult:
            return await client._await_with_session_monitoring(
                client.session.send_request(
                    ListSkillsRequest(
                        params=ListSkillsParams(cursor=cursor, meta=meta)
                    ),
                    ListSkillsResult,
                )
            )

        return await client._cached_fetch(
            "skills/list", cursor=cursor, cache_mode=cache_mode, send=send
        )


async def get_skill(
    client: Client,
    uri: str,
    *,
    cache_mode: CacheMode = "use",
    meta: _Meta = None,
) -> GetSkillResult:
    with client_span(
        "skills/get",
        "skills/get",
        uri,
        session_id=client.transport.get_session_id(),
        resource_uri=uri,
    ):

        async def send() -> GetSkillResult:
            return await client._await_with_session_monitoring(
                client.session.send_request(
                    GetSkillRequest(params=GetSkillParams(uri=uri, meta=meta)),
                    GetSkillResult,
                )
            )

        return await client._cached_fetch(
            "skills/get", cursor=None, params_key=uri, cache_mode=cache_mode, send=send
        )


async def list_all_skills(
    fetch_page: Callable[[str | None], Awaitable[ListSkillsResult]],
    *,
    name: str,
    max_pages: int = AUTO_PAGINATION_MAX_PAGES,
) -> ListSkillsResult:
    skills = []
    cursor: str | None = None
    seen: set[str] = set()
    ttl_ms: int | None = None
    for _ in range(max_pages):
        result = await fetch_page(cursor)
        skills.extend(result.skills)
        ttl_ms = result.ttl_ms if ttl_ms is None else min(ttl_ms, result.ttl_ms)
        if not result.next_cursor:
            break
        if result.next_cursor in seen:
            logger.warning(
                "[%s] Server returned duplicate pagination cursor %r for list_skills; stopping pagination",
                name,
                result.next_cursor,
            )
            break
        seen.add(result.next_cursor)
        cursor = result.next_cursor
    else:
        raise RuntimeError(
            f"[{name}] Reached auto-pagination limit ({max_pages} pages) "
            "for list_skills. Use list_skills_mcp() for manual pagination, "
            "or increase max_pages."
        )
    return result.model_copy(
        update={"skills": skills, "next_cursor": None, "ttl_ms": ttl_ms}
    )
