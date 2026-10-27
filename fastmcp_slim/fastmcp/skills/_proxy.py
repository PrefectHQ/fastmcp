"""Skills discovery forwarded to the backend of a `ProxyProvider`."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, METHOD_NOT_FOUND

from fastmcp.client.client import Client
from fastmcp.server.dependencies import get_context
from fastmcp.server.providers.base import Provider
from fastmcp.server.providers.proxy import (
    _PROXY_TRANSPORT_ERRORS,
    ProxyProvider,
    ProxyResource,
    ProxyTemplate,
    _forwardable_request_meta,
    _proxy_upstream_error,
    _session_request_meta,
)
from fastmcp.skills._routing import _may_read, _resolve_read
from fastmcp.skills.models import (
    GetSkillParams,
    GetSkillRequest,
    GetSkillResult,
    ListSkillsParams,
    ListSkillsRequest,
    ListSkillsResult,
    MCPSkill,
)


class _ProxiedSkills:
    """A skill source that asks a proxy's backend for its Skills entries.

    Entries pass through unchanged. One is kept only when this server serves
    every file in its manifest from the same backend and the caller may read
    each of them, so discovery never advertises a file the caller can't fetch.
    Discovery checks routing without reading any file.
    """

    def __init__(self, provider: ProxyProvider) -> None:
        self._provider = provider

    def _validate_skill_source(self) -> None:
        return None

    def _owns_skill_resource(self, uri: str) -> bool:
        return uri.startswith("skill://")

    async def _skill_uris(self, uri: str | None = None) -> Sequence[str]:
        if uri is None:
            return [entry.uri for entry in await self._listed()]
        entry = await self._fetched(uri)
        return [] if entry is None else [entry.uri]

    async def _list_skill_entries(
        self, context: ServerRequestContext[Any, Any]
    ) -> Sequence[MCPSkill]:
        return [entry for entry in await self._listed() if await self._is_served(entry)]

    async def _get_skill_entry(
        self, uri: str, context: ServerRequestContext[Any, Any]
    ) -> MCPSkill | None:
        entry = await self._fetched(uri)
        if entry is None or not await self._is_served(entry):
            return None
        return entry

    async def _listed(self) -> list[MCPSkill]:
        # skills/list asks for identities and entries separately; one backend
        # call per request answers both.
        state = get_context()._request_state
        key = f"_fastmcp_proxied_skills:{id(self._provider)}"
        if key not in state:
            state[key] = await self._list_backend()
        return state[key]

    async def _fetched(self, uri: str) -> MCPSkill | None:
        state = get_context()._request_state
        key = f"_fastmcp_proxied_skill:{id(self._provider)}:{uri}"
        if key not in state:
            state[key] = await self._get_backend(uri)
        return state[key]

    @asynccontextmanager
    async def _client(self) -> AsyncGenerator[Client]:
        try:
            client = await self._provider._get_client()
            async with client:
                yield client
        except _PROXY_TRANSPORT_ERRORS as error:
            raise _proxy_upstream_error(error) from error

    async def _list_backend(self) -> list[MCPSkill]:
        meta = _session_request_meta(_forwardable_request_meta(get_context()))
        entries: list[MCPSkill] = []
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
                    if error.error.code == METHOD_NOT_FOUND:
                        return []
                    raise
                entries.extend(page.skills)
                if not page.next_cursor or page.next_cursor in seen:
                    return entries
                seen.add(page.next_cursor)
                cursor = page.next_cursor

    async def _get_backend(self, uri: str) -> MCPSkill | None:
        meta = _session_request_meta(_forwardable_request_meta(get_context()))
        request = GetSkillRequest(params=GetSkillParams(uri=uri, meta=meta))
        async with self._client() as client:
            try:
                result = await client._await_with_session_monitoring(
                    client.session.send_request(request, GetSkillResult)
                )
            except MCPError as error:
                if error.error.code in (INVALID_PARAMS, METHOD_NOT_FOUND):
                    return None
                raise
        return result.skill

    async def _is_served(self, entry: MCPSkill) -> bool:
        if entry.resources == "dynamic":
            return False
        server = get_context().fastmcp
        for record in entry.resources:
            component = await _resolve_read(server, record.uri)
            # Creating a resource from a proxied template reads the backend, so
            # the template itself counts.
            if not (
                isinstance(component, ProxyResource | ProxyTemplate)
                and component._client_factory is self._provider.client_factory
                and await _may_read(server, record.uri)
            ):
                return False
        return True


def _proxied_source(provider: Provider) -> _ProxiedSkills | None:
    if (
        isinstance(provider, ProxyProvider)
        and provider._skills
        and not provider.transforms
    ):
        return _ProxiedSkills(provider)
    return None
