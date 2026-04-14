import pytest

from fastmcp import FastMCP
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware


class TestMountSafety:
    def test_self_mount_raises(self):
        mcp = FastMCP("test")
        with pytest.raises(ValueError, match="Cannot mount a server onto itself"):
            mcp.mount(mcp)

    def test_mount_wrong_arg_order_raises(self):
        parent = FastMCP("parent")
        child = FastMCP("child")
        with pytest.raises(TypeError, match="expected a FastMCP server"):
            parent.mount("namespace", child)  # type: ignore


class TestMiddlewareDedupe:
    def test_duplicate_middleware_not_added(self):
        mcp = FastMCP("test")
        mw = ErrorHandlingMiddleware()
        mcp.add_middleware(mw)
        mcp.add_middleware(mw)
        # Count only our ErrorHandlingMiddleware (server may add defaults)
        count = sum(1 for m in mcp.middleware if m is mw)
        assert count == 1

    def test_different_instances_both_added(self):
        mcp = FastMCP("test")
        mw1 = ErrorHandlingMiddleware()
        mw2 = ErrorHandlingMiddleware()
        mcp.add_middleware(mw1)
        mcp.add_middleware(mw2)
        count = sum(
            1 for m in mcp.middleware if isinstance(m, ErrorHandlingMiddleware)
        )
        assert count == 2
