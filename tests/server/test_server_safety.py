import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.providers import AggregateProvider, FastMCPProvider
from fastmcp.server.transforms import Namespace


class TestMountSafety:
    def test_self_mount_raises(self):
        mcp = FastMCP("test")
        with pytest.raises(ValueError, match="Cannot mount a server onto itself"):
            mcp.mount(mcp)

    @pytest.mark.parametrize("length", [2, 3])
    @pytest.mark.parametrize("namespace", [None, "nested"])
    @pytest.mark.parametrize("rename", [False, True])
    async def test_mount_cycle_leaves_servers_usable(self, length, namespace, rename):
        servers = [FastMCP(str(index)) for index in range(length)]
        for parent, child in zip(servers, servers[1:]):
            parent.mount(
                child,
                namespace,
                tool_names={"hello": "greeting"} if rename else None,
            )

        leaf = servers[-1]
        providers = list(leaf.providers)
        extensions = dict(leaf._extensions)
        with pytest.raises(ValueError, match="cycle"):
            leaf.mount(servers[0], namespace)

        assert leaf.providers == providers
        assert leaf._extensions == extensions
        assert servers[0]._live_parents() == []
        async with Client(servers[0]) as client:
            assert await client.list_tools() == []

    @pytest.mark.parametrize("kind", ["raw", "mounted", "wrapped", "aggregate"])
    def test_add_provider_rejects_cycle(self, kind):
        parent, child = FastMCP("parent"), FastMCP("child")
        parent.mount(child)
        provider = parent
        if kind == "mounted":
            provider = FastMCPProvider(parent)
        elif kind == "wrapped":
            provider = FastMCPProvider(parent).wrap_transform(Namespace("inner"))
        elif kind == "aggregate":
            provider = AggregateProvider([parent]).wrap_transform(Namespace("inner"))

        with pytest.raises(ValueError, match="cycle"):
            child.add_provider(provider, namespace="outer")

    @pytest.mark.parametrize("raw", [False, True])
    def test_mount_checks_nested_aggregate(self, raw):
        parent = FastMCP("parent")
        provider = parent if raw else FastMCPProvider(parent)
        nested = AggregateProvider([provider]).wrap_transform(Namespace("inner"))
        child = FastMCP("child", providers=[AggregateProvider([nested])])
        with pytest.raises(ValueError, match="cycle"):
            parent.mount(child)

    @pytest.mark.parametrize("self_reference", [False, True])
    def test_aggregate_rejects_cycle_after_attachment(self, self_reference):
        aggregate = AggregateProvider()
        parent = FastMCP("parent", providers=[aggregate])
        with pytest.raises(ValueError, match="cycle"):
            aggregate.add_provider(aggregate if self_reference else parent)
        assert aggregate.providers == []

    async def test_shared_child_and_diamond_remain_callable(self):
        root, left, right, child = [FastMCP("same name") for _ in range(4)]

        @child.tool
        def hello() -> str:
            return "hello"

        left.mount(child, "leaf")
        right.mount(child, "leaf")
        root.mount(left, "left")
        root.mount(right, "right")
        root.mount(child, "direct")
        async with Client(root) as client:
            assert {tool.name for tool in await client.list_tools()} == {
                "left_leaf_hello",
                "right_leaf_hello",
                "direct_hello",
            }
            for name in ("left_leaf_hello", "right_leaf_hello", "direct_hello"):
                assert (await client.call_tool(name)).data == "hello"
