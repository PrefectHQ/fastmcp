"""Skills metadata follows resource visibility and live proxy reads."""

import hashlib
from pathlib import Path

import pytest
from mcp import MCPError
from mcp_types import INVALID_PARAMS

from fastmcp import Client, FastMCP
from fastmcp.server import create_proxy
from fastmcp.server.extensions import MethodBinding, ServerExtension
from fastmcp.server.middleware import Middleware
from fastmcp.server.middleware.caching import ResponseCachingMiddleware
from fastmcp.server.providers.skills import SkillsDirectoryProvider
from fastmcp.server.transforms import Namespace
from fastmcp.skills import SkillsClientExtension
from fastmcp.skills._constants import SKILLS_EXTENSION_ID
from fastmcp.skills.extension import SkillsExtension
from fastmcp.skills.models import (
    GetSkillParams,
    GetSkillRequest,
    GetSkillResult,
    ListSkillsParams,
    ListSkillsResult,
    SkillEntry,
)
from tests.server.providers.proxy.test_skills import backend, skills_client


@pytest.mark.parametrize("placement", ["gateway", "mounted", "provider"])
@pytest.mark.parametrize("supporting_file", [False, True])
async def test_hidden_proxy_file_hides_skill(
    tmp_path: Path, placement: str, supporting_file: bool
) -> None:
    backend(tmp_path, "review")
    provider = SkillsDirectoryProvider(
        tmp_path, experimental_skills=True, supporting_files="resources"
    )
    upstream = FastMCP("upstream", providers=[provider])
    upstream.add_extension(SkillsExtension(providers=[provider]))
    proxy = create_proxy(upstream)
    if placement == "provider":
        target = proxy.providers[-1]
    else:
        target = proxy
    uri = "skill://review/guide.txt" if supporting_file else "skill://review/SKILL.md"
    target.disable(names={uri}, components={"resource"})
    gateway = FastMCP("gateway")
    gateway.mount(proxy, namespace="remote")
    if placement == "gateway":
        proxy.enable(names={uri}, components={"resource"})
        gateway.disable(
            names={uri.replace("skill://", "skill://remote/")}, components={"resource"}
        )
    async with skills_client(gateway) as client:
        assert await client.list_skills() == []
        with pytest.raises(MCPError) as error:
            await client.get_skill("skill://remote/review/SKILL.md")
        assert error.value.error.code == INVALID_PARAMS


@pytest.mark.parametrize("scheme", ["skill", "memory", "https"])
@pytest.mark.parametrize("mode", ["auto", "2026-07-28"])
async def test_manifest_and_bytes_stay_coherent_for_any_uri_scheme(
    scheme: str, mode: str
) -> None:
    upstream = FastMCP("upstream")
    uri = f"{scheme}://host/review/SKILL.md"
    content = "original"

    @upstream.resource(uri)
    def instructions() -> str:
        return content

    class Catalog(ServerExtension):
        identifier = SKILLS_EXTENSION_ID

        def methods(self):
            return (
                MethodBinding(
                    method="skills/list",
                    params_type=ListSkillsParams,
                    handler=self._handle_list,
                ),
                MethodBinding(
                    method="skills/get",
                    params_type=GetSkillParams,
                    handler=self._handle_get,
                ),
            )

        def current(self) -> SkillEntry:
            return SkillEntry.model_validate(
                {
                    "uri": uri,
                    "frontmatter": {"name": "review", "description": "Review code"},
                    "resources": [
                        {
                            "uri": uri,
                            "size": len(content),
                            "digest": f"sha256:{hashlib.sha256(content.encode()).hexdigest()}",
                        }
                    ],
                }
            )

        async def _handle_list(self, context, params):
            return ListSkillsResult(skills=[self.current()])

        async def _handle_get(self, context, params):
            return GetSkillResult(skill=self.current())

    upstream.add_extension(Catalog())
    gateway = FastMCP("gateway")
    gateway.mount(create_proxy(upstream), namespace="remote")
    gateway.add_middleware(ResponseCachingMiddleware())
    proxied_uri = uri.replace("://", "://remote/")
    async with Client(
        gateway, mode=mode, extensions=[SkillsClientExtension()]
    ) as client:
        assert (await client.read_resource(proxied_uri))[0].text == "original"
        content = "changed"
        if mode == "auto":
            skill = await client.get_skill(proxied_uri)
        else:
            result = await client.session.send_request(
                GetSkillRequest(params=GetSkillParams(uri=proxied_uri)), GetSkillResult
            )
            skill = result.skill
        assert (await client.read_resource(proxied_uri))[0].text == "changed"
        assert isinstance(skill.resources, list)
        assert (
            skill.resources[0].digest
            == f"sha256:{hashlib.sha256(b'changed').hexdigest()}"
        )


@pytest.mark.parametrize("supporting_file", [False, True])
@pytest.mark.parametrize("mounted", [False, True])
async def test_shadowed_proxy_resource_hides_skill(
    tmp_path: Path, supporting_file: bool, mounted: bool
) -> None:
    proxy = create_proxy(backend(tmp_path, "review"))
    gateway = FastMCP("gateway")
    if mounted:
        gateway.mount(proxy)
    else:
        gateway.add_provider(proxy.providers[-1])
    uri = "skill://review/guide.txt" if supporting_file else "skill://review/SKILL.md"

    @gateway.resource(uri)
    def shadow() -> str:
        return "Different content"

    async with skills_client(gateway) as client:
        assert (await client.read_resource(uri))[0].text == "Different content"
        assert await client.list_skills() == []
        with pytest.raises(MCPError) as error:
            await client.get_skill("skill://review/SKILL.md")
        assert error.value.error.code == INVALID_PARAMS


@pytest.mark.parametrize("skills_enabled", [False, True])
async def test_unrelated_local_resource_keeps_response_cache(
    tmp_path: Path, skills_enabled: bool
) -> None:
    upstream = backend(tmp_path, "review") if skills_enabled else FastMCP("plain")
    calls = 0
    gateway = create_proxy(upstream)
    gateway.add_middleware(ResponseCachingMiddleware())

    @gateway.resource("data://counter")
    def counter() -> str:
        nonlocal calls
        calls += 1
        return str(calls)

    async with Client(gateway) as client:
        assert (await client.read_resource("data://counter"))[0].text == "1"
        assert (await client.read_resource("data://counter"))[0].text == "1"
    assert calls == 1


async def test_ordinary_proxy_resources_keep_response_cache() -> None:
    calls = 0
    upstream = FastMCP("plain")

    @upstream.resource("skill://counter/SKILL.md")
    def counter() -> str:
        nonlocal calls
        calls += 1
        return str(calls)

    gateway = create_proxy(upstream)
    gateway.add_middleware(ResponseCachingMiddleware())
    async with Client(gateway) as client:
        assert (await client.read_resource("skill://counter/SKILL.md"))[0].text == "1"
        assert (await client.read_resource("skill://counter/SKILL.md"))[0].text == "1"
    assert calls == 1


async def test_direct_proxy_provider_namespace(tmp_path: Path) -> None:
    proxy = create_proxy(backend(tmp_path, "review"))
    proxy.providers[-1].add_transform(Namespace("remote"))
    proxy.add_middleware(ResponseCachingMiddleware())
    async with skills_client(proxy) as client:
        uri = "skill://remote/review/SKILL.md"
        skill = await client.get_skill(uri)
        assert await client.list_skills() == [skill]
        original = (await client.read_resource(uri))[0].text
        (tmp_path / "review" / "SKILL.md").write_bytes(
            (original + "Updated\n").encode()
        )
        updated = await client.get_skill(uri)
        assert (await client.read_resource(uri))[0].text == original + "Updated\n"
        assert updated != skill


async def test_cached_local_read_does_not_probe_backend() -> None:
    class Discoveries(Middleware):
        calls = 0

        async def on_discover(self, context, call_next):
            self.calls += 1
            return await call_next(context)

    upstream = FastMCP("plain")
    discoveries = Discoveries()
    upstream.add_middleware(discoveries)
    gateway = create_proxy(upstream)
    gateway.add_middleware(ResponseCachingMiddleware())

    @gateway.resource("data://local")
    def local() -> str:
        return "local"

    async with Client(gateway) as client:
        await client.read_resource("data://local")
        after_first = discoveries.calls
        await client.read_resource("data://local")
        assert discoveries.calls == after_first
