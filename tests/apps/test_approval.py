"""Tests for the Approval provider."""

from fastmcp import FastMCP
from fastmcp.apps.approval import Approval


class TestApprovalProvider:
    async def test_request_approval_returns_structured_content(self):
        server = FastMCP("test", providers=[Approval()])

        result = await server.call_tool(
            "request_approval",
            {"summary": "Delete 47 files"},
        )
        assert result.structured_content is not None

    async def test_request_approval_with_details(self):
        server = FastMCP("test", providers=[Approval()])

        result = await server.call_tool(
            "request_approval",
            {"summary": "Deploy to prod", "details": "Version 3.2.0"},
        )
        assert result.structured_content is not None

    async def test_tool_visible_to_model(self):
        server = FastMCP("test", providers=[Approval()])

        tools = await server.list_tools()
        tool_names = [t.name for t in tools]
        assert "request_approval" in tool_names

    async def test_custom_name(self):
        server = FastMCP("test", providers=[Approval(name="Gate")])

        tools = await server.list_tools()
        tool_names = [t.name for t in tools]
        assert "request_approval" in tool_names

    async def test_custom_button_text(self):
        server = FastMCP(
            "test",
            providers=[
                Approval(
                    approve_text="Ship it",
                    reject_text="Nope",
                    title="Deploy Gate",
                )
            ],
        )

        result = await server.call_tool(
            "request_approval",
            {"summary": "Deploy v3.2"},
        )
        assert result.structured_content is not None
