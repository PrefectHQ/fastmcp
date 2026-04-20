import pytest

from fastmcp import FastMCP


class TestMountSafety:
    def test_self_mount_raises(self):
        mcp = FastMCP("test")
        with pytest.raises(ValueError, match="Cannot mount a server onto itself"):
            mcp.mount(mcp)
