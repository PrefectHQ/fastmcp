from fastmcp import FastMCP
from fastmcp.server.providers import LocalProvider
from fastmcp.server.providers.aggregate import AggregateProvider
from fastmcp.server.transforms import Namespace


async def test_aggregate_provider_get_tasks_applies_transforms():
    local = LocalProvider()

    @local.tool(task=True)
    async def square(n: int) -> int:
        return n * n

    aggregate = AggregateProvider([local])
    aggregate.add_transform(Namespace("math"))
    mcp = FastMCP("demo", providers=[aggregate])

    tool = await mcp.get_tool("math_square")
    assert tool is not None
    assert tool.key == "tool:math_square@"

    tasks = await mcp.get_tasks()
    assert [c.key for c in tasks] == ["tool:math_square@"]
