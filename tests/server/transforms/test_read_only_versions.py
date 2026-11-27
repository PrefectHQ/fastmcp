import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.transforms import ReadOnly


@pytest.mark.parametrize("middle_version", [False, True])
async def test_filtered_catalog_dispatches_highest_read_only_version(
    middle_version: bool,
):
    legacy = FastMCP("Legacy")

    @legacy.tool(name="lookup", annotations={"readOnlyHint": True})
    def unversioned() -> str:
        return "unversioned reader"

    catalog = FastMCP("Catalog")
    catalog.mount(legacy)
    if middle_version:

        @catalog.tool(name="lookup", version="1", annotations={"readOnlyHint": True})
        def reader() -> str:
            return "versioned reader"

    @catalog.tool(name="lookup", version="2")
    def writer() -> str:
        return "writer"

    view = FastMCP("Reader")
    view.mount(catalog)
    view.add_transform(ReadOnly())

    async with Client(view) as client:
        assert [tool.name for tool in await client.list_tools()] == ["lookup"]
        result = await client.call_tool("lookup")
        assert result.data == (
            "versioned reader" if middle_version else "unversioned reader"
        )
