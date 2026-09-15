from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

import pytest
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    TextResourceContents,
)
from pydantic import AnyUrl

from fastmcp import Client, FastMCP
from fastmcp.resources.base import Resource
from fastmcp.resources.function_resource import FunctionResource
from fastmcp.server.providers import Provider
from fastmcp.server.transforms import Namespace
from fastmcp.skills import Skill, SkillsClientExtension
from fastmcp.skills._constants import SKILLS_EXTENSION_ID
from fastmcp.skills.extension import SkillsExtension
from fastmcp.skills.models import (
    GetSkillParams,
    GetSkillRequest,
    GetSkillResult,
    ListSkillsParams,
    ListSkillsResult,
)
from fastmcp.utilities.versions import VersionSpec

SKILL_CONTENT = "# Review\n"
DIGEST = f"sha256:{hashlib.sha256(SKILL_CONTENT.encode()).hexdigest()}"


def make_skill(name: str) -> Skill:
    uri = f"skill://{name}/SKILL.md"
    return Skill.model_validate(
        {
            "uri": uri,
            "frontmatter": {"name": name, "description": f"Use {name}"},
            "resources": [
                {"uri": uri, "digest": DIGEST, "size": len(SKILL_CONTENT.encode())}
            ],
        }
    )


class MemorySkillProvider(Provider):
    def __init__(
        self,
        *,
        listed: Sequence[Skill],
        addressable: Sequence[Skill] | None = None,
    ) -> None:
        super().__init__()
        self.listed = list(listed)
        self.addressable = {
            skill.uri: skill
            for skill in (addressable if addressable is not None else listed)
        }
        self.contexts: list[ServerRequestContext[Any, Any]] = []

    async def _list_skill_entries(
        self, context: ServerRequestContext[Any, Any]
    ) -> Sequence[Skill]:
        self.contexts.append(context)
        return self.listed

    async def _get_skill_entry(
        self, uri: str, context: ServerRequestContext[Any, Any]
    ) -> Skill | None:
        self.contexts.append(context)
        return self.addressable.get(uri)

    async def _get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        skill = self.addressable.get(uri)
        if skill is None:
            return None
        return FunctionResource(
            uri=AnyUrl(uri),
            name=skill.frontmatter.name,
            mime_type="text/markdown",
            fn=lambda: SKILL_CONTENT,
        )


class CachedSkillsExtension(SkillsExtension):
    async def _handle_list(
        self,
        context: ServerRequestContext[Any, Any],
        params: ListSkillsParams,
    ) -> ListSkillsResult:
        result = await super()._handle_list(context, params)
        return result.model_copy(update={"ttl_ms": 60_000, "cache_scope": "public"})

    async def _handle_get(
        self,
        context: ServerRequestContext[Any, Any],
        params: GetSkillParams,
    ) -> GetSkillResult:
        result = await super()._handle_get(context, params)
        return result.model_copy(update={"ttl_ms": 60_000, "cache_scope": "public"})


def make_server(
    provider: MemorySkillProvider,
    *,
    page_size: int | None = None,
    cached: bool = False,
) -> FastMCP:
    server = FastMCP("skills", providers=[provider], list_page_size=page_size)
    extension_type = CachedSkillsExtension if cached else SkillsExtension
    server.add_extension(extension_type(providers=[provider]))
    return server


def skills_client(server: FastMCP) -> Client:
    return Client(
        server,
        mode="auto",
        extensions=[SkillsClientExtension()],
    )


async def test_advertises_skills_and_resources_capabilities() -> None:
    server = make_server(MemorySkillProvider(listed=[]))

    async with skills_client(server) as client:
        assert client.server_capabilities is not None
        assert client.server_capabilities.resources is not None
        assert client.server_capabilities.extensions is not None
        assert client.server_capabilities.extensions[SKILLS_EXTENSION_ID] == {}


async def test_list_skills_auto_paginates_in_uri_order() -> None:
    provider = MemorySkillProvider(
        listed=[make_skill("zeta"), make_skill("alpha"), make_skill("middle")]
    )
    server = make_server(provider, page_size=1)

    async with skills_client(server) as client:
        first = await client.list_skills_mcp()
        all_skills = await client.list_skills()

    assert [skill.frontmatter.name for skill in first.skills] == ["alpha"]
    assert first.next_cursor is not None
    assert [skill.frontmatter.name for skill in all_skills] == [
        "alpha",
        "middle",
        "zeta",
    ]


async def test_list_skills_enforces_page_limit() -> None:
    provider = MemorySkillProvider(listed=[make_skill("alpha"), make_skill("beta")])
    server = make_server(provider, page_size=1)

    async with skills_client(server) as client:
        with pytest.raises(RuntimeError, match="Reached auto-pagination limit"):
            await client.list_skills(max_pages=1)


async def test_get_skill_is_authoritative_for_unlisted_skill() -> None:
    hidden_from_list = make_skill("direct-only")
    provider = MemorySkillProvider(listed=[], addressable=[hidden_from_list])
    server = make_server(provider)

    async with skills_client(server) as client:
        assert await client.list_skills() == []
        skill = await client.get_skill(hidden_from_list.uri)
        contents = await client.read_resource(hidden_from_list.uri)

    assert skill == hidden_from_list
    assert len(contents) == 1
    assert isinstance(contents[0], TextResourceContents)
    assert contents[0].text == SKILL_CONTENT
    assert isinstance(skill.resources, list)
    payload = contents[0].text.encode()
    assert skill.resources[0].size == len(payload)
    assert skill.resources[0].digest == (
        f"sha256:{hashlib.sha256(payload).hexdigest()}"
    )
    assert [context.method for context in provider.contexts] == [
        "skills/list",
        "skills/get",
    ]


async def test_skill_protocol_methods_preserve_cache_hints() -> None:
    skill = make_skill("review")
    server = make_server(MemorySkillProvider(listed=[skill]), cached=True)

    async with skills_client(server) as client:
        listed = await client.list_skills_mcp()
        fetched = await client.get_skill_mcp(skill.uri)

    assert listed.ttl_ms == fetched.ttl_ms == 60_000
    assert listed.cache_scope == fetched.cache_scope == "public"


async def test_skill_client_cache_serves_list_and_keys_get_by_uri() -> None:
    alpha = make_skill("alpha")
    beta = make_skill("beta")
    provider = MemorySkillProvider(listed=[alpha, beta])
    server = make_server(provider, cached=True)
    client = Client(
        server,
        mode="auto",
        extensions=[SkillsClientExtension()],
        cache=True,
    )

    async with client:
        first_listing = await client.list_skills()
        cached_listing = await client.list_skills()
        first_alpha = await client.get_skill(alpha.uri)
        cached_alpha = await client.get_skill(alpha.uri)
        fetched_beta = await client.get_skill(beta.uri)

    assert first_listing == cached_listing == [alpha, beta]
    assert first_alpha == cached_alpha == alpha
    assert fetched_beta == beta

    assert [context.method for context in provider.contexts] == [
        "skills/list",
        "skills/get",
        "skills/get",
    ]


async def test_skill_client_cache_mode_bypass_reaches_server() -> None:
    skill = make_skill("review")
    provider = MemorySkillProvider(listed=[skill])
    server = make_server(provider, cached=True)
    client = Client(
        server,
        mode="auto",
        extensions=[SkillsClientExtension()],
        cache=True,
    )

    async with client:
        await client.get_skill(skill.uri)
        await client.get_skill(skill.uri, cache_mode="bypass")

    assert [context.method for context in provider.contexts] == [
        "skills/get",
        "skills/get",
    ]


async def test_get_unknown_skill_returns_invalid_params() -> None:
    server = make_server(MemorySkillProvider(listed=[]))

    async with skills_client(server) as client:
        with pytest.raises(MCPError) as exc_info:
            await client.get_skill("skill://missing/SKILL.md")

    assert exc_info.value.error.code == INVALID_PARAMS


async def test_list_rejects_invalid_cursor() -> None:
    server = make_server(
        MemorySkillProvider(listed=[make_skill("review")]), page_size=1
    )

    async with skills_client(server) as client:
        with pytest.raises(MCPError) as exc_info:
            await client.list_skills_mcp(cursor="not-a-cursor")

    assert exc_info.value.error.code == INVALID_PARAMS


async def test_server_accepts_raw_request_after_capability_discovery() -> None:
    skill = make_skill("review")
    server = make_server(MemorySkillProvider(listed=[skill]))

    async with Client(server, mode="auto") as client:
        assert client.server_capabilities is not None
        assert client.server_capabilities.extensions is not None
        assert SKILLS_EXTENSION_ID in client.server_capabilities.extensions
        request = GetSkillRequest(params=GetSkillParams(uri=skill.uri))
        result = await client.session.send_request(request, GetSkillResult)

    assert result.skill == skill


async def test_server_rejects_skills_method_on_legacy_protocol() -> None:
    skill = make_skill("review")
    server = make_server(MemorySkillProvider(listed=[skill]))

    async with Client(server, mode="legacy") as client:
        request = GetSkillRequest(params=GetSkillParams(uri=skill.uri))
        with pytest.raises(MCPError) as exc_info:
            await client.session.send_request(request, GetSkillResult)

    assert exc_info.value.error.code == METHOD_NOT_FOUND


async def test_client_requires_explicit_opt_in() -> None:
    server = make_server(MemorySkillProvider(listed=[]))

    async with Client(server, mode="auto") as client:
        with pytest.raises(RuntimeError, match="did not opt into"):
            await client.list_skills()


async def test_client_requires_connection() -> None:
    client = skills_client(make_server(MemorySkillProvider(listed=[])))

    with pytest.raises(RuntimeError, match="Client is not connected"):
        await client.list_skills()


async def test_client_rejects_server_without_extension() -> None:
    server = FastMCP("no-skills")

    async with skills_client(server) as client:
        with pytest.raises(RuntimeError, match="server does not advertise"):
            await client.list_skills()


def test_extension_requires_provider_registered_directly() -> None:
    provider = MemorySkillProvider(listed=[])
    server = FastMCP("skills")

    with pytest.raises(ValueError, match="registered directly"):
        server.add_extension(SkillsExtension(providers=[provider]))


def test_extension_requires_at_least_one_provider() -> None:
    with pytest.raises(ValueError, match="at least one provider"):
        SkillsExtension(providers=[])


def test_extension_rejects_duplicate_provider_reference() -> None:
    provider = MemorySkillProvider(listed=[])

    with pytest.raises(ValueError, match="providers must be unique"):
        SkillsExtension(providers=[provider, provider])


def test_extension_rejects_provider_without_source_contract() -> None:
    provider = Provider()
    server = FastMCP("skills", providers=[provider])

    with pytest.raises(TypeError, match="not a SkillsExtension source"):
        server.add_extension(SkillsExtension(providers=[provider]))


def test_extension_rejects_transformed_provider() -> None:
    provider = MemorySkillProvider(listed=[])
    provider.add_transform(Namespace("docs"))
    server = FastMCP("skills", providers=[provider])

    with pytest.raises(ValueError, match="transformed providers"):
        server.add_extension(SkillsExtension(providers=[provider]))


def test_extension_rejects_server_level_transform() -> None:
    provider = MemorySkillProvider(listed=[])
    server = FastMCP("skills", providers=[provider], transforms=[Namespace("docs")])

    with pytest.raises(ValueError, match="server-level transforms"):
        server.add_extension(SkillsExtension(providers=[provider]))


async def test_extension_rejects_transform_added_after_registration() -> None:
    provider = MemorySkillProvider(listed=[])
    server = make_server(provider)
    provider.add_transform(Namespace("docs"))

    async with skills_client(server) as client:
        with pytest.raises(MCPError) as exc_info:
            await client.list_skills()

    assert exc_info.value.error.code == INTERNAL_ERROR


async def test_duplicate_uris_are_omitted_from_list_and_get(
    caplog: pytest.LogCaptureFixture,
) -> None:
    duplicate = make_skill("duplicate")
    server = FastMCP("skills")
    first = MemorySkillProvider(listed=[duplicate])
    second = MemorySkillProvider(listed=[duplicate])
    server.add_provider(first)
    server.add_provider(second)
    server.add_extension(SkillsExtension(providers=[first, second]))

    async with skills_client(server) as client:
        assert await client.list_skills() == []
        with pytest.raises(MCPError) as exc_info:
            await client.get_skill(duplicate.uri)

    assert exc_info.value.error.code == INVALID_PARAMS
    assert "Omitting duplicate Skills entries" in caplog.text
