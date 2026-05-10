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
