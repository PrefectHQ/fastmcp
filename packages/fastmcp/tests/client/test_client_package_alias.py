def test_fastmcp_client_imports_alias_split_package() -> None:
    from fastmcp_client import Client as SplitClient

    from fastmcp import Client as FastMCPClient

    assert FastMCPClient is SplitClient


def test_fastmcp_client_submodules_alias_split_package() -> None:
    from fastmcp_client.client.client import CallToolResult as SplitCallToolResult
    from fastmcp_client.client.transports.http import (
        StreamableHttpTransport as SplitStreamableHttpTransport,
    )
    from fastmcp_client.exceptions import ToolError as SplitToolError
    from fastmcp_client.mcp_config import MCPConfig as SplitMCPConfig

    from fastmcp.client.client import CallToolResult
    from fastmcp.client.transports.http import StreamableHttpTransport
    from fastmcp.exceptions import ToolError
    from fastmcp.mcp_config import MCPConfig

    assert CallToolResult is SplitCallToolResult
    assert StreamableHttpTransport is SplitStreamableHttpTransport
    assert ToolError is SplitToolError
    assert MCPConfig is SplitMCPConfig


def test_fastmcp_client_dependencies_delegate_to_full_server_dependencies(
    monkeypatch,
) -> None:
    from fastmcp_client.client.dependencies import get_http_headers

    from fastmcp.server import dependencies as server_dependencies

    def fake_get_http_headers(
        include_all: bool = False,
        include: set[str] | None = None,
    ) -> dict[str, str]:
        assert include_all is False
        assert include == {"authorization"}
        return {"authorization": "Bearer token"}

    monkeypatch.setattr(
        server_dependencies,
        "get_http_headers",
        fake_get_http_headers,
    )

    assert get_http_headers(include={"authorization"}) == {
        "authorization": "Bearer token"
    }
