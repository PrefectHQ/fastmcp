import warnings

from fastmcp import FastMCP
from fastmcp.client import Client


class TestDeprecatedMountArgOrder:
    """Test deprecated positional argument order for mount() method."""

    async def test_mount_deprecated_arg_order_with_warning(self):
        """Test that mount(prefix, server) still works but raises deprecation warning."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.tool
        def sub_tool() -> str:
            return "Sub tool result"

        # Test the deprecated argument order: mount(prefix, server)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            main_app.mount("sub", sub_app)  # type: ignore[arg-type]  # Old order: prefix first, server second

            # Check that a deprecation warning was raised
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert (
                "Mount prefixes are now optional and the first positional argument should be the server"
                in str(w[0].message)
            )

        # Verify the mount worked correctly despite deprecated order
        tools = await main_app.get_tools()
        assert "sub_sub_tool" in tools

        # Test functionality
        async with Client(main_app) as client:
            result = await client.call_tool("sub_sub_tool", {})
            assert result[0].text == "Sub tool result"  # type: ignore[attr-defined]

    async def test_mount_new_arg_order_no_warning(self):
        """Test that mount(server, prefix) works without deprecation warning."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.tool
        def sub_tool() -> str:
            return "Sub tool result"

        # Test the new argument order: mount(server, prefix)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            main_app.mount(sub_app, "sub")  # New order: server first, prefix second

            # Check that no deprecation warning was raised for argument order
            mount_warnings = [
                warning
                for warning in w
                if "Mount prefixes are now optional" in str(warning.message)
            ]
            assert len(mount_warnings) == 0

        # Verify the mount worked correctly
        tools = await main_app.get_tools()
        assert "sub_sub_tool" in tools

    async def test_mount_deprecated_order_no_prefix(self):
        """Test deprecated order detection when first arg is empty string."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.tool
        def sub_tool() -> str:
            return "Sub tool result"

        # Test with empty string as first argument (old style for no prefix)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            main_app.mount("", sub_app)  # type: ignore[arg-type]  # Old order: empty prefix first, server second

            # Check that a deprecation warning was raised
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert (
                "Mount prefixes are now optional and the first positional argument should be the server"
                in str(w[0].message)
            )

        # Verify the mount worked correctly (no prefix)
        tools = await main_app.get_tools()
        assert "sub_tool" in tools  # No prefix applied


class TestDeprecatedImportArgOrder:
    """Test deprecated positional argument order for import_server() method."""

    async def test_import_deprecated_arg_order_with_warning(self):
        """Test that import_server(prefix, server) still works but raises deprecation warning."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.tool
        def sub_tool() -> str:
            return "Sub tool result"

        # Test the deprecated argument order: import_server(prefix, server)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            await main_app.import_server("sub", sub_app)  # type: ignore[arg-type]  # Old order: prefix first, server second

            # Check that a deprecation warning was raised
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert (
                "Import prefixes are now optional and the first positional argument should be the server"
                in str(w[0].message)
            )

        # Verify the import worked correctly despite deprecated order
        assert "sub_sub_tool" in main_app._tool_manager._tools

        # Test functionality
        async with Client(main_app) as client:
            result = await client.call_tool("sub_sub_tool", {})
            assert result[0].text == "Sub tool result"  # type: ignore[attr-defined]

    async def test_import_new_arg_order_no_warning(self):
        """Test that import_server(server, prefix) works without deprecation warning."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.tool
        def sub_tool() -> str:
            return "Sub tool result"

        # Test the new argument order: import_server(server, prefix)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            await main_app.import_server(
                sub_app, "sub"
            )  # New order: server first, prefix second

            # Check that no deprecation warning was raised for argument order
            import_warnings = [
                warning
                for warning in w
                if "Import prefixes are now optional" in str(warning.message)
            ]
            assert len(import_warnings) == 0

        # Verify the import worked correctly
        assert "sub_sub_tool" in main_app._tool_manager._tools

    async def test_import_deprecated_order_no_prefix(self):
        """Test deprecated order detection when first arg is empty string."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.tool
        def sub_tool() -> str:
            return "Sub tool result"

        # Test with empty string as first argument (old style for no prefix)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            await main_app.import_server("", sub_app)  # type: ignore[arg-type]  # Old order: empty prefix first, server second

            # Check that a deprecation warning was raised
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert (
                "Import prefixes are now optional and the first positional argument should be the server"
                in str(w[0].message)
            )

        # Verify the import worked correctly (no prefix)
        assert "sub_tool" in main_app._tool_manager._tools  # No prefix applied

    async def test_import_deprecated_order_with_resources_and_prompts(self):
        """Test deprecated order works with all component types."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.tool
        def sub_tool() -> str:
            return "Sub tool result"

        @sub_app.resource(uri="data://config")
        def sub_resource():
            return "Sub resource data"

        @sub_app.resource(uri="users://{user_id}/info")
        def sub_template(user_id: str):
            return f"Sub template for user {user_id}"

        @sub_app.prompt
        def sub_prompt() -> str:
            return "Sub prompt content"

        # Test the deprecated argument order with all component types
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            await main_app.import_server("api", sub_app)  # type: ignore[arg-type]  # Old order: prefix first, server second

            # Check that a deprecation warning was raised
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)

        # Verify all component types were imported correctly with prefix
        assert "api_sub_tool" in main_app._tool_manager._tools
        assert "data://api/config" in main_app._resource_manager._resources
        assert "users://api/{user_id}/info" in main_app._resource_manager._templates
        assert "api_sub_prompt" in main_app._prompt_manager._prompts


class TestArgOrderDetection:
    """Test that argument order detection works correctly."""

    async def test_mount_correctly_identifies_server_vs_string(self):
        """Test that mount correctly identifies FastMCP instances vs strings."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        # This should NOT trigger deprecation warning (server first, prefix second)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            main_app.mount(sub_app, "prefix")

            mount_warnings = [
                warning
                for warning in w
                if "Mount prefixes are now optional" in str(warning.message)
            ]
            assert len(mount_warnings) == 0

        # This SHOULD trigger deprecation warning (string first, server second)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            main_app.mount("prefix2", sub_app)  # type: ignore[arg-type]

            mount_warnings = [
                warning
                for warning in w
                if "Mount prefixes are now optional" in str(warning.message)
            ]
            assert len(mount_warnings) == 1

    async def test_import_correctly_identifies_server_vs_string(self):
        """Test that import_server correctly identifies FastMCP instances vs strings."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        # This should NOT trigger deprecation warning (server first, prefix second)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            await main_app.import_server(sub_app, "prefix")

            import_warnings = [
                warning
                for warning in w
                if "Import prefixes are now optional" in str(warning.message)
            ]
            assert len(import_warnings) == 0

        # This SHOULD trigger deprecation warning (string first, server second)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            await main_app.import_server("prefix2", sub_app)  # type: ignore[arg-type]

            import_warnings = [
                warning
                for warning in w
                if "Import prefixes are now optional" in str(warning.message)
            ]
            assert len(import_warnings) == 1
