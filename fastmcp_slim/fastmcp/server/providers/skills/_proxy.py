"""Skills discovery forwarded through a proxy's backend client."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from mcp import MCPError
from mcp.server.context import ServerRequestContext
from mcp_types import INVALID_PARAMS, METHOD_NOT_FOUND

from fastmcp.client.client import Client
from fastmcp.server.dependencies import get_context
from fastmcp.server.providers.proxy import (
    _PROXY_TRANSPORT_ERRORS,
    _forwardable_request_meta,
    _proxy_upstream_error,
    _session_request_meta,
)
from fastmcp.skills._source import _Catalog, _Lookup, _SkillSource
from fastmcp.skills.models import (
    GetSkillParams,
    GetSkillRequest,
    GetSkillResult,
    ListSkillsParams,
    ListSkillsRequest,
    ListSkillsResult,
    Skill,
)

if TYPE_CHECKING:
    from fastmcp.server.providers.proxy import ProxyProvider


class _ProxySkillSource(_SkillSource):
    def __init__(self, provider: ProxyProvider) -> None:
        self.provider = provider

    async def _owns_skill_resource(self, uri: str) -> bool:
        if not self.provider._skills_resources_live:
            return False
        return (
            await self.provider._get_resource(uri) is not None
            or await self.provider._get_resource_template(uri) is not None
        )

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[Client]:
        try:
            client = await self.provider._get_client()
            async with client:
                yield client
        except _PROXY_TRANSPORT_ERRORS as error:
            raise _proxy_upstream_error(error) from error

    async def _list_skill_entries(
        self, context: ServerRequestContext[Any, Any]
    ) -> _Catalog:
        meta = _session_request_meta(_forwardable_request_meta(get_context()))
        skills: list[Skill] = []
        cursor: str | None = None
        seen: set[str] = set()
        async with self._client() as client:
            while True:
                request = ListSkillsRequest(
                    params=ListSkillsParams(cursor=cursor, meta=meta)
                )
                try:
                    page = await client._await_with_session_monitoring(
                        client.session.send_request(request, ListSkillsResult)
                    )
                except MCPError as error:
                    if error.error.code != METHOD_NOT_FOUND:
                        raise
                    return _Catalog([], ())
                self.provider._skills_resources_live = True
                skills.extend(page.skills)
                if not page.next_cursor or page.next_cursor in seen:
                    break
                seen.add(page.next_cursor)
                cursor = page.next_cursor
        return _Catalog(
            skills,
            tuple(skill.uri for skill in skills),
            {skill.uri: self.provider.client_factory for skill in skills},
        )

    async def _get_skill_entry(
        self, uri: str, context: ServerRequestContext[Any, Any]
    ) -> _Lookup:
        meta = _session_request_meta(_forwardable_request_meta(get_context()))
        request = GetSkillRequest(params=GetSkillParams(uri=uri, meta=meta))
        async with self._client() as client:
            try:
                result = await client._await_with_session_monitoring(
                    client.session.send_request(request, GetSkillResult)
                )
            except MCPError as error:
                if error.error.code not in (INVALID_PARAMS, METHOD_NOT_FOUND):
                    raise
                return _Lookup()
        self.provider._skills_resources_live = True
        return _Lookup(
            matches=1, skill=result.skill, owner=self.provider.client_factory
        )
