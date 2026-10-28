"""Skills discovery forwarded to a proxy's backend."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import pytest
from mcp.shared.exceptions import MCPError
from mcp_types import TextResourceContents

from fastmcp import Client, FastMCP
from fastmcp.server import create_proxy
from fastmcp.server.middleware import Middleware
from fastmcp.server.middleware.authorization import AuthMiddleware
from fastmcp.server.middleware.caching import ResponseCachingMiddleware
from fastmcp.server.providers.aggregate import ProviderErrorStrategy
from fastmcp.server.providers.proxy import ProxyClient, ProxyProvider
from fastmcp.server.providers.skills import SkillsDirectoryProvider
from fastmcp.skills import Skill, SkillsClientExtension
from fastmcp.skills._constants import SKILLS_EXTENSION_ID
from fastmcp.skills.extension import SkillsExtension
from fastmcp.skills.provider import SkillCatalog, SkillCatalogProvider
from fastmcp.utilities.tests import temporary_settings

REVIEW = Skill(
    name="review",
    description="Review changes",
    instructions="# Review\n",
    files={"notes.md": "Read the diff first.\n"},
)
DEPLOY = Skill(name="deploy", description="Deploy the app", instructions="# Deploy\n")


class Reads(Middleware):
    def __init__(self) -> None:
        self.uris: list[str] = []

    async def on_read_resource(self, context, call_next):
        self.uris.append(str(context.message.uri))
        return await call_next(context)


def backend(*skills: Skill, page_size: int | None = None) -> FastMCP:
    return FastMCP(
        "backend",
        providers=[SkillCatalogProvider(list(skills))],
        list_page_size=page_size,
    )


def gateway(
    upstream: FastMCP, *, middleware: list[Middleware] | None = None
) -> FastMCP:
    server = FastMCP("gateway", middleware=middleware)
    server.add_provider(
        ProxyProvider(lambda: ProxyClient(upstream, mode="auto"), skills=True)
    )
    return server


def skills_client(server: FastMCP) -> Client:
    return Client(server, mode="auto", extensions=[SkillsClientExtension()])


async def test_forwards_backend_skills_without_reading_files() -> None:
    upstream = backend(REVIEW)
    reads = Reads()
    upstream.add_middleware(reads)

    async with skills_client(upstream) as direct:
        expected = await direct.get_skill("skill://review/SKILL.md")
    async with skills_client(gateway(upstream)) as client:
        assert client.server_capabilities is not None
        assert SKILLS_EXTENSION_ID in (client.server_capabilities.extensions or {})
        assert await client.list_skills() == [expected]
        assert await client.get_skill(expected.uri) == expected
        assert reads.uris == []
        (content,) = await client.read_resource(expected.uri)
        assert isinstance(content, TextResourceContents)
        assert "# Review" in content.text


async def test_files_behind_a_backend_template_are_checked_without_reads(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "review"
    folder.mkdir()
    (folder / "SKILL.md").write_bytes(
        b"---\nname: review\ndescription: Review changes\n---\n# Review\n"
    )
    (folder / "notes.md").write_bytes(b"Read the diff first.\n")
    upstream = FastMCP(
        "backend",
        providers=[SkillsDirectoryProvider(tmp_path, experimental_skills=True)],
    )
    reads = Reads()
    upstream.add_middleware(reads)

    async with skills_client(upstream) as direct:
        expected = await direct.list_skills()
    assert [skill.uri for skill in expected] == ["skill://review/SKILL.md"]
    async with skills_client(gateway(upstream)) as client:
        assert await client.list_skills() == expected
    assert reads.uris == []


async def test_proxy_without_skills_flag_does_not_advertise_skills() -> None:
    async with skills_client(create_proxy(backend(REVIEW))) as client:
        assert client.server_capabilities is not None
        assert SKILLS_EXTENSION_ID not in (client.server_capabilities.extensions or {})


async def test_proxy_without_skills_flag_is_not_forwarded() -> None:
    upstream = backend(REVIEW)
    server = FastMCP("gateway", providers=[SkillCatalogProvider([DEPLOY])])
    server.add_provider(ProxyProvider(lambda: ProxyClient(upstream, mode="auto")))
    server.add_extension(SkillsExtension())

    async with skills_client(server) as client:
        listed = await client.list_skills()
    assert [skill.uri for skill in listed] == ["skill://deploy/SKILL.md"]


async def test_backend_without_skills_has_an_empty_catalog() -> None:
    upstream = FastMCP("plain")

    @upstream.resource("skill://review/SKILL.md")
    def instructions() -> str:
        return "# Not published through the extension"

    async with skills_client(gateway(upstream)) as client:
        assert await client.list_skills() == []
        with pytest.raises(MCPError, match="Unknown skill URI"):
            await client.get_skill("skill://review/SKILL.md")


async def test_follows_backend_pagination() -> None:
    async with skills_client(gateway(backend(REVIEW, DEPLOY, page_size=1))) as client:
        listed = await client.list_skills()
    assert [skill.uri for skill in listed] == [
        "skill://deploy/SKILL.md",
        "skill://review/SKILL.md",
    ]


class ListSkillsRequests(Middleware):
    def __init__(self) -> None:
        self.count = 0

    async def on_request(self, context, call_next):
        if context.method == "skills/list":
            self.count += 1
        return await call_next(context)


async def test_backend_pagination_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    # raising=False keeps this a behavioral failure on code without the bound.
    monkeypatch.setattr(
        "fastmcp.skills._proxy.AUTO_PAGINATION_MAX_PAGES", 1, raising=False
    )
    upstream = backend(REVIEW, DEPLOY, page_size=1)
    requests = ListSkillsRequests()
    upstream.add_middleware(requests)
    server = gateway(upstream)
    server.provider_error_strategy = "raise"

    async with skills_client(server) as client:
        with pytest.raises(MCPError):
            await client.list_skills()
    assert requests.count == 1


async def test_local_and_proxied_skills_share_one_catalog() -> None:
    server = gateway(backend(REVIEW))
    server.add_provider(SkillCatalogProvider([DEPLOY]))

    async with skills_client(server) as client:
        listed = await client.list_skills()
    assert [skill.uri for skill in listed] == [
        "skill://deploy/SKILL.md",
        "skill://review/SKILL.md",
    ]


@pytest.mark.parametrize("operation", ["list", "get", "read"])
@pytest.mark.parametrize(
    ("strategy", "global_raise"), [("warn", False), ("raise", False), ("warn", True)]
)
async def test_unavailable_proxy_obeys_provider_error_policy(
    operation: str,
    strategy: ProviderErrorStrategy,
    global_raise: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def unavailable() -> ProxyClient:
        raise ConnectionError("backend unavailable")

    server = gateway(backend(REVIEW))
    server.provider_error_strategy = strategy
    server.add_provider(SkillCatalogProvider([DEPLOY]))
    server.add_provider(ProxyProvider(unavailable, skills=True))

    with temporary_settings(mounted_components_raise_on_load_error=global_raise):
        async with skills_client(server) as client:
            raises = strategy == "raise" or global_raise
            with pytest.raises(MCPError) if raises else nullcontext():
                if operation == "list":
                    listed = await client.list_skills()
                    assert [entry.uri for entry in listed] == [
                        "skill://deploy/SKILL.md",
                        "skill://review/SKILL.md",
                    ]
                elif operation == "get":
                    entry = await client.get_skill("skill://deploy/SKILL.md")
                    assert entry.uri == "skill://deploy/SKILL.md"
                else:
                    skill = await client.read_skill("skill://deploy/SKILL.md")
                    assert skill.files == DEPLOY.files
            if not raises:
                assert "backend unavailable" in caplog.text


async def test_denied_file_hides_skill_without_reading_it() -> None:
    upstream = backend(REVIEW)
    reads = Reads()
    upstream.add_middleware(reads)
    blocked = {"skill://review/notes.md"}
    server = gateway(
        upstream,
        middleware=[
            AuthMiddleware(auth=lambda ctx: str(ctx.component.uri) not in blocked)
        ],
    )

    async with skills_client(server) as client:
        assert await client.list_skills() == []
        with pytest.raises(MCPError, match="Unknown skill URI"):
            await client.get_skill("skill://review/SKILL.md")
        blocked.clear()
        assert len(await client.list_skills()) == 1
    assert reads.uris == []


async def test_file_served_by_another_provider_hides_skill() -> None:
    server = gateway(backend(REVIEW, DEPLOY))

    @server.resource("skill://review/notes.md")
    def shadow() -> str:
        return "Not the backend's bytes"

    async with skills_client(server) as client:
        listed = await client.list_skills()
    assert [skill.uri for skill in listed] == ["skill://deploy/SKILL.md"]


async def test_mounted_proxy_skills_are_namespaced() -> None:
    server = FastMCP("gateway")
    server.mount(create_proxy(backend(REVIEW), skills=True), namespace="github")

    async with skills_client(server) as client:
        skill = await client.get_skill("skill://github/review/SKILL.md")
        assert await client.list_skills() == [skill]
        assert isinstance(skill.resources, list)
        assert [record.uri for record in skill.resources] == [
            "skill://github/review/SKILL.md",
            "skill://github/review/notes.md",
        ]


async def test_response_cache_never_serves_stale_skill_bytes() -> None:
    class Changing(SkillCatalogProvider):
        def __init__(self) -> None:
            super().__init__()
            self.current = REVIEW

        async def load_skills(self) -> SkillCatalog:
            return [self.current]

    changing = Changing()
    upstream = FastMCP("backend", providers=[changing])
    server = gateway(upstream, middleware=[ResponseCachingMiddleware()])

    async with skills_client(server) as client:
        (before,) = await client.read_resource("skill://review/notes.md")
        changing.current = Skill(
            name="review",
            description="Review changes",
            instructions="# Review\n",
            files={"notes.md": "Read the tests first.\n"},
        )
        (after,) = await client.read_resource("skill://review/notes.md")

    assert isinstance(before, TextResourceContents)
    assert isinstance(after, TextResourceContents)
    assert after.text == "Read the tests first.\n"


@pytest.mark.parametrize("forward_skills", [False, True])
@pytest.mark.parametrize("negotiate_skills", [False, True])
@pytest.mark.parametrize("mounted", [False, True])
async def test_read_skill_through_proxy_and_mount(
    tmp_path: Path, forward_skills: bool, negotiate_skills: bool, mounted: bool
) -> None:
    server = create_proxy(backend(REVIEW), skills=forward_skills)
    uri = "skill://review/SKILL.md"
    if mounted:
        parent = FastMCP("Parent")
        parent.mount(server, namespace="team")
        server = parent
        uri = "skill://team/review/SKILL.md"

    extensions = [SkillsClientExtension()] if negotiate_skills else []
    async with Client(server, extensions=extensions) as client:
        skill = await client.read_skill(uri)

    assert skill == REVIEW
    installed = skill.to_directory(tmp_path / skill.name)
    assert Skill.from_directory(installed) == REVIEW
