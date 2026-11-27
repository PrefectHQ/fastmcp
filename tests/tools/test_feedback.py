import pytest

from fastmcp import Client, FastMCP
from fastmcp.tools.feedback import FeedbackReport, create_feedback_tool


@pytest.mark.parametrize("use_async", [False, True])
async def test_submit_feedback(use_async: bool):
    reports: list[FeedbackReport] = []

    async def save(report: FeedbackReport) -> None:
        reports.append(report)

    server = FastMCP("feedback")
    server.add_tool(create_feedback_tool(save if use_async else reports.append))
    async with Client(server) as client:
        (tool,) = await client.list_tools()
        assert tool.name == "submit_feedback"
        assert tool.annotations and tool.annotations.read_only_hint is False
        assert set(tool.input_schema["required"]) == {"task", "description"}
        result = await client.call_tool(
            "submit_feedback",
            {
                "task": "Find my order",
                "description": "Had to scan every order",
                "suggestion": "Add search",
            },
        )
        assert result.data == "Feedback received."
    assert reports == [
        FeedbackReport(
            "Find my order", "Had to scan every order", suggestion="Add search"
        )
    ]


async def test_handler_failure_is_reported_to_client():
    def reject(report: FeedbackReport) -> None:
        raise RuntimeError("Inbox unavailable")

    server = FastMCP("feedback")
    server.add_tool(create_feedback_tool(reject))
    async with Client(server) as client:
        result = await client.call_tool(
            "submit_feedback",
            {"task": "Search", "description": "Timed out"},
            raise_on_error=False,
        )
        assert result.is_error
