"""Skills discovery through the same proxy that serves its resources."""

import asyncio
import hashlib
from contextvars import ContextVar
from pathlib import Path
from typing import Literal

import anyio
import pytest
from mcp import MCPError
from mcp_types import INTERNAL_ERROR, INVALID_PARAMS, METHOD_NOT_FOUND

from fastmcp import Client, FastMCP
from fastmcp.server import create_proxy
from fastmcp.server.middleware import AuthMiddleware, Middleware
from fastmcp.server.middleware.caching import ResponseCachingMiddleware
from fastmcp.server.providers.aggregate import AggregateProvider
from fastmcp.server.providers.proxy import ProxyClient, ProxyProvider
from fastmcp.server.providers.skills import (
    SkillFilesProvider,
    SkillsDirectoryProvider,
)
from fastmcp.skills import SkillsClientExtension
from fastmcp.skills._constants import SKILLS_EXTENSION_ID
from fastmcp.skills.extension import SkillsExtension
from fastmcp.skills.models import GetSkillParams, GetSkillRequest, GetSkillResult


class Reads(Middleware):
    def __init__(self) -> None:
        self.uris: list[str] = []

    async def on_read_resource(self, context, call_next):
        self.uris.append(str(context.message.uri))
        return await call_next(context)


def backend(root: Path, *names: str, page_size: int | None = None) -> FastMCP:
    for name in names:
        directory = root / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_bytes(
            f"---\nname: {name}\ndescription: Use {name}.\n---\n\n# {name}\n".encode()
        )
        (directory / "guide.txt").write_bytes(b"Supporting instructions\n")
    provider = SkillsDirectoryProvider(root, experimental_skills=True)
    server = FastMCP("upstream", providers=[provider], list_page_size=page_size)
    server.add_extension(SkillsExtension(providers=[provider]))
    return server


def skills_client(server: FastMCP, *, mode: str = "auto") -> Client:
    return Client(server, mode=mode, extensions=[SkillsClientExtension()])


@pytest.mark.parametrize("manual", [False, True])
async def test_automatically_advertises_and_forwards_skills(
    tmp_path: Path, manual: bool
) -> None:
    upstream = backend(tmp_path, "review")
    reads = Reads()
    upstream.add_middleware(reads)
    if manual:
        provider = ProxyProvider(lambda: ProxyClient(upstream, mode="auto"))
        proxy = FastMCP("gateway", providers=[provider])
    else:
        proxy = create_proxy(upstream)

    async with skills_client(upstream) as direct:
        expected = await direct.get_skill("skill://review/SKILL.md")
    async with skills_client(proxy) as client:
        assert client.server_capabilities is not None
        assert SKILLS_EXTENSION_ID in (client.server_capabilities.extensions or {})
        assert await client.list_skills() == [expected]
        assert await client.get_skill(expected.uri) == expected
        assert reads.uris == []
        contents = await client.read_resource(expected.uri)
        assert "# review" in contents[0].text
    assert reads.uris == [expected.uri]


async def test_backend_without_skills_has_an_empty_catalog() -> None:
    upstream = FastMCP("ordinary-resources")

    @upstream.resource("skill://review/SKILL.md")
    def instructions() -> str:
        return "# Not an extension"

    proxy = create_proxy(upstream)
    proxy.provider_error_strategy = "raise"
    async with skills_client(proxy) as client:
        assert await client.list_skills() == []
        with pytest.raises(MCPError) as error:
            await client.get_skill("skill://review/SKILL.md")
        assert error.value.error.code == INVALID_PARAMS
        assert (await client.read_resource("skill://review/SKILL.md"))[0].text


async def test_server_without_proxies_does_not_advertise_skills() -> None:
    async with skills_client(FastMCP("plain")) as client:
        assert client.server_capabilities is not None
        assert SKILLS_EXTENSION_ID not in (client.server_capabilities.extensions or {})


async def test_namespaces_identities_and_all_manifest_uris(tmp_path: Path) -> None:
    upstream = backend(tmp_path, "review")
    proxy = FastMCP("gateway")
    proxy.mount(create_proxy(upstream), namespace="github")

    async with skills_client(upstream) as direct:
        original = await direct.get_skill("skill://review/SKILL.md")
    async with skills_client(proxy) as client:
        listed = await client.list_skills()
        skill = await client.get_skill("skill://github/review/SKILL.md")
        assert listed == [skill]
        assert skill.frontmatter == original.frontmatter
        assert isinstance(skill.resources, list)
        assert isinstance(original.resources, list)
        for namespaced, plain in zip(skill.resources, original.resources, strict=True):
            assert namespaced.uri == plain.uri.replace("skill://", "skill://github/")
            assert (namespaced.digest, namespaced.size) == (plain.digest, plain.size)
            assert await client.read_resource(namespaced.uri)


async def test_multiple_namespaced_backends_and_pagination(tmp_path: Path) -> None:
    proxy = FastMCP("gateway", list_page_size=1)
    for name in ("github", "gitlab"):
        upstream = backend(tmp_path / name, "review", "write", page_size=1)
        proxy.mount(create_proxy(upstream), namespace=name)

    async with skills_client(proxy) as client:
        page = await client.list_skills_mcp()
        assert len(page.skills) == 1
        assert page.next_cursor is not None
        skills = await client.list_skills()
        assert [skill.uri for skill in skills] == [
            "skill://github/review/SKILL.md",
            "skill://github/write/SKILL.md",
            "skill://gitlab/review/SKILL.md",
            "skill://gitlab/write/SKILL.md",
        ]
        for skill in skills:
            assert await client.get_skill(skill.uri) == skill


async def test_unknown_namespace_is_an_unknown_skill(tmp_path: Path) -> None:
    proxy = FastMCP("gateway")
    proxy.mount(create_proxy(backend(tmp_path, "review")), namespace="github")
    async with skills_client(proxy) as client:
        with pytest.raises(MCPError) as error:
            await client.get_skill("skill://gitlab/review/SKILL.md")
    assert error.value.error.code == INVALID_PARAMS


async def test_local_and_proxied_skills_share_catalog(tmp_path: Path) -> None:
    local = backend(tmp_path / "local", "local")
    remote = backend(tmp_path / "remote", "remote")
    local.mount(create_proxy(remote), namespace="upstream")
    async with skills_client(local) as client:
        assert [skill.uri for skill in await client.list_skills()] == [
            "skill://local/SKILL.md",
            "skill://upstream/remote/SKILL.md",
        ]
        assert await client.get_skill("skill://upstream/remote/SKILL.md")


class StoredSkills(SkillFilesProvider):
    files = {
        "deploy": {"SKILL.md": b"---\nname: deploy\ndescription: Use deploy.\n---\n"}
    }

    async def skill_files(self):
        return self.files


async def test_loaded_skills_share_catalog_with_proxy(tmp_path: Path) -> None:
    proxy = create_proxy(backend(tmp_path, "review"))
    stored = StoredSkills()
    proxy.add_provider(stored)
    proxy.add_extension(SkillsExtension(providers=[stored]))
    async with skills_client(proxy) as client:
        assert [skill.uri for skill in await client.list_skills()] == [
            "skill://deploy/SKILL.md",
            "skill://review/SKILL.md",
        ]


async def test_duplicate_skill_identities_are_not_chosen_arbitrarily(
    tmp_path: Path,
) -> None:
    proxy = FastMCP("gateway")
    for name in ("first", "second"):
        proxy.mount(create_proxy(backend(tmp_path / name, "review")))

    async with skills_client(proxy) as client:
        assert await client.list_skills() == []
        with pytest.raises(MCPError) as error:
            await client.get_skill("skill://review/SKILL.md")
    assert error.value.error.code == INVALID_PARAMS


async def test_get_is_authoritative_without_listing(tmp_path: Path) -> None:
    upstream = backend(tmp_path, "review")

    class EmptyListing(SkillsExtension):
        async def _handle_list(self, context, params):
            result = await super()._handle_list(context, params)
            return result.model_copy(update={"skills": []})

    provider = upstream.providers[-1]
    # Use a separate server: extension registrations are intentionally unique.
    upstream = FastMCP("unlisted", providers=[provider])
    upstream.add_extension(EmptyListing(providers=[provider]))
    async with skills_client(create_proxy(upstream)) as client:
        assert await client.list_skills() == []
        assert (
            await client.get_skill("skill://review/SKILL.md")
        ).frontmatter.name == "review"


@pytest.mark.parametrize("supporting_files", ["resources", "template"])
async def test_proxy_permissions_hide_whole_skill_without_reads(
    tmp_path: Path, supporting_files: Literal["resources", "template"]
) -> None:
    upstream = backend(tmp_path, "review")
    provider = SkillsDirectoryProvider(
        tmp_path, experimental_skills=True, supporting_files=supporting_files
    )
    upstream = FastMCP("upstream", providers=[provider])
    upstream.add_extension(SkillsExtension(providers=[provider]))
    reads = Reads()
    upstream.add_middleware(reads)
    proxy = create_proxy(upstream)
    proxy.add_middleware(
        AuthMiddleware(
            auth=lambda ctx: (
                not ctx.component.name.endswith("guide.txt")
                and not ctx.component.name.endswith("_files")
            )
        )
    )

    async with skills_client(proxy) as client:
        assert await client.list_skills() == []
        with pytest.raises(MCPError) as error:
            await client.get_skill("skill://review/SKILL.md")
        assert error.value.error.code == INVALID_PARAMS
    assert reads.uris == []


async def test_permissions_do_not_leak_between_callers(tmp_path: Path) -> None:
    allowed: ContextVar[bool] = ContextVar("skills_allowed", default=False)
    proxy = create_proxy(backend(tmp_path, "review"))
    proxy.add_middleware(AuthMiddleware(auth=lambda _: allowed.get()))

    async def catalog(permission: bool) -> list[str]:
        token = allowed.set(permission)
        try:
            async with skills_client(proxy) as client:
                return [skill.uri for skill in await client.list_skills()]
        finally:
            allowed.reset(token)

    assert await asyncio.gather(catalog(True), catalog(False)) == [
        ["skill://review/SKILL.md"],
        [],
    ]


async def test_unresponsive_backend_does_not_block_connecting() -> None:
    never = anyio.Event()

    async def unresponsive_backend() -> ProxyClient:
        await never.wait()
        raise AssertionError("the backend never responds")

    proxy = FastMCP("gateway", providers=[ProxyProvider(unresponsive_backend)])
    with anyio.fail_after(1):
        async with skills_client(proxy) as client:
            assert client.server_capabilities is not None
            assert SKILLS_EXTENSION_ID in (client.server_capabilities.extensions or {})


async def test_bad_resource_fails_only_when_read(tmp_path: Path) -> None:
    class BrokenRead(Reads):
        async def on_read_resource(self, context, call_next):
            self.uris.append(str(context.message.uri))
            raise RuntimeError("broken file")

    upstream = backend(tmp_path, "review")
    reads = BrokenRead()
    upstream.add_middleware(reads)
    async with skills_client(create_proxy(upstream)) as client:
        assert len(await client.list_skills()) == 1
        assert await client.get_skill("skill://review/SKILL.md")
        assert reads.uris == []
        with pytest.raises(MCPError) as error:
            await client.read_resource("skill://review/SKILL.md")
        assert error.value.error.code == INTERNAL_ERROR
    assert reads.uris == ["skill://review/SKILL.md"]


async def test_refreshed_manifest_is_not_paired_with_cached_old_bytes(
    tmp_path: Path,
) -> None:
    upstream = backend(tmp_path, "review")
    upstream.add_middleware(ResponseCachingMiddleware())
    proxy = create_proxy(upstream)
    proxy.add_middleware(ResponseCachingMiddleware())
    uri = "skill://review/guide.txt"
    async with skills_client(proxy) as client:
        await client.get_skill("skill://review/SKILL.md")
        assert (await client.read_resource(uri))[0].text == "Supporting instructions\n"
        (tmp_path / "review" / "guide.txt").write_bytes(b"New content\n")
        skill = await client.get_skill("skill://review/SKILL.md")
        result = await client.read_resource(uri)
        content = result[0].text.encode()
        assert content == b"New content\n"
        assert isinstance(skill.resources, list)
        entry = next(resource for resource in skill.resources if resource.uri == uri)
        assert entry.digest == f"sha256:{hashlib.sha256(content).hexdigest()}"
        assert entry.size == len(content)


async def test_backend_cache_hints_do_not_publicize_catalog(tmp_path: Path) -> None:
    class PublicHints(Middleware):
        async def on_request(self, context, call_next):
            result = await call_next(context)
            if context.method in ("skills/list", "skills/get"):
                return {**result, "ttlMs": 30_000, "cacheScope": "public"}
            return result

    upstream = backend(tmp_path, "review")
    upstream.add_middleware(PublicHints())
    async with skills_client(create_proxy(upstream)) as client:
        listed = await client.list_skills_mcp()
        fetched = await client.get_skill_mcp("skill://review/SKILL.md")
        assert listed.cache_scope == fetched.cache_scope == "private"


async def test_can_mount_ordinary_proxy_while_serving() -> None:
    upstream = FastMCP("ordinary")

    @upstream.tool
    def marker() -> str:
        return "marker"

    root = FastMCP("root")
    async with Client(root, mode="auto") as client:
        root.mount(create_proxy(upstream))
        assert [tool.name for tool in await client.list_tools()] == ["marker"]
        assert (await client.call_tool("marker")).data == "marker"


@pytest.mark.parametrize("strategy", ["warn", "raise"])
async def test_aggregate_uses_existing_provider_error_strategy(
    tmp_path: Path, strategy: Literal["warn", "raise"]
) -> None:
    class FailingCatalog(Middleware):
        async def on_request(self, context, call_next):
            if context.method == "skills/list":
                raise MCPError(code=INTERNAL_ERROR, message="backend catalog failed")
            return await call_next(context)

    good = backend(tmp_path / "good", "good")
    bad = backend(tmp_path / "bad", "bad")
    bad.add_middleware(FailingCatalog())
    aggregate = AggregateProvider(provider_error_strategy=strategy)
    for name, upstream in [("good", good), ("bad", bad)]:

        def client_factory(upstream: FastMCP = upstream) -> ProxyClient:
            return ProxyClient(upstream, mode="auto")

        aggregate.add_provider(ProxyProvider(client_factory), namespace=name)
    root = FastMCP("root", providers=[aggregate])
    root.provider_error_strategy = strategy
    async with skills_client(root) as client:
        if strategy == "warn":
            assert [skill.uri for skill in await client.list_skills()] == [
                "skill://good/good/SKILL.md"
            ]
        else:
            with pytest.raises(MCPError, match="backend catalog failed"):
                await client.list_skills()


async def test_legacy_proxy_does_not_serve_skills_methods(tmp_path: Path) -> None:
    async with skills_client(
        create_proxy(backend(tmp_path, "review")), mode="legacy"
    ) as client:
        with pytest.raises(MCPError) as error:
            await client.session.send_request(
                GetSkillRequest(params=GetSkillParams(uri="skill://review/SKILL.md")),
                GetSkillResult,
            )
    assert error.value.error.code == METHOD_NOT_FOUND
