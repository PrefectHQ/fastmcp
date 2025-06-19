"""Tests for different resource prefix formats in server mounting and importing."""

from fastmcp import FastMCP


async def test_resource_prefix_format_in_constructor():
    """Test that the resource_prefix_format parameter is respected in the constructor."""
    server_path = FastMCP("PathFormat", resource_prefix_format="path")
    server_protocol = FastMCP("ProtocolFormat", resource_prefix_format="protocol")

    # Check that the format is stored correctly
    assert server_path.resource_prefix_format == "path"
    assert server_protocol.resource_prefix_format == "protocol"

    # Register resources
    @server_path.resource("resource://test")
    def get_test_path():
        return "test content"

    @server_protocol.resource("resource://test")
    def get_test_protocol():
        return "test content"

    # Create mount servers
    main_server_path = FastMCP("MainPath", resource_prefix_format="path")
    main_server_protocol = FastMCP("MainProtocol", resource_prefix_format="protocol")

    # Mount the servers
    main_server_path.mount(server_path, "sub")
    main_server_protocol.mount(server_protocol, "sub")

    # Check that the resources are prefixed correctly
    path_resources = await main_server_path.get_resources()
    protocol_resources = await main_server_protocol.get_resources()

    # Path format should be resource://sub/test
    assert "resource://sub/test" in path_resources
    # Protocol format should be sub+resource://test
    assert "sub+resource://test" in protocol_resources


async def test_resource_prefix_format_in_import_server():
    """Test that the resource_prefix_format parameter is respected in import_server."""
    server = FastMCP("TestServer")

    @server.resource("resource://test")
    def get_test():
        return "test content"

    # Import with path format
    main_server_path = FastMCP("MainPath", resource_prefix_format="path")
    await main_server_path.import_server(server, "sub")

    # Import with protocol format
    main_server_protocol = FastMCP("MainProtocol", resource_prefix_format="protocol")
    await main_server_protocol.import_server(server, "sub")

    # Check that the resources are prefixed correctly
    path_resources = await main_server_path._resource_manager.get_resources()
    protocol_resources = await main_server_protocol._resource_manager.get_resources()

    # Path format should be resource://sub/test
    assert "resource://sub/test" in path_resources
    # Protocol format should be sub+resource://test
    assert "sub+resource://test" in protocol_resources
