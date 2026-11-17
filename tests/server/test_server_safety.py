import pytest

from fastmcp import FastMCP


class TestMountSafety:
    def test_self_mount_raises(self):
        mcp = FastMCP("test")
        with pytest.raises(ValueError, match="Cannot mount a server onto itself"):
            mcp.mount(mcp)

    def test_indirect_mount_cycle_raises(self):
        a = FastMCP("A")
        b = FastMCP("B")
        a.mount(b)

        with pytest.raises(ValueError, match="mount cycle"):
            b.mount(a)

    def test_mount_cycle_names_the_cycle(self):
        a = FastMCP("A")
        b = FastMCP("B")
        a.mount(b)

        with pytest.raises(ValueError) as exc_info:
            b.mount(a)

        message = str(exc_info.value)
        assert "'A'" in message
        assert "'B'" in message
        assert "A -> B -> A" in message

    def test_three_server_mount_cycle_raises(self):
        a = FastMCP("A")
        b = FastMCP("B")
        c = FastMCP("C")
        a.mount(b)
        b.mount(c)

        with pytest.raises(ValueError, match="A -> B -> C -> A"):
            c.mount(a)

    def test_cycle_detected_through_namespaced_mounts(self):
        a = FastMCP("A")
        b = FastMCP("B")
        a.mount(b, namespace="b")

        with pytest.raises(ValueError, match="mount cycle"):
            b.mount(a, namespace="a")

    def test_cycle_detected_through_renamed_mounts(self):
        a = FastMCP("A")
        b = FastMCP("B")
        a.mount(b, tool_names={"one": "two"})

        with pytest.raises(ValueError, match="mount cycle"):
            b.mount(a)

    def test_cycle_detected_through_added_provider(self):
        from fastmcp.server.providers import FastMCPProvider

        a = FastMCP("A")
        b = FastMCP("B")
        a.add_provider(FastMCPProvider(b))

        with pytest.raises(ValueError, match="mount cycle"):
            b.mount(a)

    def test_failed_cycle_does_not_attach_the_provider(self):
        a = FastMCP("A")
        b = FastMCP("B")
        a.mount(b)
        providers_before = list(b.providers)

        with pytest.raises(ValueError, match="mount cycle"):
            b.mount(a)

        assert b.providers == providers_before

    def test_acyclic_diamond_mount_graph_is_allowed(self):
        top = FastMCP("Top")
        left = FastMCP("Left")
        right = FastMCP("Right")
        bottom = FastMCP("Bottom")

        top.mount(left)
        top.mount(right)
        left.mount(bottom)
        right.mount(bottom)

        # Bottom is already mounted twice but does not reach Top, so adding a
        # third edge is still acyclic.
        top.mount(bottom)

    def test_long_mount_chain_is_allowed(self):
        servers = [FastMCP(f"S{i}") for i in range(10)]
        for parent, child in zip(servers, servers[1:]):
            parent.mount(child)

        with pytest.raises(ValueError, match="mount cycle"):
            servers[-1].mount(servers[0])

    async def test_acyclic_mounts_still_serve_tools(self):
        a = FastMCP("A")
        b = FastMCP("B")
        c = FastMCP("C")

        @c.tool
        def ping() -> str:
            return "pong"

        a.mount(b)
        b.mount(c, namespace="inner")

        tools = await a.list_tools()
        assert [tool.name for tool in tools] == ["inner_ping"]
