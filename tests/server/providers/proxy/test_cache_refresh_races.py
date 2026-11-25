import asyncio
from unittest.mock import AsyncMock, MagicMock

import mcp_types
import pytest
from mcp import MCPError

from fastmcp import Client
from fastmcp.server.providers.proxy import ProxyProvider


def _listing(kind: str, name: str):
    if kind in {"tools", "tool_hash"}:
        return [
            mcp_types.Tool(
                name=name,
                input_schema={"type": "object"},
                meta={
                    "fastmcp": {"tool_hash": f"{name}-hash"},
                    "ui": {"visibility": ["app"]},
                },
            )
        ]
    if kind == "resources":
        return [mcp_types.Resource(name=name, uri=f"data://{name}")]
    if kind == "templates":
        return [
            mcp_types.ResourceTemplate(
                name=name, uri_template=f"data://{name}/{{value}}"
            )
        ]
    return [mcp_types.Prompt(name=name)]


def _methods(provider: ProxyProvider, client: MagicMock, kind: str):
    if kind in {"tools", "tool_hash"}:

        def getter():
            if kind == "tool_hash":
                return provider._get_tool_by_hash("old-hash", "old")
            return provider._get_tool("old")

        return provider._list_tools, getter, client.list_tools, "_tools_cache"
    if kind == "resources":
        return (
            provider._list_resources,
            lambda: provider._get_resource("data://old"),
            client.list_resources,
            "_resources_cache",
        )
    if kind == "templates":
        return (
            provider._list_resource_templates,
            lambda: provider._get_resource_template("data://old/value"),
            client.list_resource_templates,
            "_templates_cache",
        )
    return (
        provider._list_prompts,
        lambda: provider._get_prompt("old"),
        client.list_prompts,
        "_prompts_cache",
    )


@pytest.fixture
def upstream_client():
    client = MagicMock(spec=Client)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


@pytest.mark.parametrize("kind", ["tools", "resources", "templates", "prompts"])
async def test_older_listing_cannot_replace_newer_cache(upstream_client, kind):
    provider = ProxyProvider(lambda: upstream_client)
    list_components, _, upstream_list, cache_attribute = _methods(
        provider, upstream_client, kind
    )
    old_started = asyncio.Event()
    release_old = asyncio.Event()
    calls = 0

    async def list_upstream():
        nonlocal calls
        calls += 1
        if calls == 1:
            old_started.set()
            await release_old.wait()
            return _listing(kind, "old")
        return _listing(kind, "new")

    upstream_list.side_effect = list_upstream
    old_task = asyncio.create_task(list_components())
    try:
        await old_started.wait()
        newer_result = await list_components()
        newer_cache = getattr(provider, cache_attribute)
        release_old.set()
        older_result = await old_task

        assert [item.name for item in older_result] == ["old"]
        assert [item.name for item in newer_result] == ["new"]
        assert getattr(provider, cache_attribute) is newer_cache
        assert [item.name for item in newer_cache.items] == ["new"]
    finally:
        old_task.cancel()
        await asyncio.gather(old_task, return_exceptions=True)


@pytest.mark.parametrize(
    "kind", ["tools", "resources", "templates", "prompts", "tool_hash"]
)
@pytest.mark.parametrize("warm_cache", [False, True], ids=["cold", "expired"])
@pytest.mark.parametrize("newer_outcome", ["pending", "success", "failure"])
async def test_lookup_uses_its_own_refresh_result(
    upstream_client, kind, warm_cache, newer_outcome
):
    provider = ProxyProvider(lambda: upstream_client, cache_ttl=0)
    list_components, get_component, upstream_list, cache_attribute = _methods(
        provider, upstream_client, kind
    )
    if warm_cache:
        upstream_list.return_value = _listing(kind, "seed")
        await list_components()
    initial_cache = getattr(provider, cache_attribute)

    started = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]
    calls = 0

    async def list_upstream():
        nonlocal calls
        index = calls
        calls += 1
        started[index].set()
        await release[index].wait()
        if index == 1 and newer_outcome == "failure":
            raise RuntimeError("upstream refresh failed")
        return _listing(kind, "old" if index == 0 else "new")

    upstream_list.side_effect = list_upstream
    old_task = asyncio.create_task(get_component())
    tasks = [old_task]
    try:
        await started[0].wait()
        new_task = asyncio.create_task(list_components())
        tasks.append(new_task)
        await started[1].wait()

        if newer_outcome != "pending":
            release[1].set()
            if newer_outcome == "failure":
                with pytest.raises(MCPError, match="upstream refresh failed"):
                    await new_task
            else:
                await new_task

        cache_before_old_completion = getattr(provider, cache_attribute)
        release[0].set()
        result = await old_task
        assert result is not None
        assert result.name == "old"
        assert getattr(provider, cache_attribute) is cache_before_old_completion

        if newer_outcome == "success":
            assert [item.name for item in cache_before_old_completion.items] == ["new"]
        else:
            assert cache_before_old_completion is initial_cache

        if newer_outcome == "pending":
            release[1].set()
            await new_task
            assert [item.name for item in getattr(provider, cache_attribute).items] == [
                "new"
            ]
        assert upstream_list.await_count == (3 if warm_cache else 2)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
