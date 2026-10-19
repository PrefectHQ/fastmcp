"""Extension requirements follow every runtime using live provider composition."""

from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager

import anyio
import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.extensions import ServerExtension
from fastmcp.server.providers import LocalProvider, Provider
from fastmcp.server.providers.aggregate import AggregateProvider
from fastmcp.server.providers.fastmcp_provider import FastMCPProvider
from fastmcp.server.transforms import Namespace

EXT_ID = "com.example/runtime"


class RuntimeExtension(ServerExtension):
    identifier = EXT_ID
    auto_register = True

    def __init__(self) -> None:
        self.events: list[str] = []

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        self.events.append("enter")
        try:
            yield
        finally:
            self.events.append("exit")


class BundledLocalProvider(LocalProvider):
    def required_extensions(self) -> Sequence[ServerExtension]:
        return [RuntimeExtension()]


@pytest.fixture
def bundle() -> BundledLocalProvider:
    provider = BundledLocalProvider()

    @provider.tool
    def marker() -> str:
        return "marker"

    return provider


@pytest.mark.parametrize("wrapped", [False, True])
async def test_running_mounted_child_accepts_root_supported_bundle(
    bundle: BundledLocalProvider, wrapped: bool
):
    child = FastMCP("child")
    root = FastMCP("root")
    extension = RuntimeExtension()
    root.add_extension(extension)
    provider: Provider = FastMCPProvider(child)
    if wrapped:
        provider = provider.wrap_transform(Namespace("ns"))
    root.add_provider(provider)

    async with Client(root, mode="auto") as client:
        with pytest.raises(RuntimeError, match="lifespan has already started"):
            child.add_extension(RuntimeExtension())
        child.add_provider(bundle)
        name = "ns_marker" if wrapped else "marker"
        assert [tool.name for tool in await client.list_tools()] == [name]
        assert (await client.call_tool(name)).data == "marker"
        assert EXT_ID in client.server_capabilities.extensions
        assert extension.events == ["enter"]
        registered = child._extensions[EXT_ID]
        assert isinstance(registered, RuntimeExtension)
        assert registered.events == []
    assert extension.events == ["enter", "exit"]
    assert child._extension_scopes == []


async def test_child_provider_lifespan_accepts_root_supported_bundle(
    bundle: BundledLocalProvider,
):
    child = FastMCP("child")

    class SetupProvider(Provider):
        @asynccontextmanager
        async def lifespan(self) -> AsyncIterator[None]:
            child.add_provider(bundle)
            yield

    child.add_provider(SetupProvider())
    root = FastMCP("root")
    root.add_extension(RuntimeExtension())
    root.mount(child)
    async with Client(root, mode="auto") as client:
        assert (await client.call_tool("marker")).data == "marker"


@pytest.mark.parametrize("wrapped", [False, True])
async def test_shared_mounted_aggregate_checks_every_root(
    bundle: BundledLocalProvider, wrapped: bool
):
    aggregate = AggregateProvider()
    provider: Provider = aggregate
    if wrapped:
        provider = AggregateProvider([aggregate.wrap_transform(Namespace("ns"))])
    child = FastMCP("shared child", providers=[provider])
    first = FastMCP("first")
    first.add_extension(RuntimeExtension())
    first.mount(child)
    second = FastMCP("second")
    second.mount(child)

    async with Client(first, mode="auto"):
        async with Client(second, mode="auto") as client:
            with pytest.raises(RuntimeError, match="unavailable in a running server"):
                aggregate.add_provider(bundle)
            assert aggregate.providers == []
            assert await client.list_tools() == []
            assert EXT_ID not in client.server_capabilities.extensions
        # Releasing the unsupported root must release its restriction as well.
        aggregate.add_provider(bundle)
    assert aggregate._extension_scopes == []
    assert child._extension_scopes == []


async def test_shared_mounted_child_accepts_bundle_supported_by_all_roots(
    bundle: BundledLocalProvider,
):
    child = FastMCP("shared child")
    first, second = FastMCP("first"), FastMCP("second")
    for root in (first, second):
        root.add_extension(RuntimeExtension())
        root.mount(child)
    async with Client(first, mode="auto") as first_client:
        async with Client(second, mode="auto") as second_client:
            child.add_provider(bundle)
            assert (await first_client.call_tool("marker")).data == "marker"
            assert (await second_client.call_tool("marker")).data == "marker"
    assert child._extension_scopes == []


async def test_shared_child_keeps_second_roots_restrictions_after_first_exits(
    bundle: BundledLocalProvider,
):
    aggregate = AggregateProvider()
    child = FastMCP("shared child", providers=[aggregate])
    first, second = FastMCP("first"), FastMCP("second")
    first.add_extension(RuntimeExtension())
    first.mount(child)
    second.mount(child)
    first_running, stop_first, first_stopped = (
        anyio.Event(),
        anyio.Event(),
        anyio.Event(),
    )

    async def serve_first() -> None:
        async with Client(first, mode="auto"):
            first_running.set()
            await stop_first.wait()
        first_stopped.set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(serve_first)
        await first_running.wait()
        async with Client(second, mode="auto") as client:
            stop_first.set()
            await first_stopped.wait()
            with pytest.raises(RuntimeError, match="unavailable in a running server"):
                aggregate.add_provider(bundle)
            assert await client.list_tools() == []
    assert aggregate._extension_scopes == []


async def test_shared_child_rejection_leaves_its_registrations_unchanged(
    bundle: BundledLocalProvider,
):
    child = FastMCP("shared child")
    first, second = FastMCP("first"), FastMCP("second")
    first.add_extension(RuntimeExtension())
    first.mount(child)
    second.mount(child)
    async with Client(first, mode="auto"):
        async with Client(second, mode="auto"):
            with pytest.raises(RuntimeError):
                child.add_provider(bundle)
            assert child._extensions == {}
            assert len(child.providers) == 1
        child.add_provider(bundle)


async def test_dynamically_attached_descendant_inherits_all_root_restrictions(
    bundle: BundledLocalProvider,
):
    aggregate = AggregateProvider()
    child = FastMCP("shared child", providers=[aggregate])
    first, second = FastMCP("first"), FastMCP("second")
    first.add_extension(RuntimeExtension())
    first.mount(child)
    second.mount(child)
    descendant = AggregateProvider()
    async with Client(first, mode="auto"):
        async with Client(second, mode="auto") as client:
            aggregate.add_provider(descendant.wrap_transform(Namespace("ns")))
            with pytest.raises(RuntimeError, match="unavailable in a running server"):
                descendant.add_provider(bundle)
            assert await client.list_tools() == []
        descendant.add_provider(bundle)
    assert descendant._extension_scopes == []


@pytest.mark.parametrize("standalone_first", [False, True])
async def test_standalone_child_still_rejects_unavailable_extension(
    bundle: BundledLocalProvider, standalone_first: bool
):
    child = FastMCP("child")
    root = FastMCP("root")
    root.add_extension(RuntimeExtension())
    root.mount(child)
    first, second = (child, root) if standalone_first else (root, child)
    async with Client(first, mode="auto") as first_client:
        async with Client(second, mode="auto") as second_client:
            with pytest.raises(RuntimeError):
                child.add_provider(bundle)
            standalone = first_client if standalone_first else second_client
            assert await standalone.list_tools() == []
            assert child._extensions == {}


async def test_standalone_aggregate_releases_descendant_restrictions(
    bundle: BundledLocalProvider,
):
    descendant = AggregateProvider()
    aggregate = AggregateProvider([descendant])
    async with aggregate.lifespan():
        with pytest.raises(RuntimeError, match="unavailable in a running server"):
            descendant.add_provider(bundle)
    descendant.add_provider(bundle)
    assert aggregate._extension_scopes == []
    assert descendant._extension_scopes == []


async def test_overlapping_standalone_aggregate_lifespans_keep_independent_scopes(
    bundle: BundledLocalProvider,
):
    descendant = AggregateProvider()
    aggregate = AggregateProvider([descendant])
    async with AsyncExitStack() as first, AsyncExitStack() as second:
        await first.enter_async_context(aggregate.lifespan())
        await second.enter_async_context(aggregate.lifespan())
        await first.aclose()
        with pytest.raises(RuntimeError, match="unavailable in a running server"):
            descendant.add_provider(bundle)
    descendant.add_provider(bundle)
    assert aggregate._extension_scopes == []
    assert descendant._extension_scopes == []


async def test_failed_startup_releases_extension_restrictions(
    bundle: BundledLocalProvider,
):
    aggregate = AggregateProvider()
    child = FastMCP("child", providers=[aggregate])

    class FailingProvider(Provider):
        @asynccontextmanager
        async def lifespan(self) -> AsyncIterator[None]:
            raise RuntimeError("setup failed")
            yield  # pragma: no cover

    root = FastMCP("root", providers=[child, FailingProvider()])
    with pytest.raises(RuntimeError, match="setup failed"):
        async with root._lifespan_manager():
            pass
    assert child._extension_scopes == []
    assert aggregate._extension_scopes == []
    aggregate.add_provider(bundle)
