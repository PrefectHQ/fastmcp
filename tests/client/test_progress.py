import pytest

from fastmcp import Client, Context, FastMCP

PROGRESS_MESSAGES = []


@pytest.fixture(autouse=True)
def clear_progress_messages():
    PROGRESS_MESSAGES.clear()
    yield
    PROGRESS_MESSAGES.clear()


@pytest.fixture
def fastmcp_server():
    mcp = FastMCP()

    @mcp.tool
    async def progress_tool(context: Context) -> int:
        for i in range(3):
            await context.report_progress(
                progress=i + 1,
                total=3,
                message=f"{(i + 1) / 3 * 100:.2f}% complete",
            )
        return 100

    return mcp


EXPECTED_PROGRESS_MESSAGES = [
    dict(progress=1, total=3, message="33.33% complete"),
    dict(progress=2, total=3, message="66.67% complete"),
    dict(progress=3, total=3, message="100.00% complete"),
]


async def progress_handler(
    progress: float, total: float | None, message: str | None
) -> None:
    PROGRESS_MESSAGES.append(dict(progress=progress, total=total, message=message))


async def test_progress_handler(fastmcp_server: FastMCP):
    async with Client(fastmcp_server, progress_handler=progress_handler) as client:
        await client.call_tool("progress_tool", {})

    assert PROGRESS_MESSAGES == EXPECTED_PROGRESS_MESSAGES


async def test_progress_handler_can_be_supplied_on_tool_call(fastmcp_server: FastMCP):
    async with Client(fastmcp_server) as client:
        await client.call_tool("progress_tool", {}, progress_handler=progress_handler)

    assert PROGRESS_MESSAGES == EXPECTED_PROGRESS_MESSAGES


async def test_progress_handler_supplied_on_tool_call_overrides_default(
    fastmcp_server: FastMCP,
):
    async def bad_progress_handler(
        progress: float, total: float | None, message: str | None
    ) -> None:
        raise Exception("This should not be called")

    async with Client(fastmcp_server, progress_handler=bad_progress_handler) as client:
        await client.call_tool("progress_tool", {}, progress_handler=progress_handler)

    assert PROGRESS_MESSAGES == EXPECTED_PROGRESS_MESSAGES
