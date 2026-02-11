import pytest
from starlette.applications import Starlette

from fastmcp import FastMCP


class TestRemovedKwargs:
    def test_host_kwarg_raises_type_error(self):
        with pytest.raises(TypeError, match="no longer accepts `host`"):
            FastMCP(host="1.2.3.4")

    def test_settings_property_removed(self):
        mcp = FastMCP()
        assert not hasattr(mcp, "_deprecated_settings")
        with pytest.raises(AttributeError):
            mcp.settings  # noqa: B018  # ty: ignore[unresolved-attribute]


def test_http_app_with_sse_transport():
    """Test that http_app with SSE transport works."""
    server = FastMCP("TestServer")
    app = server.http_app(transport="sse")
    assert isinstance(app, Starlette)
