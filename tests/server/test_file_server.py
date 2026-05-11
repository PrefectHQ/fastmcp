from pathlib import Path

import mcp.types as mcp_types
import pytest

from fastmcp import FastMCP
from fastmcp.resources import ResourceContent, ResourceResult


@pytest.fixture()
def test_dir(tmp_path_factory) -> Path:
    """Create a temporary directory with test files."""
    tmp = tmp_path_factory.mktemp("test_files")

    # Create test files
    (tmp / "example.py").write_text("print('hello world')")
    (tmp / "readme.md").write_text("# Test Directory\nThis is a test.")
    (tmp / "config.json").write_text('{"test": true}')

    return tmp


@pytest.fixture
def mcp() -> FastMCP:
    mcp = FastMCP()

    return mcp


@pytest.fixture(autouse=True)
def resources(mcp: FastMCP, test_dir: Path) -> FastMCP:
    @mcp.resource("dir://test_dir")
    def list_test_dir() -> ResourceResult:
        """List the files in the test directory"""
        files = [str(f) for f in test_dir.iterdir()]
        return ResourceResult([ResourceContent(f) for f in files])

    @mcp.resource("file://test_dir/example.py")
    def read_example_py() -> str:
        """Read the example.py file"""
        try:
            return (test_dir / "example.py").read_text()
        except FileNotFoundError:
            return "File not found"

    @mcp.resource("file://test_dir/readme.md")
    def read_readme_md() -> str:
        """Read the readme.md file"""
        try:
            return (test_dir / "readme.md").read_text()
        except FileNotFoundError:
            return "File not found"

    @mcp.resource("file://test_dir/config.json")
    def read_config_json() -> str:
        """Read the config.json file"""
        try:
            return (test_dir / "config.json").read_text()
        except FileNotFoundError:
            return "File not found"

    return mcp


@pytest.fixture(autouse=True)
def tools(mcp: FastMCP, test_dir: Path) -> FastMCP:
    @mcp.tool
    def delete_file(path: str) -> bool:
        # ensure path is in test_dir
        if Path(path).resolve().parent != test_dir:
            raise ValueError(f"Path must be in test_dir: {path}")
        Path(path).unlink()
        return True

    return mcp


async def test_list_resources(mcp: FastMCP):
    result = await mcp._list_resources_mcp(mcp_types.ListResourcesRequest())
    assert len(result.resources) == 4

    assert [str(r.uri) for r in result.resources] == [
        "dir://test_dir",
        "file://test_dir/example.py",
        "file://test_dir/readme.md",
        "file://test_dir/config.json",
    ]


async def test_read_resource_dir(mcp: FastMCP):
    res_result = await mcp._read_resource_mcp("dir://test_dir")
    assert isinstance(res_result, mcp_types.ReadResourceResult)
    # ResourceResult splits lists into multiple contents (one per file path)
    assert len(res_result.contents) == 3
    # Extract file paths from each content
    files = [
        item.text
        for item in res_result.contents
        if isinstance(item, mcp_types.TextResourceContents)
    ]

    assert sorted([Path(f).name for f in files]) == [
        "config.json",
        "example.py",
        "readme.md",
    ]


async def test_read_resource_file(mcp: FastMCP):
    res_result = await mcp._read_resource_mcp("file://test_dir/example.py")
    assert isinstance(res_result, mcp_types.ReadResourceResult)
    assert len(res_result.contents) == 1
    res = res_result.contents[0]
    assert isinstance(res, mcp_types.TextResourceContents)
    assert res.text == "print('hello world')"


async def test_delete_file(mcp: FastMCP, test_dir: Path):
    await mcp._call_tool_mcp(
        "delete_file", arguments=dict(path=str(test_dir / "example.py"))
    )
    assert not (test_dir / "example.py").exists()


async def test_delete_file_and_check_resources(mcp: FastMCP, test_dir: Path):
    await mcp._call_tool_mcp(
        "delete_file", arguments=dict(path=str(test_dir / "example.py"))
    )
    res_result = await mcp._read_resource_mcp("file://test_dir/example.py")
    assert isinstance(res_result, mcp_types.ReadResourceResult)
    assert len(res_result.contents) == 1
    res = res_result.contents[0]
    assert isinstance(res, mcp_types.TextResourceContents)
    assert res.text == "File not found"
