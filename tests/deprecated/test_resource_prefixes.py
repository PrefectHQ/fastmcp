"""Tests for legacy resource prefix behavior."""

import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.server import (
    add_resource_prefix,
    has_resource_prefix,
    remove_resource_prefix,
)
from fastmcp.utilities.tests import temporary_settings

# reset deprecation warnings for this module
pytestmark = pytest.mark.filterwarnings("default::DeprecationWarning")


class TestLegacyResourcePrefixes:
    """Test the legacy resource prefix behavior."""

    def test_add_resource_prefix_legacy(self):
        """Test that add_resource_prefix uses the legacy format when resource_prefix_format is 'protocol'."""
        with temporary_settings(resource_prefix_format="protocol"):
            result = add_resource_prefix("resource://path/to/resource", "prefix")
            assert result == "prefix+resource://path/to/resource"

            # Empty prefix should return the original URI
            result = add_resource_prefix("resource://path/to/resource", "")
            assert result == "resource://path/to/resource"

    def test_remove_resource_prefix_legacy(self):
        """Test that remove_resource_prefix uses the legacy format when resource_prefix_format is 'protocol'."""
        with temporary_settings(resource_prefix_format="protocol"):
            result = remove_resource_prefix(
                "prefix+resource://path/to/resource", "prefix"
            )
            assert result == "resource://path/to/resource"

            # URI without the prefix should be returned as is
            result = remove_resource_prefix("resource://path/to/resource", "prefix")
            assert result == "resource://path/to/resource"

            # Empty prefix should return the original URI
            result = remove_resource_prefix("resource://path/to/resource", "")
            assert result == "resource://path/to/resource"

    def test_has_resource_prefix_legacy(self):
        """Test that has_resource_prefix uses the legacy format when resource_prefix_format is 'protocol'."""
        with temporary_settings(resource_prefix_format="protocol"):
            result = has_resource_prefix("prefix+resource://path/to/resource", "prefix")
            assert result is True

            result = has_resource_prefix("resource://path/to/resource", "prefix")
            assert result is False

            # Empty prefix should always return False
            result = has_resource_prefix("resource://path/to/resource", "")
            assert result is False


async def test_mount_with_legacy_prefixes():
    """Test mounting a server with legacy resource prefixes."""
    with temporary_settings(resource_prefix_format="protocol"):
        main_server = FastMCP("MainServer")
        sub_server = FastMCP("SubServer")

        @sub_server.resource("resource://test")
        def get_test():
            return "test content"

        # Mount the server with a prefix (using old argument order for this legacy test)
        with pytest.warns(DeprecationWarning, match="Mount prefixes are now optional"):
            main_server.mount("sub", sub_server)  # type: ignore[arg-type]

        # Check that the resource is prefixed using the legacy format
        resources = await main_server.get_resources()

        # In legacy format, the key would be "sub+resource://test"
        assert "sub+resource://test" in resources

        # Test accessing the resource through client
        async with Client(main_server) as client:
            result = await client.read_resource("sub+resource://test")
            # Different content types might be returned, but we just want to verify we got something
            assert len(result) > 0


async def test_import_server_with_legacy_prefixes():
    """Test importing a server with legacy resource prefixes."""
    with temporary_settings(resource_prefix_format="protocol"):
        main_server = FastMCP("MainServer")
        sub_server = FastMCP("SubServer")

        @sub_server.resource("resource://test")
        def get_test():
            return "test content"

        # Import the server with a prefix (using old argument order for this legacy test)
        with pytest.warns(DeprecationWarning, match="Import prefixes are now optional"):
            await main_server.import_server("sub", sub_server)  # type: ignore[arg-type]

        # Check that the resource is prefixed using the legacy format
        resources = await main_server.get_resources()

        # In legacy format, the key would be "sub+resource://test"
        assert "sub+resource://test" in resources
