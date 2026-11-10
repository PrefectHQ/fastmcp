"""Client transport inference tests."""

import warnings

import pytest

from fastmcp.client.transports import (
    FastMCPTransport,
    MCPConfigTransport,
    NodeStdioTransport,
    PythonStdioTransport,
    SSETransport,
    StdioTransport,
    StreamableHttpTransport,
    infer_transport,
)
from fastmcp.exceptions import FastMCPDeprecationWarning


class TestInferTransport:
    """Tests for the infer_transport function."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com/api/sse/stream",
            "https://localhost:8080/mcp/sse/endpoint",
            "http://example.com/api/sse",
            "http://example.com/api/sse/",
            "https://localhost:8080/mcp/sse/",
            "http://example.com/api/sse?param=value",
            "https://localhost:8080/mcp/sse/?param=value",
            "https://localhost:8000/mcp/sse?x=1&y=2",
        ],
        ids=[
            "path_with_sse_directory",
            "path_with_sse_subdirectory",
            "path_ending_with_sse",
            "path_ending_with_sse_slash",
            "path_ending_with_sse_https",
            "path_with_sse_and_query_params",
            "path_with_sse_slash_and_query_params",
            "path_with_sse_and_ampersand_param",
        ],
    )
    def test_url_returns_sse_transport(self, url):
        """Test that URLs with /sse/ pattern return SSETransport."""
        assert isinstance(infer_transport(url), SSETransport)

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com/api",
            "https://localhost:8080/mcp/",
            "http://example.com/asset/image.jpg",
            "https://localhost:8080/sservice/endpoint",
            "https://example.com/assets/file",
        ],
        ids=[
            "regular_http_url",
            "regular_https_url",
            "url_with_unrelated_path",
            "url_with_sservice_in_path",
            "url_with_assets_in_path",
        ],
    )
    def test_url_returns_streamable_http_transport(self, url):
        """Test that URLs without /sse/ pattern return StreamableHttpTransport."""
        assert isinstance(infer_transport(url), StreamableHttpTransport)

    def test_infer_remote_transport_from_config(self):
        config = {
            "mcpServers": {
                "test_server": {
                    "url": "http://localhost:8000/sse/",
                    "headers": {"Authorization": "Bearer 123"},
                },
            }
        }
        transport = infer_transport(config)
        assert isinstance(transport, MCPConfigTransport)
        assert isinstance(transport.transport, SSETransport)
        assert transport.transport.url == "http://localhost:8000/sse/"
        assert transport.transport.headers == {"Authorization": "Bearer 123"}

    def test_infer_local_transport_from_config(self):
        config = {
            "mcpServers": {
                "test_server": {
                    "command": "echo",
                    "args": ["hello"],
                },
            }
        }
        transport = infer_transport(config)
        assert isinstance(transport, MCPConfigTransport)
        assert isinstance(transport.transport, StdioTransport)
        assert transport.transport.command == "echo"
        assert transport.transport.args == ["hello"]

    def test_config_with_no_servers(self):
        """Test that an empty MCPConfig raises a ValueError."""
        config = {"mcpServers": {}}
        with pytest.raises(ValueError, match="No MCP servers defined in the config"):
            infer_transport(config)

    def test_mcpconfigtransport_with_no_servers(self):
        """Test that MCPConfigTransport raises a ValueError when initialized with an empty config."""
        config = {"mcpServers": {}}
        with pytest.raises(ValueError, match="No MCP servers defined in the config"):
            MCPConfigTransport(config=config)

    def test_infer_composite_client(self):
        config = {
            "mcpServers": {
                "local": {
                    "command": "echo",
                    "args": ["hello"],
                },
                "remote": {
                    "url": "http://localhost:8000/sse/",
                    "headers": {"Authorization": "Bearer 123"},
                },
            }
        }
        transport = infer_transport(config)
        assert isinstance(transport, MCPConfigTransport)
        # Multi-server configs create composite server at connect time
        assert len(transport.config.mcpServers) == 2

    def test_infer_fastmcp_server(self, fastmcp_server):
        """FastMCP server instances should infer to FastMCPTransport."""
        transport = infer_transport(fastmcp_server)
        assert isinstance(transport, FastMCPTransport)

    def test_infer_fastmcp_v1_server(self):
        """FastMCP 1.0 server instances should infer to FastMCPTransport."""
        from mcp.server.mcpserver import MCPServer as FastMCP1

        server = FastMCP1()
        transport = infer_transport(server)
        assert isinstance(transport, FastMCPTransport)


class TestScriptPathInference:
    def test_path_to_python_script_infers_stdio(self, tmp_path):
        script = tmp_path / "server.py"
        script.write_text("")
        assert isinstance(infer_transport(script), PythonStdioTransport)

    def test_path_to_node_script_infers_stdio(self, tmp_path):
        script = tmp_path / "server.js"
        script.write_text("")
        assert isinstance(infer_transport(script), NodeStdioTransport)

    def test_string_script_path_still_works_but_warns(self, tmp_path):
        script = tmp_path / "server.py"
        script.write_text("")
        with pytest.warns(FastMCPDeprecationWarning, match="pathlib.Path") as record:
            transport = infer_transport(str(script))
        assert isinstance(transport, PythonStdioTransport)
        assert record[0].filename == __file__

    def test_warning_points_at_the_caller_for_every_entry_point(self, tmp_path):
        from fastmcp import Client
        from fastmcp.server import create_proxy

        script = tmp_path / "server.py"
        script.write_text("")
        for build in (Client, create_proxy):
            with pytest.warns(FastMCPDeprecationWarning) as record:
                build(str(script))
            assert record[0].filename == __file__, build

    def test_path_does_not_warn(self, tmp_path):
        script = tmp_path / "server.py"
        script.write_text("")
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            infer_transport(script)

    def test_string_to_missing_script_is_not_a_transport(self, tmp_path):
        with pytest.raises(ValueError, match="Could not infer"):
            infer_transport(str(tmp_path / "missing.py"))

    def test_path_with_unsupported_suffix_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="Unsupported script type"):
            infer_transport(tmp_path / "server.rb")
