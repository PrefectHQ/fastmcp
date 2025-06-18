"""Tests for the deprecated separator parameters in mount() and import_server() methods."""

import pytest
from mcp import McpError

from fastmcp import Client, FastMCP

# reset deprecation warnings for this module
pytestmark = pytest.mark.filterwarnings("default::DeprecationWarning")


def test_mount_resource_separator_deprecation_warning():
    """Test that using resource_separator in mount() raises a deprecation warning."""
    main_app = FastMCP("MainApp")
    sub_app = FastMCP("SubApp")

    with pytest.warns(
        DeprecationWarning,
        match="The resource_separator parameter is deprecated and ignored",
    ):
        main_app.mount("sub", sub_app, resource_separator="+")


async def test_mount_tool_separator_deprecation_warning():
    """Test that using tool_separator in mount() raises a deprecation warning."""
    main_app = FastMCP("MainApp")
    sub_app = FastMCP("SubApp")

    with pytest.warns(
        DeprecationWarning,
        match="The tool_separator parameter is deprecated and will be removed in a future version",
    ):
        main_app.mount("sub", sub_app, tool_separator="-")

    # Verify the separator is ignored and the default is used
    @sub_app.tool
    def test_tool():
        return "test"

    async with Client(main_app) as client:
        assert "sub_test_tool" in {t.name for t in await client.list_tools()}
        assert "sub-test_tool" not in {t.name for t in await client.list_tools()}


async def test_mount_prompt_separator_deprecation_warning():
    """Test that using prompt_separator in mount() raises a deprecation warning."""
    main_app = FastMCP("MainApp")
    sub_app = FastMCP("SubApp")

    with pytest.warns(
        DeprecationWarning,
        match="The prompt_separator parameter is deprecated and will be removed in a future version",
    ):
        main_app.mount("sub", sub_app, prompt_separator="-")

    # Verify the separator is ignored and the default is used
    @sub_app.prompt
    def test_prompt():
        return "test"

    async with Client(main_app) as client:
        assert await client.get_prompt("sub_test_prompt")
        with pytest.raises(McpError, match="Unknown prompt"):
            await client.get_prompt("sub-test_prompt")


async def test_import_server_separator_deprecation_warnings():
    """Test that using separators in import_server() raises deprecation warnings."""
    main_app = FastMCP("MainApp")
    sub_app = FastMCP("SubApp")

    with pytest.warns(
        DeprecationWarning,
        match="The tool_separator parameter is deprecated and will be removed in a future version",
    ):
        await main_app.import_server("sub", sub_app, tool_separator="-")

    main_app = FastMCP("MainApp")
    with pytest.warns(
        DeprecationWarning,
        match="The resource_separator parameter is deprecated and ignored",
    ):
        await main_app.import_server("sub", sub_app, resource_separator="+")

    main_app = FastMCP("MainApp")
    with pytest.warns(
        DeprecationWarning,
        match="The prompt_separator parameter is deprecated and will be removed in a future version",
    ):
        await main_app.import_server("sub", sub_app, prompt_separator="-")
